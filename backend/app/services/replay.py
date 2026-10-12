"""Immutable local snapshots and provider-free historical analysis replay."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from app.repositories.diagnostics import DiagnosticRepository
from app.schemas.diagnostics import DiagnosticParameters, DiagnosticResponse, ExecutionOutcome
from app.schemas.evaluation import TraceObservation
from app.schemas.traces import Contract, SpanRead, TraceRead
from app.services.diagnostics import analyze_trace
from app.services.diagnostics.signals import duration_ms, exception_signals, failed


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class SnapshotData(Contract):
    trace: TraceRead
    spans: list[SpanRead] = Field(max_length=100_000)
    truncated: bool = False


@dataclass(frozen=True)
class AnalysisSnapshot:
    """Bytes cannot retain mutable ORM/Pydantic references. Decode fresh for each use."""

    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("Snapshot payload must be immutable bytes")

    @classmethod
    def capture(
        cls, trace: TraceRead, spans: list[SpanRead], *, truncated: bool = False
    ) -> "AnalysisSnapshot":
        # Revalidate even model_copy/model_construct inputs; never silently repair malformed data.
        data = SnapshotData.model_validate_json(
            canonical(
                {
                    "trace": trace.model_dump(mode="json"),
                    "spans": [s.model_dump(mode="json") for s in spans],
                    "truncated": truncated,
                }
            )
        )
        data.spans.sort(key=lambda s: (s.start_time, s.span_id))
        return cls(canonical(data.model_dump(mode="json")))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


class ReplayComparison(Contract):
    compatible: bool
    same_snapshot: bool = False
    stable_findings: list[str] = Field(default_factory=list)
    changed_findings: list[str] = Field(default_factory=list)
    added_findings: list[str] = Field(default_factory=list)
    removed_findings: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reason: str


class ReplayResult(Contract):
    schema_version: Literal["5c1.1"] = "5c1.1"
    snapshot_digest: str | None = None
    parameters: DiagnosticParameters
    status: Literal["missing", "invalid", "incomplete", "complete"]
    issues: list[str] = Field(default_factory=list)
    observation: TraceObservation | None = None
    comparison: ReplayComparison | None = None
    stored_span_ids: list[str] = Field(default_factory=list)
    event_timestamps: dict[str, list[str]] = Field(default_factory=dict)
    exception_event_indexes: dict[str, list[int]] = Field(default_factory=dict)
    error_type_span_ids: list[str] = Field(default_factory=list)
    limitation: str = "Historical analysis replay cannot establish repair effectiveness."


def load_snapshot(
    repository: DiagnosticRepository,
    trace_id: str,
    parameters: DiagnosticParameters,
    project_id: UUID | None = None,
) -> AnalysisSnapshot | None:
    """Call inside a transaction; SQLDiagnosticRepository holds the shared trace lock."""
    stored = repository.snapshot(trace_id, project_id, parameters.max_spans)
    return AnalysisSnapshot.capture(*stored) if stored is not None else None


def _finding_map(analysis: DiagnosticResponse) -> dict[str, dict]:
    return {
        digest({"rule": f.rule_id, "spans": sorted(f.span_ids)}): f.model_dump(mode="json")
        for f in analysis.findings
    }


def compare_replay(original: ReplayResult, current: ReplayResult) -> ReplayComparison:
    old, new = original.observation, current.observation
    if (
        old is None
        or new is None
        or (
            old.analysis.trace_id,
            old.analysis.project_id,
            old.analysis.rules_version,
            old.analysis.schema_version,
        )
        != (
            new.analysis.trace_id,
            new.analysis.project_id,
            new.analysis.rules_version,
            new.analysis.schema_version,
        )
    ):
        return ReplayComparison(
            compatible=False, reason="Missing or incompatible original analysis"
        )
    before, after = _finding_map(old.analysis), _finding_map(new.analysis)
    shared = before.keys() & after.keys()

    # A rule no longer triggering is not missing evidence. Check actual stored references.
    def absent(e) -> bool:
        if e.span_id is not None and e.span_id not in current.stored_span_ids:
            return True
        if e.signal == "error.type_present":
            return e.span_id not in current.error_type_span_ids
        index = e.values.get("event_index")
        if type(index) is int:
            times = current.event_timestamps.get(e.span_id, [])
            return (
                index < 0
                or index >= len(times)
                or times[index] != e.values.get("timestamp")
                or (
                    e.signal == "exception_event"
                    and index not in current.exception_event_indexes.get(e.span_id, [])
                )
            )
        return False

    missing = sorted(
        {
            digest(e.model_dump(mode="json"))
            for f in old.analysis.findings
            for e in f.evidence
            if absent(e)
        }
    )
    complete = old.complete and new.complete
    return ReplayComparison(
        compatible=True,
        same_snapshot=original.snapshot_digest == current.snapshot_digest,
        stable_findings=sorted(k for k in shared if before[k] == after[k]),
        changed_findings=sorted(k for k in shared if before[k] != after[k]),
        added_findings=sorted(after.keys() - before.keys()),
        removed_findings=sorted(before.keys() - after.keys()),
        missing_evidence=missing,
        reason=(
            "Comparable recorded findings; disappearance is not proof of repair"
            if complete
            else "Incomplete evidence; comparison is descriptive only"
        ),
    )


def replay(
    snapshot: AnalysisSnapshot | None,
    parameters: DiagnosticParameters,
    *,
    original: ReplayResult | None = None,
) -> ReplayResult:
    result = ReplayResult(parameters=parameters.model_copy(deep=True), status="missing")
    if snapshot is None:
        result.issues = ["trace_not_found"]
    else:
        result.snapshot_digest = snapshot.sha256
        try:
            data = SnapshotData.model_validate_json(snapshot.payload)
        except (ValidationError, ValueError):
            result.status, result.issues = "invalid", ["invalid_snapshot_contract"]
        else:
            if len(data.spans) > parameters.max_spans:
                result.status, result.issues = "invalid", ["diagnostic_span_limit"]
            else:
                result.stored_span_ids = sorted(s.span_id for s in data.spans)
                result.event_timestamps = {
                    s.span_id: [e.timestamp.isoformat() for e in s.events] for s in data.spans
                }
                result.exception_event_indexes = {
                    s.span_id: [
                        e.values["event_index"]
                        for e in exception_signals(s)
                        if e.signal == "exception_event"
                    ]
                    for s in data.spans
                }
                result.error_type_span_ids = sorted(
                    s.span_id
                    for s in data.spans
                    if any(e.signal == "error.type_present" for e in exception_signals(s))
                )
                analysis = analyze_trace(data.trace, data.spans, result.parameters)
                incomplete = data.truncated or any(
                    f.category == "INCOMPLETE_OR_INVALID_TRACE" for f in analysis.findings
                )
                if data.truncated:
                    # An offline partial snapshot must not masquerade as a complete stored read.
                    analysis.coverage = "partial_snapshot"
                    analysis.outcome.classification = ExecutionOutcome.UNKNOWN
                    analysis.outcome.explanation = "Snapshot explicitly truncated; outcome unknown"
                    analysis.outcome.evidence = []
                result.status = "incomplete" if incomplete else "complete"
                result.issues = ["truncated_snapshot"] if data.truncated else []
                llms = [s for s in data.spans if s.operation_type == "llm"]

                def tokens(key: str) -> list[int]:
                    return [
                        s.attributes[key]
                        for s in llms
                        if type(s.attributes.get(key)) is int and s.attributes[key] >= 0
                    ]

                inputs, outputs = (
                    tokens("gen_ai.usage.input_tokens"),
                    tokens("gen_ai.usage.output_tokens"),
                )
                result.observation = TraceObservation(
                    snapshot_digest=snapshot.sha256,
                    analysis=analysis,
                    complete=not incomplete,
                    wall_duration_ms=duration_ms(data.trace),
                    tool_errors=sum(failed(s) for s in data.spans if s.operation_type == "tool"),
                    llm_errors=sum(failed(s) for s in llms),
                    llm_span_count=len(llms),
                    input_tokens=sum(inputs) if inputs else None,
                    output_tokens=sum(outputs) if outputs else None,
                    input_token_coverage=len(inputs),
                    output_token_coverage=len(outputs),
                )
    if original is not None:
        result.comparison = compare_replay(original, result)
    return result
