"""Small labeled evaluation cases, not evidence of general-world diagnostic accuracy."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.schemas.diagnostics import Category, ExecutionOutcome
from app.schemas.traces import SpanRead, StatusCode, TraceRead
from tests.payloads import span_payload, trace_payload

BASE = datetime(2026, 10, 12, tzinfo=UTC)
PROJECT = "11111111-1111-4111-8111-111111111111"


def span(
    identifier: int,
    name: str = "lookup",
    operation: str = "tool",
    status: str = "ok",
    start: int = 100,
    end: int | None = 200,
    parent: int | None = 1,
    **extra: object,
) -> SpanRead:
    return SpanRead.model_validate(
        span_payload(
            span_id=f"{identifier:016x}",
            name=name,
            operation_type=operation,
            status_code=status,
            start_time=BASE + timedelta(milliseconds=start),
            end_time=BASE + timedelta(milliseconds=end) if end is not None else None,
            parent_span_id=f"{parent:016x}" if parent else None,
            **extra,
        )
    )


def trace(**extra: object) -> TraceRead:
    return TraceRead.model_validate(
        trace_payload(
            PROJECT,
            **{"start_time": BASE, "end_time": BASE + timedelta(seconds=1), **extra},
        )
    )


def root(status: str = "ok", **extra: object) -> SpanRead:
    return span(1, "Agent", "agent", status, 0, 1000, None, **extra)


def retry_attributes(attempt: int, **extra: object) -> dict:
    return {
        "gen_ai.tool.name": "lookup",
        "agent_debugger.retry.group_id": "catalog-retry",
        "agent_debugger.retry.attempt": attempt,
        "agent_debugger.operation.input_fingerprint": "a" * 64,
        **extra,
    }


@dataclass
class DiagnosticCase:
    name: str
    trace: TraceRead
    spans: list[SpanRead]
    expected: set[Category]
    outcome: ExecutionOutcome


def labeled_cases() -> list[DiagnosticCase]:
    C = Category
    OutcomeLabel = ExecutionOutcome
    failures = [
        span(
            i + 2,
            status="error",
            start=100 + i * 100,
            end=200 + i * 100,
            attributes=retry_attributes(i + 1),
        )
        for i in range(3)
    ]
    repeated = {C.TOOL_EXECUTION_ERROR, C.REPEATED_TOOL_FAILURE}
    recovery = {
        "name": "recovery",
        "timestamp": BASE + timedelta(milliseconds=500),
        "attributes": {"strategy": "fallback"},
    }
    return [
        DiagnosticCase("success", trace(), [root(), span(2)], set(), OutcomeLabel.RECORDED_SUCCESS),
        DiagnosticCase(
            "tool_error",
            trace(),
            [root(), span(2, status="error")],
            {C.TOOL_EXECUTION_ERROR},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "llm_error",
            trace(),
            [root(), span(2, operation="llm", status="error")],
            {C.LLM_EXECUTION_ERROR},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "repeated_failures_without_retry_metadata",
            trace(),
            [root(), *[s.model_copy(update={"attributes": {}}) for s in failures]],
            repeated,
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "legitimate_repeated_success",
            trace(),
            [root(), *[s.model_copy(update={"status_code": StatusCode.OK}) for s in failures]],
            set(),
            OutcomeLabel.RECORDED_SUCCESS,
        ),
        DiagnosticCase(
            "suspicious_explicit_retries",
            trace(),
            [root(), *failures],
            repeated | {C.POSSIBLE_RETRY_LOOP},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "slow_operation",
            trace(end_time=BASE + timedelta(seconds=3)),
            [root().model_copy(update={"end_time": BASE + timedelta(seconds=3)})],
            {C.SLOW_OPERATION},
            OutcomeLabel.RECORDED_SUCCESS,
        ),
        DiagnosticCase(
            "missing_parent",
            trace(),
            [root(), span(2, parent=99)],
            {C.INCOMPLETE_OR_INVALID_TRACE},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "incomplete_span",
            trace(),
            [root(), span(2, end=None, status="unset")],
            {C.INCOMPLETE_OR_INVALID_TRACE},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "incomplete_trace",
            trace(end_time=None),
            [root()],
            {C.INCOMPLETE_OR_INVALID_TRACE},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "no_spans", trace(), [], {C.INCOMPLETE_OR_INVALID_TRACE}, OutcomeLabel.UNKNOWN
        ),
        DiagnosticCase(
            "explicit_recovery",
            trace(),
            [
                root(events=[recovery]),
                span(2, status="error"),
                span(3, "fallback", start=500, end=600),
            ],
            {C.TOOL_EXECUTION_ERROR},
            OutcomeLabel.RECOVERED,
        ),
        DiagnosticCase(
            "later_fallback_is_not_recovery_proof",
            trace(),
            [root(), span(2, status="error"), span(3, "fallback", start=500, end=600)],
            {C.TOOL_EXECUTION_ERROR},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "failed_root",
            trace(status_code="error"),
            [root("error")],
            set(),
            OutcomeLabel.UNRECOVERED,
        ),
        DiagnosticCase(
            "concurrent_siblings",
            trace(),
            [root(), span(2, start=100, end=700), span(3, start=300, end=800)],
            set(),
            OutcomeLabel.RECORDED_SUCCESS,
        ),
        DiagnosticCase(
            "missing_fingerprint",
            trace(),
            [
                root(),
                *[
                    s.model_copy(
                        update={
                            "attributes": retry_attributes(
                                i + 1, **{"agent_debugger.operation.input_fingerprint": None}
                            )
                        }
                    )
                    for i, s in enumerate(failures)
                ],
            ],
            repeated,
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "different_fingerprints",
            trace(),
            [
                root(),
                *[
                    s.model_copy(
                        update={
                            "attributes": retry_attributes(
                                i + 1, **{"agent_debugger.operation.input_fingerprint": str(i) * 64}
                            )
                        }
                    )
                    for i, s in enumerate(failures)
                ],
            ],
            repeated,
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "concurrent_failed_attempts",
            trace(),
            [
                root(),
                *[
                    s.model_copy(
                        update={
                            "start_time": BASE + timedelta(milliseconds=100),
                            "end_time": BASE + timedelta(milliseconds=700),
                        }
                    )
                    for s in failures
                ],
            ],
            repeated,
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "handled_exception_final_ok",
            trace(),
            [
                root(),
                span(
                    2,
                    events=[
                        {
                            "name": "exception",
                            "timestamp": BASE + timedelta(milliseconds=150),
                            "attributes": {"exception.type": "HandledError"},
                        }
                    ],
                ),
            ],
            set(),
            OutcomeLabel.RECORDED_SUCCESS,
        ),
        DiagnosticCase(
            "unset_with_exception",
            trace(status_code="unset"),
            [
                root("unset"),
                span(
                    2,
                    status="unset",
                    events=[
                        {
                            "name": "exception",
                            "timestamp": BASE + timedelta(milliseconds=150),
                            "attributes": {"exception.type": "Error"},
                        }
                    ],
                ),
            ],
            {C.TOOL_EXECUTION_ERROR},
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "unknown_root_status",
            trace(status_code="unset"),
            [root("unset")],
            set(),
            OutcomeLabel.UNKNOWN,
        ),
        DiagnosticCase(
            "conflicting_status",
            trace(),
            [root("error")],
            {C.INCOMPLETE_OR_INVALID_TRACE},
            OutcomeLabel.UNKNOWN,
        ),
    ]


def evaluate_cases() -> dict:
    from app.services.diagnostics import analyze_trace

    cases = labeled_cases()
    scores = {}
    for category in Category:
        tp = fp = fn = 0
        for case in cases:
            actual = {item.category for item in analyze_trace(case.trace, case.spans).findings}
            tp += category in actual and category in case.expected
            fp += category in actual and category not in case.expected
            fn += category not in actual and category in case.expected
        scores[category.value] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }
    return {"case_count": len(cases), "categories": scores}


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate_cases(), indent=2, sort_keys=True))
