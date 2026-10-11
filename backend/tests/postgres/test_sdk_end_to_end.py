"""Installed SDK -> real HTTP server -> ingestion service -> isolated PostgreSQL schema."""

import asyncio
import runpy
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app
from app.models.traces import Span, SpanEventRow, Trace
from app.schemas.traces import SpanRead, TraceRead

sdk = pytest.importorskip("agent_debugger", reason="Run pytest with uv run --with ../sdk")
pytestmark = pytest.mark.postgres


@pytest.fixture
def live_api(postgres_engine: Engine) -> Iterator[str]:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)

    def test_session():
        with factory.begin() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "Isolated HTTP server did not start"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "Isolated HTTP server did not stop"


def test_sdk_http_postgres_hierarchy_and_idempotency(
    live_api: str, postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with httpx.Client(base_url=live_api, trust_env=False, timeout=5) as client:
        project = client.post("/api/v1/projects", json={"name": "SDK e2e"}).json()
        exporter = sdk.APIExporter(live_api, project["id"])
        exported = []
        original = exporter.export

        def capture(root, spans):
            exported.append((root, spans))
            return original(root, spans)

        monkeypatch.setattr(exporter, "export", capture)
        with sdk.AgentDebugger(project["id"], exporter=exporter) as debugger:
            with debugger.trace("Example agent", attributes={"agent.name": "demo"}) as root:
                with debugger.span("Plan", operation_type="llm"):
                    with debugger.span("Lookup", operation_type="tool"):
                        pass
                with debugger.span("Answer", operation_type="runtime"):
                    pass
            assert exporter.last_export_succeeded
            # Retry the exact completed snapshot: Trace POST 409 -> verify GET -> span no-op.
            assert original(*exported[0])
        trace_id = f"{root.get_span_context().trace_id:032x}"
        trace_data = client.get(f"/api/v1/traces/{trace_id}").json()
        stored_trace = TraceRead.model_validate(trace_data)
        assert stored_trace.end_time is not None and stored_trace.duration_ms >= 0
        rows = client.get(f"/api/v1/traces/{trace_id}/spans").json()
        assert rows["total"] == 4
        spans = {span.name: span for span in map(SpanRead.model_validate, rows["items"])}
        assert spans["Example agent"].parent_span_id is None
        assert spans["Plan"].parent_span_id == spans["Example agent"].span_id
        assert spans["Lookup"].parent_span_id == spans["Plan"].span_id
        assert spans["Answer"].parent_span_id == spans["Example agent"].span_id
        assert spans["Lookup"].operation_type == "tool"
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count()).select_from(Trace).where(Trace.trace_id == trace_id)
                )
                == 1
            )
            assert (
                connection.scalar(
                    select(func.count()).select_from(Span).where(Span.trace_id == trace_id)
                )
                == 4
            )


def test_sdk_exception_roundtrip(live_api: str, postgres_engine: Engine) -> None:
    with httpx.Client(base_url=live_api, trust_env=False, timeout=5) as client:
        project = client.post("/api/v1/projects", json={"name": "SDK exception e2e"}).json()
        with sdk.AgentDebugger(project["id"], live_api) as debugger:
            with pytest.raises(ValueError, match="tool rejected"), debugger.trace("Failed task"):
                with debugger.span("Tool", operation_type="tool"):
                    raise ValueError("tool rejected")
            assert debugger.exporter.last_export_succeeded
        page = client.get("/api/v1/traces", params={"project_id": project["id"]}).json()
        assert page["total"] == 1 and page["items"][0]["status_code"] == "error"
        trace_id = page["items"][0]["trace_id"]
        spans = client.get(f"/api/v1/traces/{trace_id}/spans").json()["items"]
        assert all(row["status_code"] == "error" for row in spans)
        assert all(
            row["events"][0]["attributes"]["exception.type"] == "ValueError" for row in spans
        )
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(SpanEventRow)
                    .where(SpanEventRow.trace_id == trace_id)
                )
                == 2
            )


def test_decorated_example_roundtrip(live_api: str, postgres_engine: Engine) -> None:
    workflow = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / "examples/instrumented_agent.py")
    )
    with httpx.Client(base_url=live_api, trust_env=False, timeout=5) as client:
        response = client.post("/api/v1/projects", json={"name": "Phase 2B isolated example"})
        response.raise_for_status()
        with sdk.AgentDebugger(response.json()["id"], live_api) as debugger:
            trace_id = workflow["run_agent"](debugger)
            assert debugger.exporter.last_export_succeeded
        trace_data = client.get(f"/api/v1/traces/{trace_id}").json()
        assert TraceRead.model_validate(trace_data).status_code == "ok"
        data = client.get(f"/api/v1/traces/{trace_id}/spans").json()
        spans = {span.name: span for span in map(SpanRead.model_validate, data["items"])}
        assert data["total"] == 7
        root = spans["Instrumented agent"]
        assert spans["Read local catalog"].parent_span_id == spans["Lookup"].span_id
        for name in ("Plan action", "Lookup", "Optional enrichment", "Fallback", "Final answer"):
            assert spans[name].parent_span_id == root.span_id
        failed = spans["Optional enrichment"]
        assert failed.status_code == "error"
        assert failed.events[0].attributes == {"exception.type": "ConnectionError"}
        assert spans["Fallback"].status_code == "ok"
        assert root.events[0].name == "recovery"
        assert spans["Plan action"].attributes["gen_ai.provider.name"] == "mock"
        assert "gen_ai.usage.input_tokens" not in spans["Plan action"].attributes
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count()).select_from(Span).where(Span.trace_id == trace_id)
                )
                == 7
            )


def test_async_decorators_usage_roundtrip(live_api: str) -> None:
    with httpx.Client(base_url=live_api, trust_env=False, timeout=5) as client:
        response = client.post("/api/v1/projects", json={"name": "Phase 2B token fixture"})
        response.raise_for_status()
        with sdk.AgentDebugger(response.json()["id"], live_api) as debugger:

            @sdk.trace_tool(debugger)
            async def tool():
                await asyncio.sleep(0)

            @sdk.trace_llm(debugger, model="fixture", provider="mock", usage=sdk.openai_usage)
            async def llm():
                await tool()
                # Explicit test fixture usage, not a tokenizer estimate.
                return {"usage": {"input_tokens": 11, "output_tokens": 4}}

            async def run():
                with debugger.trace("Async token fixture") as root:
                    await asyncio.gather(llm(), llm())
                return f"{root.get_span_context().trace_id:032x}"

            trace_id = asyncio.run(run())
            assert debugger.exporter.last_export_succeeded
        data = client.get(f"/api/v1/traces/{trace_id}/spans").json()
        spans = list(map(SpanRead.model_validate, data["items"]))
        assert len(spans) == 5
        llms = [span for span in spans if span.operation_type == "llm"]
        assert len(llms) == 2
        for span in llms:
            assert span.attributes["gen_ai.usage.input_tokens"] == 11
            assert span.attributes["gen_ai.usage.output_tokens"] == 4
            assert sum(child.parent_span_id == span.span_id for child in spans) == 1
