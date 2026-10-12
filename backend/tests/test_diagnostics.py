import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.diagnostics import Category, DiagnosticParameters
from app.services.diagnostics import analyze_trace
from tests.diagnostic_fixtures import (
    BASE,
    evaluate_cases,
    labeled_cases,
    retry_attributes,
    root,
    span,
    trace,
)


@pytest.mark.parametrize("case", labeled_cases(), ids=lambda case: case.name)
def test_labeled_diagnostics(case) -> None:
    result = analyze_trace(case.trace, case.spans)
    assert {item.category for item in result.findings} == case.expected
    assert result.outcome.classification == case.outcome
    assert result.model_dump(mode="json") == analyze_trace(
        case.trace, list(reversed(case.spans))
    ).model_dump(mode="json")
    assert all(
        item.rule_id.endswith(".v1") and item.explanation and item.evidence
        for item in result.findings
    )
    assert all(item.trace_id == case.trace.trace_id for item in result.findings)


def test_configurable_thresholds_and_strict_duration_boundary() -> None:
    spans = [root(), span(2, status="error"), span(3, status="error")]
    result = analyze_trace(
        trace(),
        spans,
        DiagnosticParameters(repeated_tool_failure_threshold=3, slow_operation_ms=100),
    )
    assert Category.REPEATED_TOOL_FAILURE not in {item.category for item in result.findings}
    slow = [item for item in result.findings if item.category == Category.SLOW_OPERATION]
    assert len(slow) == 1 and slow[0].span_ids == [root().span_id]
    assert slow[0].parameters == {"slow_operation_ms": 100}
    assert slow[0].nature == "heuristic_warning"


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_thresholds_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        DiagnosticParameters(slow_operation_ms=value)


def test_nested_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIAGNOSTICS__REPEATED_TOOL_FAILURE_THRESHOLD", "4")
    monkeypatch.setenv("DIAGNOSTICS__SLOW_OPERATION_MS", "2500")
    parameters = Settings(_env_file=None).diagnostics
    assert parameters.repeated_tool_failure_threshold == 4
    assert parameters.slow_operation_ms == 2500


def test_data_quality_cycles_reversed_intervals_and_outside_bounds() -> None:
    spans = [
        root(),
        span(2, parent=3),
        span(3, parent=2),
        span(4).model_copy(update={"end_time": BASE}),
        span(5, start=900, end=1100),
    ]
    result = analyze_trace(trace(), spans)
    signals = {item.signal for f in result.findings for item in f.evidence}
    assert {"parent_cycle", "span_times_reversed", "span_outside_trace_bounds"} <= signals
    assert result.outcome.classification == "unknown"


def test_sensitive_attributes_events_and_fingerprints_not_echoed() -> None:
    secret = "private credential prompt completion exception message"
    spans = [
        root(),
        span(
            2,
            status="error",
            attributes={"prompt": secret, "error.type": secret},
            status_message=secret,
            events=[
                {
                    "name": "exception",
                    "timestamp": BASE,
                    "attributes": {"exception.message": secret},
                }
            ],
        ),
    ]
    result = analyze_trace(trace(attributes={"credential": secret}), spans)
    assert secret not in result.model_dump_json()
    assert "exception_event" in result.model_dump_json()
    retries = [
        root(),
        *[
            span(
                i + 2,
                status="error",
                start=100 * i,
                end=100 * (i + 1),
                attributes=retry_attributes(i + 1),
            )
            for i in range(3)
        ],
    ]
    assert "a" * 64 not in analyze_trace(trace(), retries).model_dump_json()
    assert "catalog-retry" not in analyze_trace(trace(), retries).model_dump_json()


def test_recovery_event_must_follow_failure_and_be_on_agent_root() -> None:
    event = {"name": "recovery", "timestamp": BASE + timedelta(milliseconds=50), "attributes": {}}
    result = analyze_trace(trace(), [root(events=[event]), span(2, status="error")])
    assert result.outcome.classification == "unknown"
    event["timestamp"] = BASE + timedelta(milliseconds=500)
    result = analyze_trace(trace(), [root(), span(2, status="error"), span(3, events=[event])])
    assert result.outcome.classification == "unknown"


def test_microsecond_duration_and_runtime_error_count() -> None:
    operation = span(2, operation="runtime", status="error").model_copy(
        update={"start_time": BASE, "end_time": BASE + timedelta(microseconds=2)}
    )
    result = analyze_trace(
        trace(), [root(), operation], DiagnosticParameters(slow_operation_ms=0.001)
    )
    slow = next(f for f in result.findings if f.span_ids == [operation.span_id])
    assert slow.evidence[0].values["duration_ms"] == 0.002
    assert result.operation_failure_count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"agent_debugger.retry.attempt": True},
        {"agent_debugger.retry.attempt": 1.5},
        {"agent_debugger.retry.group_id": None},
        {"agent_debugger.operation.input_fingerprint": "raw arguments"},
    ],
)
def test_invalid_retry_metadata_cannot_trigger_loop(change: dict) -> None:
    calls = [
        span(
            i + 2,
            status="error",
            start=i * 100,
            end=(i + 1) * 100,
            attributes=retry_attributes(i + 1, **change),
        )
        for i in range(3)
    ]
    assert Category.POSSIBLE_RETRY_LOOP not in {
        f.category for f in analyze_trace(trace(), [root(), *calls]).findings
    }


def test_evaluation_report(capsys: pytest.CaptureFixture) -> None:
    report = evaluate_cases()
    assert report["case_count"] == 22
    assert all(score["fp"] == score["fn"] == 0 for score in report["categories"].values())
    print(json.dumps(report, sort_keys=True))
    assert "POSSIBLE_RETRY_LOOP" in capsys.readouterr().out


def test_diagnostic_id_validation(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/traces/invalid/diagnostics").status_code == 422


def test_recovery_must_follow_every_recorded_failure() -> None:
    event = {"name": "recovery", "timestamp": BASE + timedelta(milliseconds=500), "attributes": {}}
    result = analyze_trace(
        trace(),
        [
            root(events=[event]),
            span(2, status="error"),
            span(3, "late failure", status="error", start=600, end=700),
        ],
    )
    assert result.outcome.classification == "unknown"


@pytest.mark.parametrize(
    "pattern",
    [
        "success",
        "duplicate",
        "gap",
        "different_parent",
        "partial_metadata",
        "different_model",
        "threshold",
    ],
)
def test_retry_warning_requires_full_compatible_evidence(pattern: str) -> None:
    calls = [
        span(
            i + 2,
            status="error",
            start=i * 100,
            end=(i + 1) * 100,
            attributes=retry_attributes(i + 1),
        )
        for i in range(3)
    ]
    parameters = DiagnosticParameters()
    if pattern == "success":
        calls[2] = calls[2].model_copy(update={"status_code": "ok"})
    elif pattern == "duplicate":
        calls[2].attributes["agent_debugger.retry.attempt"] = 2
    elif pattern == "gap":
        calls[2].attributes["agent_debugger.retry.attempt"] = 4
    elif pattern == "different_parent":
        calls[2] = calls[2].model_copy(update={"parent_span_id": span(99).span_id})
    elif pattern == "different_model":
        calls[2].attributes["gen_ai.request.model"] = "different"
    elif pattern == "partial_metadata":
        calls.append(
            span(5, status="ok", attributes={"agent_debugger.retry.group_id": "catalog-retry"})
        )
    else:
        parameters.retry_loop_threshold = 4
    result = analyze_trace(trace(), [root(), *calls], parameters)
    assert Category.POSSIBLE_RETRY_LOOP not in {f.category for f in result.findings}
