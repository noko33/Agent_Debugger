import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.context import attach, detach, get_current
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from agent_debugger import AgentDebugger, APIExporter


@pytest.fixture
def recording_client():
    calls = []

    def handle(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201 if request.url.path.endswith("traces") else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        exporter = APIExporter("http://test", uuid4(), client=client)
        with AgentDebugger(exporter.project_id, exporter=exporter) as debugger:
            yield debugger, calls


def test_nested_context_ids_attributes_and_status(recording_client):
    debugger, calls = recording_client
    original = trace.get_current_span()
    with debugger.trace("Agent run", attributes={"agent.name": "demo"}) as root:
        assert not debugger.flush()
        with debugger.span("Plan", operation_type="llm") as plan:
            with debugger.span("Lookup", operation_type="tool", kind=SpanKind.CLIENT) as tool:
                tool.set_attribute("tool.name", "lookup")
                tool.add_event("state.changed", {"state": "ready"})
        assert trace.get_current_span() is root
        root.set_status(Status(StatusCode.OK))
    assert trace.get_current_span() is original
    assert debugger.flush() and debugger.exporter.last_export_succeeded
    assert len(calls) == 2
    trace_payload = calls[0][1]
    assert trace_payload["trace_id"] == f"{root.get_span_context().trace_id:032x}"
    assert int(trace_payload["trace_id"], 16) > 0
    assert trace_payload["status_code"] == "ok"
    rows = {row["name"]: row for row in calls[1][1]["spans"]}
    assert rows["Agent run"]["parent_span_id"] is None
    assert rows["Plan"]["parent_span_id"] == f"{root.get_span_context().span_id:016x}"
    assert rows["Lookup"]["parent_span_id"] == f"{plan.get_span_context().span_id:016x}"
    assert all(len(row["span_id"]) == 16 for row in rows.values())
    assert rows["Lookup"]["span_kind"] == "client"
    assert rows["Lookup"]["attributes"]["tool.name"] == "lookup"
    assert rows["Lookup"]["events"][0]["name"] == "state.changed"
    assert all(row["start_time"].endswith("+00:00") for row in rows.values())
    assert all(row["end_time"] >= row["start_time"] for row in rows.values())


def test_agent_exception_is_preserved_and_recorded(recording_client):
    debugger, calls = recording_client
    original = ValueError("tool input rejected")
    with pytest.raises(ValueError) as raised, debugger.trace("Failing task"):
        with debugger.span("Failing tool", operation_type="tool"):
            raise original
    assert raised.value is original
    assert calls[0][1]["status_code"] == "error"
    rows = calls[1][1]["spans"]
    assert all(row["status_code"] == "error" for row in rows)
    event = rows[0]["events"][0]
    assert event["name"] == "exception"
    assert event["attributes"]["exception.type"] == "ValueError"
    assert event["attributes"]["exception.message"] == str(original)
    assert "exception.stacktrace" in event["attributes"]


def test_caught_exception_does_not_mark_root_failed(recording_client):
    debugger, calls = recording_client
    with debugger.trace("Recovered task"):
        try:
            with debugger.span("Retryable tool"):
                raise RuntimeError("temporary")
        except RuntimeError:
            pass
        with debugger.span("Fallback"):
            pass
    assert calls[0][1]["status_code"] == "unset"
    assert (
        next(row for row in calls[1][1]["spans"] if row["name"] == "Retryable tool")["status_code"]
        == "error"
    )


def test_async_tasks_preserve_separate_parents(recording_client):
    debugger, calls = recording_client

    async def branch(name):
        with debugger.span(name):
            await asyncio.sleep(0)
            with debugger.span(name + " child"):
                await asyncio.sleep(0)

    async def run():
        with debugger.trace("Concurrent task"):
            await asyncio.gather(branch("a"), branch("b"))

    asyncio.run(run())
    rows = {row["name"]: row for row in calls[1][1]["spans"]}
    assert rows["a child"]["parent_span_id"] == rows["a"]["span_id"]
    assert rows["b child"]["parent_span_id"] == rows["b"]["span_id"]


def test_standard_w3c_context_and_global_provider_unchanged(recording_client):
    debugger, calls = recording_client
    provider = trace.get_tracer_provider()
    with debugger.trace("Propagated task") as root:
        carrier = {}
        propagator = TraceContextTextMapPropagator()
        propagator.inject(carrier)
        extracted = trace.get_current_span(propagator.extract(carrier)).get_span_context()
        assert extracted.trace_id == root.get_span_context().trace_id
        assert extracted.span_id == root.get_span_context().span_id
    assert trace.get_tracer_provider() is provider
    assert len(calls) == 2


def test_thread_uses_standard_otel_attach_detach(recording_client):
    debugger, calls = recording_client

    def worker(context):
        token = attach(context)
        try:
            with debugger.span("Worker"):
                pass
        finally:
            detach(token)

    with debugger.trace("Thread task"), ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker, get_current()).result(timeout=5)
    rows = {row["name"]: row for row in calls[1][1]["spans"]}
    assert rows["Worker"]["parent_span_id"] == rows["Thread task"]["span_id"]


