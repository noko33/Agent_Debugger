"""Privacy by omission: do not send arbitrary labels, attributes or error messages."""

from dataclasses import dataclass

from app.core.ai_config import AISettings
from app.schemas.ai_diagnosis import ContextFact, ContextReport
from app.schemas.diagnostics import DiagnosticResponse
from app.schemas.traces import SpanRead, TraceRead
from app.services.ai_diagnosis.prompt import encode, input_units

KNOWN_ERRORS = frozenset(
    {
        "TimeoutError",
        "ConnectionError",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
        "PermissionError",
        "FileNotFoundError",
        "CancelledError",
        "HTTPStatusError",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectError",
    }
)


@dataclass
class BuiltContext:
    payload: dict
    report: ContextReport
    facts: list[ContextFact]
    fits: bool


def span_fact(span: SpanRead) -> ContextFact:
    errors = [span.attributes.get("error.type")]
    errors.extend(event.attributes.get("exception.type") for event in span.events)
    types = sorted({value for value in errors if isinstance(value, str) and value in KNOWN_ERRORS})
    values = {
        "operation_type": span.operation_type.value,
        "status_code": span.status_code.value,
        "parent_span_id": span.parent_span_id,
        "start_time": span.start_time.isoformat(),
        "end_time": span.end_time.isoformat() if span.end_time else None,
        "duration_ms": span.duration_ms,
        "known_exception_types": types,
        "exception_recorded": any(event.name == "exception" for event in span.events)
        or any(isinstance(value, str) and bool(value) for value in errors),
        "unknown_exception_type_omitted": any(
            isinstance(value, str) and bool(value) and value not in KNOWN_ERRORS for value in errors
        ),
        "events": [
            {
                "event_index": index,
                "timestamp": event.timestamp.isoformat(),
                "signal": "recovery" if event.name == "recovery" else "exception",
            }
            for index, event in enumerate(span.events)
            if event.name in ("exception", "recovery")
        ][:10],
        "event_count": len(span.events),
    }
    for key in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"):
        value = span.attributes.get(key)
        if type(value) is int and 0 <= value <= 2**53 - 1:
            values[key] = value
    return ContextFact(
        evidence_id=f"s:{span.span_id}",
        source="span_record",
        span_ids=[span.span_id],
        values=values,
    )


def build_context(
    trace: TraceRead, spans: list[SpanRead], diagnostics: DiagnosticResponse, settings: AISettings
) -> BuiltContext:
    by_id = {span.span_id: span for span in spans}
    # Root/ancestor records precede relevant children in the compact representation;
    # IDs break presentation ties only and never imply original execution order.
    priority: list[str] = []
    visited: set[str] = set()

    def include_chain(identifier: str) -> None:
        chain = []
        while identifier in by_id and identifier not in visited:
            visited.add(identifier)
            chain.append(identifier)
            identifier = by_id[identifier].parent_span_id
        priority.extend(reversed(chain))

    for span in sorted(spans, key=lambda s: (s.start_time, s.span_id)):
        if span.parent_span_id is None:
            include_chain(span.span_id)
    for finding in diagnostics.findings:
        for identifier in finding.span_ids:
            include_chain(identifier)
    for span in sorted(spans, key=lambda s: (s.start_time, s.span_id)):
        include_chain(span.span_id)

    missing = [
        "Prompts, completions, raw arguments/results, arbitrary labels/attributes "
        "and error messages omitted by policy.",
        "Semantic task outcome and underlying external-service state are not established.",
        "Equal timestamps do not establish order; timestamps alone do not prove causality.",
    ]
    if any(span.end_time is None for span in spans) or trace.end_time is None:
        missing.append("Some recorded intervals are incomplete.")
    if any(span.parent_span_id and span.parent_span_id not in by_id for span in spans):
        missing.append("Some recorded parents are missing from stored data.")
    if diagnostics.outcome.classification == "unknown":
        missing.append("Overall execution outcome cannot be determined from recorded evidence.")
    payload = {
        "policy_version": "5a.1",
        "trace_id": trace.trace_id,
        "trace": {
            "status_code": trace.status_code.value,
            "start_time": trace.start_time.isoformat(),
            "end_time": trace.end_time.isoformat() if trace.end_time else None,
            "duration_ms": trace.duration_ms,
        },
        "recorded_outcome": diagnostics.outcome.classification.value,
        "ordering": "Use recorded times/parent IDs; array order and equal start times "
        "are not causal order.",
        "missing_evidence": missing,
        "stored_span_count": len(spans),
        "stored_finding_count": len(diagnostics.findings),
        "facts": [],
        "omitted_span_count": len(spans),
        "omitted_finding_count": len(diagnostics.findings),
    }
    facts: list[ContextFact] = []
    included: list[str] = []
    finding_count = 0

    def fits() -> bool:
        return (
            len(encode(payload).encode()) <= settings.max_context_bytes
            and input_units(payload) <= settings.max_input_tokens
        )

    def add(fact: ContextFact) -> bool:
        payload["facts"].append(fact.model_dump(mode="json"))
        if not fits():
            payload["facts"].pop()
            return False
        facts.append(fact)
        return True

    add(
        ContextFact(
            evidence_id="outcome",
            source="recorded_outcome",
            span_ids=[],
            values={
                "classification": diagnostics.outcome.classification.value,
                "explicit_recovery_recorded": any(
                    e.signal == "explicit_recovery_event" for e in diagnostics.outcome.evidence
                ),
            },
        )
    )
    # Reserve space for at least the first relevant finding before filling span facts.
    # Finding evidence values can contain tool labels, so only typed categories/counts/
    # thresholds are copied; raw deterministic evidence is never blindly forwarded.
    candidates = diagnostics.findings[: settings.max_context_findings]
    for index, finding in enumerate(candidates):
        identifiers = [sid for sid in finding.span_ids if sid in by_id]
        fact = ContextFact(
            evidence_id=f"f:{index}",
            source="rule_finding",
            span_ids=identifiers,
            values={
                "category": finding.category.value,
                "nature": finding.nature.value,
                "severity": finding.severity.value,
                "parameters": finding.parameters,
                "related_span_count": len(finding.span_ids),
            },
        )
        if add(fact):
            finding_count += 1
    for identifier in priority[: settings.max_context_spans]:
        if add(span_fact(by_id[identifier])):
            included.append(identifier)
    # Do not allow a model to cite spans that were referenced by a rule but whose actual
    # records were omitted. Truncation retains counts and explicit missing-evidence flags.
    included_set = set(included)
    for fact in facts:
        if fact.source == "rule_finding":
            fact.span_ids = [
                identifier for identifier in fact.span_ids if identifier in included_set
            ]
    payload["facts"] = [fact.model_dump(mode="json") for fact in facts]
    payload["omitted_span_count"] = len(spans) - len(included)
    payload["omitted_finding_count"] = len(diagnostics.findings) - finding_count
    report = ContextReport(
        stored_span_count=len(spans),
        included_span_ids=included,
        omitted_span_count=payload["omitted_span_count"],
        omitted_finding_count=payload["omitted_finding_count"],
        truncated=len(included) < len(spans) or finding_count < len(diagnostics.findings),
        serialized_context_bytes=len(encode(payload).encode()),
        input_budget_units=input_units(payload),
        missing_evidence=missing,
    )
    return BuiltContext(payload, report, facts, fits())
