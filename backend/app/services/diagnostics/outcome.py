from app.schemas.diagnostics import Evidence, ExecutionOutcome, Outcome
from app.schemas.traces import SpanRead, StatusCode, TraceRead
from app.services.diagnostics.signals import failed


def classify_outcome(trace: TraceRead, spans: list[SpanRead], structural: bool) -> Outcome:
    roots = [s for s in spans if s.operation_type == "agent" and s.parent_span_id is None]
    root = roots[0] if len(roots) == 1 else None
    evidence = [
        Evidence(
            signal="trace_status",
            values={
                "status_code": trace.status_code,
                "end_recorded": trace.end_time is not None,
            },
        )
    ]
    if root:
        evidence.append(
            Evidence(
                span_id=root.span_id,
                signal="agent_root_status",
                values={
                    "status_code": root.status_code,
                    "end_recorded": root.end_time is not None,
                },
            )
        )

    def result(classification: ExecutionOutcome, explanation: str) -> Outcome:
        return Outcome(classification=classification, explanation=explanation, evidence=evidence)

    # Incomplete or inconsistent data prevents definitive outcome classification.
    if structural or len(roots) > 1:
        return result(
            ExecutionOutcome.UNKNOWN,
            "Incomplete/inconsistent data or ambiguous Agent "
            "roots prevents an overall classification. Operation findings remain valid.",
        )
    if trace.status_code == StatusCode.ERROR or (root and root.status_code == StatusCode.ERROR):
        return result(
            ExecutionOutcome.UNRECOVERED,
            "The completed trace or Agent root explicitly "
            "reports Error. This describes recorded execution status, not its root cause.",
        )
    failures = [span for span in spans if failed(span)]
    successful = trace.status_code == StatusCode.OK or (root and root.status_code == StatusCode.OK)
    if not failures and successful:
        return result(
            ExecutionOutcome.RECORDED_SUCCESS,
            "The completed execution explicitly reports "
            "OK with no recorded failed operations. Semantic task correctness is unknown.",
        )
    if failures and successful and root and root.status_code == StatusCode.OK:
        recovery = next(
            (
                event
                for event in root.events
                if event.name == "recovery"
                and root.start_time <= event.timestamp <= root.end_time
                and all(
                    span.end_time is not None and span.end_time <= event.timestamp
                    for span in failures
                )
            ),
            None,
        )
        if recovery is not None:
            evidence.append(
                Evidence(
                    span_id=root.span_id,
                    signal="explicit_recovery_event",
                    values={
                        "timestamp": recovery.timestamp.isoformat(),
                        "failed_span_mapping_used": False,
                        "preceding_failed_span_ids": [span.span_id for span in failures],
                    },
                )
            )
            return result(
                ExecutionOutcome.RECOVERED,
                "The completed Agent root reports OK and an "
                "explicit recovery event at/after all recorded operation failures. Recovery "
                "is self-reported; the event does not identify which failures it resolved "
                "or prove semantic task success.",
            )
    return result(
        ExecutionOutcome.UNKNOWN,
        "Recorded operation errors and later/OK spans alone "
        "do not prove recovery or overall failure. Explicit outcome/recovery evidence "
        "is insufficient; semantic task correctness is not assessed.",
    )