def test_span_rejects_unrelated_ambient_context(recording_client):
    debugger, calls = recording_client
    provider = TracerProvider(shutdown_on_exit=False)
    try:
        with provider.get_tracer("host").start_as_current_span("Host"):
            with pytest.raises(RuntimeError), debugger.span("Wrong client"):
                pass
    finally:
        provider.shutdown()
    assert calls == []


def test_trace_starts_fresh_and_restores_ambient_span(recording_client):
    debugger, calls = recording_client
    provider = TracerProvider(shutdown_on_exit=False)
    try:
        with provider.get_tracer("host").start_as_current_span("Host") as host:
            with debugger.trace("Independent task") as root:
                assert root.get_span_context().trace_id != host.get_span_context().trace_id
            assert trace.get_current_span() is host
    finally:
        provider.shutdown()
    assert calls[1][1]["spans"][0]["parent_span_id"] is None


@pytest.mark.parametrize("max_spans,max_traces", [(0, 1), (501, 1), (1, 0), (1, 129)])
def test_invalid_buffer_configuration(max_spans, max_traces):
    with pytest.raises(ValueError):
        AgentDebugger(uuid4(), max_spans=max_spans, max_traces=max_traces)


def test_buffer_overflow_is_nonfatal_and_sends_nothing(recording_client):
    debugger, calls = recording_client
    debugger._processor.max_spans = 1
    with debugger.trace("Bounded task"):
        with debugger.span("Too many spans"):
            with debugger.span("Still harmless"):
                pass
    assert calls == []
    assert debugger.exporter.last_export_succeeded is False


def test_active_trace_limit_and_nested_independent_trace(recording_client):
    debugger, calls = recording_client
    debugger._processor.max_traces = 1
    with debugger.trace("Outer") as outer:
        with debugger.trace("Dropped inner"):
            pass
        assert trace.get_current_span() is outer
    assert len(calls) == 2 and calls[0][1]["name"] == "Outer"


def test_child_outliving_root_is_dropped(recording_client):
    debugger, calls = recording_client
    with debugger.trace("Incomplete"):
        child = debugger._tracer.start_span("Unfinished")
    child.end()
    assert calls == []
    assert debugger.exporter.last_export_succeeded is False


def test_lifecycle_and_invalid_usage(recording_client):
    debugger, calls = recording_client
    with pytest.raises(RuntimeError), debugger.span("Outside trace"):
        pass
    with pytest.raises(ValueError), debugger.trace(" "):
        pass
    with debugger.trace("Valid"):
        with pytest.raises(ValueError), debugger.span("Invalid", operation_type="unknown"):
            pass
    debugger.close()
    debugger.close()
    with pytest.raises(RuntimeError), debugger.trace("Closed"):
        pass
    assert len(calls) == 2


def test_close_with_unfinished_trace_marks_delivery_failed(recording_client):
    debugger, calls = recording_client
    with debugger.trace("Unfinished at shutdown"):
        debugger.close()
    assert calls == []
    assert debugger.exporter.last_export_succeeded is False
