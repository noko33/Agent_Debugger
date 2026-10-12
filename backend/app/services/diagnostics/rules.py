import re
from collections import defaultdict

from app.schemas.diagnostics import (
    Category,
    DiagnosticParameters,
    Evidence,
    Finding,
    StrEnumNature,
    StrEnumSeverity,
)
from app.schemas.traces import OperationType, SpanRead, TraceRead
from app.services.diagnostics.signals import (
    duration_ms,
    failed,
    failure_evidence,
    operation_identity,
)


def finding(
    trace: TraceRead,
    category: Category,
    spans: list[SpanRead],
    summary: str,
    explanation: str,
    evidence: list[Evidence],
    *,
    nature: StrEnumNature = StrEnumNature.OBSERVED_FAILURE,
    parameters: dict | None = None,
) -> Finding:
    return Finding(
        rule_id=f"{category.value.lower()}.v1",
        category=category,
        severity=StrEnumSeverity.ERROR
        if nature == StrEnumNature.OBSERVED_FAILURE
        else StrEnumSeverity.WARNING,
        nature=nature,
        summary=summary,
        explanation=explanation,
        trace_id=trace.trace_id,
        span_ids=[span.span_id for span in spans],
        evidence=evidence,
        parameters=parameters or {},
    )


def operation_findings(
    trace: TraceRead, spans: list[SpanRead], parameters: DiagnosticParameters
) -> list[Finding]:
    findings: list[Finding] = []
    tools: dict[tuple[str, str], list[SpanRead]] = defaultdict(list)
    for span in spans:
        if failed(span):
            if span.operation_type in (OperationType.TOOL, OperationType.LLM):
                category = (
                    Category.TOOL_EXECUTION_ERROR
                    if span.operation_type == OperationType.TOOL
                    else Category.LLM_EXECUTION_ERROR
                )
                findings.append(
                    finding(
                        trace,
                        category,
                        [span],
                        "Operation reports an error",
                        "Recorded error status, or exception evidence with Unset status, "
                        "identifies an operation failure. "
                        "This does not determine the Agent outcome.",
                        failure_evidence(span),
                    )
                )
            if span.operation_type == OperationType.TOOL:
                tools[operation_identity(span)].append(span)
        duration = duration_ms(span)
        if duration is not None and duration > parameters.slow_operation_ms:
            findings.append(
                finding(
                    trace,
                    Category.SLOW_OPERATION,
                    [span],
                    "Operation exceeds duration threshold",
                    "Recorded inclusive duration exceeds the configured threshold. This is a "
                    "performance warning, not proof of a correctness failure or root cause.",
                    [
                        Evidence(
                            span_id=span.span_id,
                            signal="recorded_duration",
                            values={
                                "start_time": span.start_time.isoformat(),
                                "end_time": span.end_time.isoformat(),
                                "duration_ms": duration,
                            },
                        )
                    ],
                    nature=StrEnumNature.HEURISTIC_WARNING,
                    parameters={"slow_operation_ms": parameters.slow_operation_ms},
                )
            )
    for identity, calls in sorted(tools.items()):
        if len(calls) >= parameters.repeated_tool_failure_threshold:
            findings.append(
                finding(
                    trace,
                    Category.REPEATED_TOOL_FAILURE,
                    calls,
                    "Repeated tool failures observed",
                    "Multiple failed tool operations share the same recorded tool label. Names "
                    "can collide; this does not prove identical arguments, retries or a loop.",
                    [
                        Evidence(
                            signal="tool_label_group",
                            values={
                                "identity_source": identity[0],
                                "identity": identity[1],
                                "failed_call_count": len(calls),
                            },
                        ),
                        *[item for call in calls for item in failure_evidence(call)],
                    ],
                    parameters={
                        "repeated_tool_failure_threshold": (
                            parameters.repeated_tool_failure_threshold
                        )
                    },
                )
            )
    return findings


def retry_findings(
    trace: TraceRead, spans: list[SpanRead], parameters: DiagnosticParameters
) -> list[Finding]:
    groups: dict[tuple, list[SpanRead]] = defaultdict(list)
    invalid_groups: set[tuple] = set()
    for span in spans:
        if span.operation_type not in (OperationType.TOOL, OperationType.LLM):
            continue
        attributes = span.attributes
        fingerprint = attributes.get("agent_debugger.operation.input_fingerprint")
        group = attributes.get("agent_debugger.retry.group_id")
        attempt = attributes.get("agent_debugger.retry.attempt")
        base_group = (span.operation_type, span.parent_span_id, group)
        if not isinstance(group, str):
            continue
        if not (
            isinstance(fingerprint, str)
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            and isinstance(group, str)
            and group.strip()
            and len(group) <= 200
            and type(attempt) is int
            and 1 <= attempt <= 1_000_000
        ):
            invalid_groups.add(base_group)
            continue
        identity = operation_identity(span)
        labels = tuple(
            str(attributes.get(key, "")) for key in ("gen_ai.provider.name", "gen_ai.request.model")
        )
        groups[
            (span.operation_type, identity, span.parent_span_id, fingerprint, group, labels)
        ].append(span)
    findings = []
    for _key, calls in sorted(groups.items(), key=lambda item: repr(item[0])):
        if (_key[0], _key[2], _key[4]) in invalid_groups:
            continue
        calls.sort(key=lambda span: span.attributes["agent_debugger.retry.attempt"])
        attempts = [span.attributes["agent_debugger.retry.attempt"] for span in calls]
        if len(calls) < parameters.retry_loop_threshold or not all(failed(call) for call in calls):
            continue
        if any(b != a + 1 for a, b in zip(attempts, attempts[1:], strict=False)):
            continue
        if any(duration_ms(call) is None for call in calls) or any(
            previous.end_time > current.start_time
            for previous, current in zip(calls, calls[1:], strict=False)
        ):
            continue
        findings.append(
            finding(
                trace,
                Category.POSSIBLE_RETRY_LOOP,
                calls,
                "Repeated failed retry attempts",
                "Consecutive explicit attempt numbers, one retry group, matching caller-provided "
                "input fingerprints and non-overlapping timings suggest a possible retry loop. "
                "A finite intentional retry policy can produce this pattern; no unbounded loop "
                "or identical raw arguments are proven. "
                "Fingerprints are not independently verified.",
                [
                    Evidence(
                        span_id=call.span_id,
                        signal="retry_attempt",
                        values={
                            "attempt": call.attributes["agent_debugger.retry.attempt"],
                            "start_time": call.start_time.isoformat(),
                            "end_time": call.end_time.isoformat(),
                        },
                    )
                    for call in calls
                ]
                + [
                    Evidence(
                        signal="retry_group_match",
                        values={
                            "same_parent": True,
                            "same_identity": True,
                            "same_input_fingerprint": True,
                            "same_retry_group": True,
                            "all_attempts_failed": True,
                        },
                    )
                ],
                nature=StrEnumNature.HEURISTIC_WARNING,
                parameters={"retry_loop_threshold": parameters.retry_loop_threshold},
            )
        )
    return findings
