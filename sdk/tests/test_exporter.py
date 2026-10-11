import json
from uuid import uuid4

import httpx
import pytest

from agent_debugger import AgentDebugger, APIExporter
from agent_debugger.exporter import timestamp


def run_trace(handler, *, max_retries=1, redact=None, fail_agent=False):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        exporter = APIExporter(
            "http://test", uuid4(), client=client, max_retries=max_retries, redact=redact
        )
        with AgentDebugger(exporter.project_id, exporter=exporter) as debugger:
            with debugger.trace("Task") as root:
                root.set_attribute("secret", "do-not-log-this")
                if fail_agent:
                    raise ValueError("agent failure")
            return exporter.last_export_succeeded


@pytest.mark.parametrize("status", [400, 401, 404, 409, 422, 429, 500, 503])
def test_http_errors_are_nonfatal_and_bounded(status, caplog):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"private": "do-not-log-this"})

    assert run_trace(handle) is False
    assert len(calls) <= 3
    assert "do-not-log-this" not in caplog.text


def test_timeout_does_not_replace_agent_exception(caplog):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private failure do-not-log-this", request=request)

    with pytest.raises(ValueError, match="agent failure"):
        run_trace(handle, fail_agent=True)
    assert len(calls) == 2
    assert "do-not-log-this" not in caplog.text


def test_retry_after_trace_commit_and_batch_response_loss():
    stored = None
    calls = []
    batches = []

    def handle(request):
        nonlocal stored
        calls.append(request.url.path)
        if request.method == "GET":
            # FastAPI formats UTC with Z whereas the SDK uses +00:00.
            response = {
                **stored,
                "start_time": stored["start_time"].replace("+00:00", "Z"),
                "end_time": stored["end_time"].replace("+00:00", "Z"),
                "duration_ms": 0.0,
            }
            return httpx.Response(200, json=response)
        if request.url.path.endswith("traces"):
            if stored is None:
                stored = json.loads(request.content)
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(409)
        batches.append(request.content)
        if len(batches) == 1:
            raise httpx.ReadTimeout("batch response lost", request=request)
        return httpx.Response(200, json={"received": 1, "inserted": 0, "duplicates": 1})

    assert run_trace(handle)
    assert len(calls) == 5
    assert batches[0] == batches[1]


def test_conflicting_trace_does_not_send_spans():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"name": "Different record"})
        return httpx.Response(409)

    assert not run_trace(handle)
    assert len(calls) == 2
    assert "/api/v1/spans/batch" not in calls


def test_batch_failure_after_trace_creation_is_nonfatal():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(201 if request.url.path.endswith("traces") else 503)

    assert run_trace(handle) is False
    assert calls == ["/api/v1/traces", "/api/v1/spans/batch", "/api/v1/spans/batch"]


def test_redaction_and_serialization_failures_are_safe():
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(201 if request.url.path.endswith("traces") else 200, json={})

    def redact(payload):
        payload["attributes"].pop("secret", None)
        return payload

    assert run_trace(handle, redact=redact)
    assert "do-not-log-this" not in json.dumps(calls)

    def broken_redactor(payload):
        raise RuntimeError("private")

    calls.clear()
    assert not run_trace(handle, redact=broken_redactor)
    assert calls == []


def test_nonfinite_attributes_drop_before_network():
    def handle(request):
        pytest.fail("Invalid JSON must not reach network")

    def redact(payload):
        payload["attributes"]["bad"] = float("nan")
        return payload

    assert not run_trace(handle, redact=redact)


def test_redaction_covers_exception_events_and_status_messages():
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(201 if request.url.path.endswith("traces") else 200, json={})

    def redact(payload):
        payload["status_message"] = None
        for event in payload.get("events", []):
            event["attributes"].pop("exception.message", None)
            event["attributes"].pop("exception.stacktrace", None)
        return payload

    with pytest.raises(ValueError, match="agent failure"):
        run_trace(handle, redact=redact, fail_agent=True)
    assert "agent failure" not in json.dumps(calls)


def test_oversized_attributes_drop_before_network():
    def handle(request):
        pytest.fail("Oversized attributes must not reach network")

    def redact(payload):
        payload["attributes"] = {"too_large": "x" * 32_768}
        return payload

    assert not run_trace(handle, redact=redact)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_url": "ftp://test"},
        {"api_url": "http://user:password@test"},
        {"timeout": 0},
        {"timeout": float("inf")},
        {"max_retries": 4},
    ],
)
def test_invalid_exporter_configuration(kwargs):
    options = {"api_url": "http://test", "project_id": str(uuid4()), **kwargs}
    with pytest.raises(ValueError):
        APIExporter(**options)


def test_nanosecond_conversion_preserves_microseconds():
    assert timestamp(1_000_123_456) == "1970-01-01T00:00:01.000123+00:00"
