# Agent Debugger Python SDK — Phase 2A/2B

Explicit, reusable Python 3.11+ tracing for the existing Agent Debugger ingestion API.
Installable package: `agent-debugger-sdk`; import: `agent_debugger`. This package has
not been published to PyPI. It uses OpenTelemetry API/SDK and HTTPX, without importing
the backend or requiring database access in instrumented applications.

## Install and run on Windows

From `D:\Agent_Debugger`, with the existing Docker services running:

```powershell
# SDK development environment, using the checked-in lockfile:
uv sync --project sdk --frozen
uv run --project sdk python examples/minimal_agent.py --create-project
```

`--create-project` explicitly creates a new development project before running the
example. Subsequent runs can reuse its printed UUID:

```powershell
uv run --project sdk python examples/minimal_agent.py --project-id <project-uuid>
```

For another Python application, add this local package to that application's environment:

```powershell
uv add D:\Agent_Debugger\sdk
# Or, in an activated Python environment:
python -m pip install D:\Agent_Debugger\sdk
```

Both wheel and source distributions can be generated with `uv build` from `sdk/`.
No API keys or LLM calls are needed. `AGENT_DEBUGGER_API_URL` and
`AGENT_DEBUGGER_PROJECT_ID` are optional environment variables read by the example
script; the SDK itself receives configuration explicitly through its constructor.

## Public API

```python
from agent_debugger import AgentDebugger
from opentelemetry.trace import Status, StatusCode

with AgentDebugger(project_id="<existing-project-uuid>") as debugger:
    with debugger.trace("Answer a question", attributes={"agent.name": "demo"}) as task:
        with debugger.span("Plan", operation_type="agent"):
            with debugger.span("Lookup", operation_type="tool") as tool:
                tool.set_attribute("tool.name", "local_lookup")
                tool.add_event("state.changed", {"state": "found"})
        task.set_status(Status(StatusCode.OK))

    print(debugger.exporter.last_export_succeeded)
```

- `AgentDebugger(project_id, api_url="http://localhost:8000", exporter=None,
  max_spans=500, max_traces=32)` owns a private OpenTelemetry tracer provider. It does
  not replace the application's global provider.
- `trace(name, attributes=None)` starts a fresh task trace and its root Agent span.
  An ambient host span is restored afterward. Cross-service continuation into an
  existing remote trace is outside Phase 2A.
- `span(name, operation_type="runtime", attributes=None, kind=SpanKind.INTERNAL)`
  creates a child of the current span inside this client's trace. Operation types are
  `agent`, `llm`, `tool`, and `runtime`; `llm` is a label, not an LLM integration.
- Both context managers yield real OpenTelemetry spans: use `set_attribute`, `add_event`,
  `set_status`, `record_exception`, and `get_span_context` directly.
- `flush()` checks whether unfinished buffered traces remain. Finished traces export
  synchronously on root exit. It does not retry failed exports or end active spans.
  Check `last_export_succeeded` for the latest delivery attempt, not `flush()`.
- `close()` is idempotent and closes owned resources; the client context manager calls
  it automatically. Finish all work before closing. A caller-supplied HTTP client
  remains owned by the caller.

An exception escaping a span records the standard `exception` event (type, message,
stacktrace), sets error status, and continues propagating the original application
exception. The SDK does not catch the Agent's business failures. If application code
catches a failed child, the root is not automatically marked failed. Successful spans
default to OTel `unset`; explicitly set `OK` when success has been established.

Attributes use standard OTel scalar values and homogeneous sequences, rather than
arbitrary nested Python objects. Backend JSONB supports richer JSON, but Phase 2A does
not invent an alternative OTel attribute format. Span/event names must meet the API's
200-character limit. The SDK limits spans to 64 attributes, 100 events, and attribute
strings to 4096 characters. Backend validation remains authoritative.

## Context, identifiers, and concurrency

OpenTelemetry generates non-zero 128-bit trace IDs and 64-bit span IDs. The exporter
formats them as lowercase 32/16-character hex, and converts nanosecond timestamps to
timezone-aware UTC microseconds without floating-point rounding. Durations are derived
by the existing backend. `agent_debugger.operation_type` is an internal OTel attribute
mapped into the API's typed `operation_type` field and removed from exported attributes.

