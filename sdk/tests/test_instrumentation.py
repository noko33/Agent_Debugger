import asyncio
import json

import pytest
from opentelemetry import trace

from agent_debugger import CapturePolicy, LLMUsage, openai_usage, trace_llm, trace_tool


def rows(calls):
    return {row["name"]: row for row in calls[-1][1]["spans"]}


def test_sync_nested_safe_capture_and_usage(recording_client):
    debugger, calls = recording_client

    @trace_tool(debugger, capture=CapturePolicy(arguments=("api_key",), result=True))
    def lookup(api_key):
        return {"secret": api_key}

    @trace_llm(debugger, model="demo", provider="mock", usage=openai_usage)
    def plan(prompt):
        lookup("credential-value")
        return {"content": prompt, "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    with debugger.trace("Agent"):
        result = plan("private prompt")
    assert result["content"] == "private prompt"
    spans = rows(calls)
    tool, llm = spans["lookup"], spans["plan"]
    assert tool["parent_span_id"] == llm["span_id"]
    assert llm["parent_span_id"] == spans["Agent"]["span_id"]
    assert tool["operation_type"] == "tool" and llm["operation_type"] == "llm"
    assert tool["status_code"] == llm["status_code"] == "ok"
    assert tool["end_time"] >= tool["start_time"]
    assert tool["attributes"]["agent_debugger.arguments.api_key.type"] == "str"
    assert tool["attributes"]["agent_debugger.result.length"] == 1
    assert llm["attributes"]["gen_ai.usage.input_tokens"] == 3
    assert llm["attributes"]["gen_ai.provider.name"] == "mock"
    assert "private prompt" not in json.dumps(calls)
    assert "credential-value" not in json.dumps(calls)
    assert plan.__name__ == "plan"


def test_default_capture_and_unknown_usage(recording_client):
    debugger, calls = recording_client

    @trace_llm(debugger, usage=openai_usage)
    def llm(api_key):
        return {"content": api_key}

    with debugger.trace("Agent"):
        llm("secret")
    attrs = rows(calls)["llm"]["attributes"]
    assert attrs == {"gen_ai.operation.name": "chat"}
    assert "secret" not in json.dumps(calls)


def test_errors_are_type_only_and_original_is_preserved(recording_client):
    debugger, calls = recording_client
    error = ValueError("secret prompt and credential")

    @trace_tool(debugger)
    def fail():
        raise error

    with debugger.trace("Agent", record_exception_details=False):
        with pytest.raises(ValueError) as raised:
            fail()
    assert raised.value is error
    failed = rows(calls)["fail"]
    assert failed["status_code"] == "error"
    assert failed["status_message"] is None
    assert failed["events"][0]["attributes"] == {"exception.type": "ValueError"}
    assert "secret prompt" not in json.dumps(calls)


def test_async_concurrency_context_and_cancellation(recording_client):
    debugger, calls = recording_client
    original = trace.get_current_span()

    @trace_tool(debugger)
    async def child():
        await asyncio.sleep(0)
        return trace.get_current_span().get_span_context().span_id

    @trace_llm(debugger, usage=LLMUsage(input_tokens=0))
    async def branch():
        parent = trace.get_current_span().get_span_context().span_id
        return parent, await child()

    @trace_tool(debugger)
    async def cancelled():
        raise asyncio.CancelledError("private cancellation reason")

    async def run():
        with debugger.trace("Agent", record_exception_details=False) as root:
            pairs = await asyncio.gather(branch(), branch())
            with pytest.raises(asyncio.CancelledError):
                await cancelled()
            assert trace.get_current_span() is root
            return pairs

    pairs = asyncio.run(run())
    spans = calls[-1][1]["spans"]
    by_id = {span["span_id"]: span for span in spans}
    for parent, child_id in pairs:
        assert by_id[f"{child_id:016x}"]["parent_span_id"] == f"{parent:016x}"
        assert by_id[f"{parent:016x}"]["attributes"]["gen_ai.usage.input_tokens"] == 0
    assert len({parent for parent, _ in pairs}) == 2
    assert rows(calls)["cancelled"]["status_code"] == "error"
    assert "private cancellation" not in json.dumps(calls)
    assert trace.get_current_span() is original


def test_manual_span_cancellation_regression(recording_client):
    debugger, calls = recording_client
    with pytest.raises(asyncio.CancelledError), debugger.trace("Agent"):
        with debugger.span("Child"):
            raise asyncio.CancelledError("cancelled")
    assert all(row["status_code"] == "error" for row in rows(calls).values())


def test_adapter_failure_does_not_change_result_or_log_data(recording_client, caplog):
    debugger, calls = recording_client

    def broken(result):
        raise ValueError("private response")

    @trace_llm(debugger, usage=broken)
    def llm():
        return "private response"

    with debugger.trace("Agent"):
        assert llm() == "private response"
    assert "private response" not in caplog.text + json.dumps(calls)
    assert rows(calls)["llm"]["status_code"] == "ok"


def test_no_trace_and_closed_client_pass_through(recording_client):
    debugger, calls = recording_client

    @trace_tool(debugger)
    def tool():
        return 42

    assert tool() == 42 and not calls
    debugger.close()
    assert tool() == 42


@pytest.mark.parametrize("count", [-1, True, 1.5, "12"])
def test_invalid_usage(count):
    with pytest.raises(ValueError):
        LLMUsage(input_tokens=count)


def test_generator_and_invalid_capture_rejected(recording_client):
    debugger, _ = recording_client

    def generator():
        yield 1

    with pytest.raises(TypeError):
        trace_tool(debugger)(generator)
    with pytest.raises(ValueError):
        trace_tool(debugger, capture=CapturePolicy(arguments=("missing",)))(lambda: 1)


def test_shape_does_not_invoke_user_code(recording_client):
    debugger, calls = recording_client

    class Sensitive:
        def __len__(self):
            raise AssertionError("Do not inspect")

        def __repr__(self):
            raise AssertionError("Do not inspect")

    @trace_tool(debugger, capture=CapturePolicy(arguments=("value",), result=True))
    def tool(value):
        return value

    with debugger.trace("Agent"):
        tool(Sensitive())
    assert rows(calls)["tool"]["attributes"]["agent_debugger.result.type"] == "object"


@pytest.mark.parametrize("decorator", [trace_tool, trace_llm])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_sync_async_error_paths(recording_client, decorator, asynchronous):
    debugger, calls = recording_client
    error = RuntimeError("private payload")

    def sync_call():
        raise error

    async def async_call():
        await asyncio.sleep(0)
        raise error

    call = decorator(debugger)(async_call if asynchronous else sync_call)
    with (
        pytest.raises(RuntimeError) as raised,
        debugger.trace("Agent", record_exception_details=False),
    ):
        if asynchronous:
            asyncio.run(call())
        else:
            call()
    assert raised.value is error
    assert all(span["status_code"] == "error" for span in rows(calls).values())
    assert "private payload" not in json.dumps(calls)


def test_actual_task_cancellation_finishes_span(recording_client):
    debugger, calls = recording_client

    async def run():
        started = asyncio.Event()

        @trace_tool(debugger)
        async def wait():
            started.set()
            await asyncio.Event().wait()

        with debugger.trace("Agent"):
            task = asyncio.create_task(wait())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert debugger.exporter.last_export_succeeded
    assert rows(calls)["wait"]["status_code"] == "error"


def test_decorator_survives_export_failure(recording_client, monkeypatch, caplog):
    debugger, _ = recording_client

    def unavailable(*args, **kwargs):
        raise RuntimeError("private transport details")

    monkeypatch.setattr(debugger.exporter, "_request", unavailable)

    @trace_tool(debugger)
    def tool():
        return 42

    with debugger.trace("Agent"):
        assert tool() == 42
    assert debugger.exporter.last_export_succeeded is False
    assert "private transport details" not in caplog.text


def test_multiple_async_traces_are_isolated(recording_client):
    debugger, calls = recording_client

    @trace_tool(debugger)
    async def tool():
        await asyncio.sleep(0)

    async def task(name):
        with debugger.trace(name):
            await tool()

    async def run():
        await asyncio.gather(task("A"), task("B"))

    asyncio.run(run())
    batches = [payload["spans"] for path, payload in calls if path.endswith("batch")]
    assert len(batches) == 2
    assert batches[0][0]["trace_id"] != batches[1][0]["trace_id"]
    for batch in batches:
        root = next(span for span in batch if span["parent_span_id"] is None)
        assert (
            next(span for span in batch if span["name"] == "tool")["parent_span_id"]
            == root["span_id"]
        )