OTel's context API manages the current span and client scope. Nested contexts restore
their parents; asyncio tasks inherit independent contexts. Await child tasks before
leaving the root trace. For worker threads, use standard OTel context propagation:

```python
from opentelemetry.context import attach, detach, get_current

parent_context = get_current()  # capture inside debugger.trace(...)


def worker():
    token = attach(parent_context)
    try:
        with debugger.span("Worker"):
            pass
    finally:
        detach(token)
```

Standard `TraceContextTextMapPropagator.inject` can read/inject the active W3C context;
there is no custom trace ID stack or replacement header protocol. Remote continuation,
automatic framework instrumentation, OTLP adapters, and OpenInference mapping remain
future work. See the official [OTel context propagation guide](https://opentelemetry.io/docs/languages/python/propagation/).

## Export behavior and limitations

The immutable Phase 1 Trace API requires a complete record. A bounded `SpanProcessor`
buffers ended spans until the root finishes, then `APIExporter`:

1. Maps the root to `POST /api/v1/traces`.
2. Maps all spans, including the root, to `POST /api/v1/spans/batch`.
3. On trace POST 409, fetches and compares the stored immutable record before continuing.
   This handles a lost POST response without accepting a conflicting trace.
4. Retries transport failures and HTTP 408/429/500/502/503/504 at most once by default;
   exact span retries use existing backend idempotency. Other HTTP errors are not retried.

`APIExporter(api_url, project_id, timeout=2.0, max_retries=1, redact=None, client=None)`
allows explicit configuration. Retries are immediate, capped at three, and contain no
unbounded waiting. HTTPX timeouts apply to individual network phases, not a single
overall task deadline. Export is synchronous, so root exit can incur network latency;
do not block a latency-sensitive asyncio event loop with production network export.
An explicit caller-owned HTTPX client can provide transport/proxy settings. The default
client ignores environment proxies; no user or system settings are changed.

Network, HTTP, serialization, and redactor errors return failure with a generic warning;
payloads, response bodies, exception contents, and URLs are not included in exporter
failure logs. Agent execution continues. There is no background worker, durable queue,
automatic retry after the attempt, or crash-safe delivery. Trace creation and span batch
submission are separate transactions: failure between them can leave a trace without
spans. No backend/API redesign was introduced to conceal that limitation.

At most 500 spans per trace and 32 concurrent traces are buffered by default. Overflow
or children still active when the root ends drop the entire buffered trace with a generic
warning. Respect the backend's 32 KiB per-attribute-object and 1 MiB batch limits; oversized
encoded payloads are dropped before any request. `last_export_succeeded` reports the
latest attempt/drop, so concurrent traces should not treat it as a per-trace receipt.

## Redaction

Prompts, tool arguments, and environment variables are not collected automatically.
User attributes and exception messages/stacktraces can still contain secrets. Provide a
payload redactor before capturing sensitive workloads:

```python
from agent_debugger import APIExporter, AgentDebugger


def redact(payload):
    payload["attributes"].pop("api_key", None)
    payload["status_message"] = None
    for event in payload.get("events", []):
        event["attributes"].pop("exception.message", None)
        event["attributes"].pop("exception.stacktrace", None)
    return payload


exporter = APIExporter("http://localhost:8000", project_id, redact=redact)
debugger = AgentDebugger(project_id, exporter=exporter)
```

The callback runs on each Trace and Span payload, including its events. Preserve
identifiers, relationships, timestamps, and required fields. A broken callback safely
drops the export. There is no automatic secret detection or LLM transmission.

## Phase 2B: explicit Tool and LLM decorators

Decorators instrument functions you select; they do not intercept LangChain, LlamaIndex,
provider libraries, or arbitrary network calls. The same decorator supports `def` and
`async def`, preserves the wrapped signature/name, and uses the active OTel parent.
Outside this client's active trace (including after close), calls run without tracing.
Finish/await all children before leaving the root. Generators and streaming iteration
are unsupported and generator functions are rejected at decoration time.

```python
from agent_debugger import (
    AgentDebugger,
    CapturePolicy,
    LLMUsage,
    openai_usage,
    trace_llm,
    trace_tool,
)

debugger = AgentDebugger(project_id, "http://localhost:8000")


@trace_tool(debugger, capture=CapturePolicy(arguments=("query",), result=True))
def lookup(query: str) -> dict:
    return {"answer": "A local fact"}


@trace_llm(debugger, model="your-model", provider="your-provider", usage=openai_usage)
async def ask(prompt: str) -> dict:
    return await your_provider_call(prompt)


# Inside an async function:
with debugger.trace("Task", record_exception_details=False):
    facts = lookup("private query")
    response = await ask("private prompt")
debugger.close()
```

The provider function above is application code, not a bundled integration. Install the
SDK using the existing installation instructions; no new dependencies are required.
Run the self-contained no-provider example from the repository root:

```powershell
uv run --project sdk python examples/instrumented_agent.py --create-project
```

Use `--project-id <UUID>` to reuse a project. This example reports **no token counts**:
its deterministic LLM stand-ins do not have a tokenizer or billed usage.

Default capture stores names, operation type, timing, status, and exception **type** only.
`CapturePolicy` explicitly enables built-in type/length metadata for selected arguments
and/or results. It never records values, dictionary keys, object properties, repr/str,
prompts, completions, or credentials. Arbitrary objects report `object` without inspection.
Lengths can still reveal structural information; enable this only where appropriate.
Function names and caller-supplied model/provider labels are exported: use static safe labels.

`LLMUsage(input_tokens=..., output_tokens=...)` accepts known non-negative integer counts
(including zero); omitted counts remain absent. Supply it directly to `usage=`, or supply
a synchronous response adapter returning `LLMUsage`. The optional `openai_usage` adapter
reads **dictionary** `usage.input_tokens/output_tokens` (Responses) or
`usage.prompt_tokens/completion_tokens` (Chat Completions), without reading response content.
For typed provider objects, provide your own small adapter; there is no provider dependency.
Invalid counts/adapters produce a generic warning and omit usage; they never replace the
application result. Fixed `LLMUsage` is appropriate only when the caller knows the counts
for every invocation; prefer a response adapter for variable usage.

LLM spans are CLIENT spans with `gen_ai.operation.name=chat`, `gen_ai.provider.name`,
`gen_ai.request.model`, and available `gen_ai.usage.input_tokens/output_tokens`. Tool spans
are INTERNAL with `gen_ai.tool.name`. These conventions are evolving; see the
[OpenTelemetry GenAI definitions](https://github.com/open-telemetry/semantic-conventions-genai).
They remain JSONB attributes; existing API fields and database tables are unchanged.

Successful decorated calls are OK; errors and async cancellation are ERROR, with one
type-only exception event, and the original exception is re-raised. A recovered child
failure does not automatically fail the root. Manual `trace()` / `span()` preserve Phase
2A's detailed exception behavior by default; pass `record_exception_details=False` to
**every manual scope** where sensitive errors can escape, including the root, or use the
existing exporter redactor. Decorator privacy does not redact manually added attributes.
API outages retain the existing non-throwing exporter behavior and bounded retries.

## Tests

```powershell
uv run --project sdk python -m pytest
uv run --project sdk ruff check --config sdk/pyproject.toml sdk examples/minimal_agent.py examples/instrumented_agent.py
uv run --project sdk ruff format --check --config sdk/pyproject.toml sdk examples/minimal_agent.py examples/instrumented_agent.py
```

Real HTTP/PostgreSQL end-to-end tests live in
`backend/tests/postgres/test_sdk_end_to_end.py` and reuse the existing generated-schema
fixtures. Configure a dedicated `TEST_DATABASE_URL`, then from `backend/` run:

```powershell
uv run --frozen --with-editable ../sdk python -m pytest -ra
```

These tests start an isolated loopback FastAPI server, export through HTTP, validate
responses using the existing Pydantic contracts, and query actual PostgreSQL rows.
They verify nested relationships, exceptions, committed writes, and exact retry
idempotency. Without a test DSN they skip explicitly; a configured unavailable database
fails. Without the SDK installed/importable the SDK test module skips explicitly;
the `--with ../sdk` command and CI backend job ensure it is included.
