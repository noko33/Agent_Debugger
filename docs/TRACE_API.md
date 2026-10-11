# Phase 1: trace storage and ingestion

Phase 1 implements a versioned JSON API and PostgreSQL persistence. The frontend remains
the Phase 0 landing page. There is no SDK, exporter, automatic instrumentation, diagnosis,
LLM integration, or visualization yet. Local PostgreSQL runtime verification is blocked;
see `VERIFICATION.md` for actual executed checks.

## Layers and transactions

```text
Pydantic contract → FastAPI router → TraceService → SQLTraceRepository → PostgreSQL
```

Schemas validate transport data. Routers define HTTP contracts. Services enforce
existence, duplicate, and graph rules. Repositories issue SQLAlchemy queries. Models
define persistence types and constraints; Alembic versions define historical DDL.
Each request gets its own synchronous SQLAlchemy session and transaction; sync routes
run in FastAPI's worker thread pool. Writes are committed before a success response is
sent, and failed requests roll back. Connections are bounded and lazily opened.
`GET /health` remains liveness only; it does not prove PostgreSQL readiness.

## Data model

| Table | Primary key | Relationship / purpose |
| --- | --- | --- |
| `projects` | UUID `id` | One instrumented application; many traces |
| `traces` | 32-character hex `trace_id` | One task execution; FK `project_id` → projects |
| `spans` | (`trace_id`, `span_id`) | One operation; FK `trace_id` → traces |
| `span_events` | (`trace_id`, `span_id`, `position`) | Ordered event; composite FK → spans |

Projects have a name, optional description, attributes, and server creation time. The
API generates UUIDs; names are labels and need not be unique. Trace IDs are globally
unique across projects; span IDs are unique within a trace. Identifiers are exactly
32/16 lowercase hexadecimal digits (128/64 bits); all-zero IDs are invalid. Clients
generate trace/span IDs, preserving future instrumentation context.

Traces and spans contain `name`, `start_time`, optional `end_time`, `status_code`, optional
`status_message`, and `attributes`. Span `operation_type` is `agent`, `llm`, `tool`, or
`runtime`; `span_kind` separately follows OpenTelemetry's `internal`, `server`, `client`,
`producer`, or `consumer` vocabulary. Status codes are `unset`, `ok`, or `error`, inspired
by [OpenTelemetry tracing conventions](https://opentelemetry.io/docs/specs/otel/trace/api/).
`unset` means no status was assigned, not necessarily that execution is still running.
The nullability of `end_time` indicates an incomplete timing record.

All input timestamps must have an offset and are normalized to UTC. PostgreSQL uses
`TIMESTAMP WITH TIME ZONE`. End time must be on/after start time. `duration_ms` is read-only
and calculated from timestamps; the database also stores it as a generated column
to prevent inconsistent values from direct writers. Precision is microseconds, not the
nanosecond precision of some instrumentation clocks. Event timestamps may lie outside
span timing bounds; clock skew or delayed event reporting is not treated as invalid.

Each event contains `name`, timezone-aware `timestamp`, and `attributes`. Names can be
`exception`, `state.changed`, etc. Events are embedded in span ingestion but persisted
in their own table. `position` preserves supplied order and allows repeated identical
events; retries do not append them again. All ownership FKs cascade on deletion, although
Phase 1 exposes no delete API. Check constraints protect ID formats, allowed statuses/
operation types/kinds, time ordering, self-parenting, and nonnegative event positions.

Indexes cover trace project/time, global time, status/time, and span trace/time and
trace/parent lookup. Primary keys supply uniqueness. No JSONB GIN index is added before
attribute-filter workloads exist.

## Why JSONB?

Agent applications produce evolving metadata such as tool arguments, model names,
token counts, and structured runtime context. JSONB stores these as structured objects
without a migration for each new attribute. Ownership, IDs, timestamps, status, and
relationships stay in typed columns so PostgreSQL can enforce constraints and use normal
indexes. `attributes` must be an object; nested arrays/objects are supported. NaN/Infinity
are rejected because PostgreSQL JSONB cannot represent them. NUL characters and invalid
UTF-8 text are also rejected before storage. This flexible JSON envelope
is not an OTLP wire-format implementation.

## Endpoints and errors

| Endpoint | Success | Behavior |
| --- | --- | --- |
| `POST /api/v1/projects` | 201 `ProjectRead` | Create project |
| `POST /api/v1/traces` | 201 `TraceRead` | Project must exist; reused trace ID → 409 |
| `POST /api/v1/spans/batch` | 200 `BatchResult` | Atomic ingestion; all traces must exist |
| `GET /api/v1/traces` | 200 `TracePage` | Optional `project_id`, `status_code`, `limit`, `offset` |
| `GET /api/v1/traces/{trace_id}` | 200 `TraceRead` | Missing trace → 404 |
| `GET /api/v1/traces/{trace_id}/spans` | 200 `SpanPage` | Paged spans/events; missing trace → 404 |

Error envelope:

```json
{"error":{"code":"trace_not_found","message":"Trace does not exist","details":null}}
```

Invalid payloads/IDs/query parameters return 422; nonexistent ownership returns 404;
conflicting identities, cycles, and database constraint conflicts return 409; unavailable
or unconfigured PostgreSQL returns 503. Validation details contain locations and error
types without echoing raw input. Database exception text, SQL parameters, and credentials
are not returned. OpenAPI documents response contracts at `/docs`.

Both list endpoints default to `limit=50`, `offset=0`; limits are 1–500, offsets
0–1,000,000. Trace order is start time descending, then trace ID descending; spans are
start time ascending, then span ID ascending. Ties therefore have a deterministic order.
Responses contain `items`, `total`, `limit`, `offset`. Unknown project filters yield an
empty page. Offset pagination is not a snapshot: concurrent inserts can move rows between
pages, and count/list statements use PostgreSQL's default read-committed isolation.

## Request and response examples

1. Create a project: `POST /api/v1/projects`

```json
{"name":"Research agent","description":"Local debugging example","attributes":{"environment":"development"}}
```

Example 201 response (UUID/time generated by the server):

```json
{"id":"11111111-1111-4111-8111-111111111111","name":"Research agent","description":"Local debugging example","attributes":{"environment":"development"},"created_at":"2026-10-11T12:00:00Z"}
```

2. Create a trace: `POST /api/v1/traces`. Replace `project_id` with the returned UUID.

```json
{
  "project_id":"11111111-1111-4111-8111-111111111111",
  "trace_id":"0123456789abcdef0123456789abcdef",
  "name":"Research a topic",
  "start_time":"2026-10-11T12:00:00Z",
  "end_time":"2026-10-11T12:00:02Z",
  "status_code":"ok",
  "attributes":{"agent.name":"researcher","configuration":{"max_steps":5}}
}
```

The 201 response contains submitted fields plus `status_message: null` and
`duration_ms: 2000.0`. `GET /api/v1/traces/0123456789abcdef0123456789abcdef` returns the
same record. `GET /api/v1/traces?limit=1&offset=0&status_code=ok` returns:

```json
{
  "items":[{
    "project_id":"11111111-1111-4111-8111-111111111111",
    "trace_id":"0123456789abcdef0123456789abcdef",
    "name":"Research a topic",
    "start_time":"2026-10-11T12:00:00Z",
    "end_time":"2026-10-11T12:00:02Z",
    "status_code":"ok",
    "status_message":null,
    "attributes":{"agent.name":"researcher","configuration":{"max_steps":5}},
    "duration_ms":2000.0
  }],
  "total":1,"limit":1,"offset":0
}
```

3. Ingest child before parent: `POST /api/v1/spans/batch`

```json
{
  "spans":[
    {
      "trace_id":"0123456789abcdef0123456789abcdef",
      "span_id":"fedcba9876543210",
      "parent_span_id":"0123456789abcdef",
      "name":"Search tool",
      "operation_type":"tool",
      "span_kind":"client",
      "start_time":"2026-10-11T12:00:00.500000Z",
      "end_time":"2026-10-11T12:00:01Z",
      "status_code":"error",
      "status_message":"Tool input rejected",
      "attributes":{"tool.name":"search"},
      "events":[{
        "name":"exception",
        "timestamp":"2026-10-11T12:00:01Z",
        "attributes":{"exception.type":"ValueError","exception.message":"Invalid search query"}
      }]
    },
    {
      "trace_id":"0123456789abcdef0123456789abcdef",
      "span_id":"0123456789abcdef",
      "name":"Agent run",
      "operation_type":"agent",
      "start_time":"2026-10-11T12:00:00Z",
      "end_time":"2026-10-11T12:00:02Z",
      "status_code":"ok"
    }
  ]
}
```

Response:

```json
{"received":2,"inserted":2,"duplicates":0}
```

Retrying the original request returns `{"received":2,"inserted":0,"duplicates":2}`.
Span retrieval adds `duration_ms` to each item, includes events, and uses the same page
envelope. An empty existing trace returns `{"items":[],"total":0,"limit":50,"offset":0}`.

## Duplicate and out-of-order policies

- The key is `(trace_id, span_id)`. Exact retries, including repeats inside one batch,
  count as duplicates and do not create rows or events. Defaulted fields and UTC offsets
  are normalized before comparison; object key order is irrelevant, event list order matters.
- A reused key with different content returns `409 span_conflict`; the entire batch
  rolls back. There is no overwrite, partial success, or update endpoint in this phase.
- All referenced traces must already exist. Parent spans may arrive in any order,
  including a later request. Missing parents remain references and can remain absent
  permanently if an exporter samples or omits the parent. Parents are resolved only
  inside the same trace, never by looking up another trace's span ID.
- Deliberately no parent FK: a required self-FK would reject children exported before
  their parents. The API rejects self-parenting and cycles across existing plus incoming
  spans. Ownership FKs still apply. Direct SQL writers can bypass the API cycle policy.
- Each batch locks affected trace rows in sorted ID order, checks duplicates and the
  graph, and inserts spans/events in one transaction. Concurrent API writers for the
  same trace serialize; writes for different traces can proceed independently. Composite
  primary keys are a final duplicate safeguard. This avoids adding queues or another database.
- Trace creation itself is not retry-idempotent: a repeated trace ID returns 409. Clients
  can retrieve it to confirm ownership/content before exporting spans.

## Limits and security boundaries

500 spans per batch, 100 events per span, 32 KiB JSON per attributes object, and 1 MiB
normalized JSON per span batch. Unknown request fields are rejected. These are
post-parse validation limits, not a transport-level streaming body cap. Graph checking
currently reads the trace's parent-link map, so its work grows with trace size.

Local development endpoints have no authentication, tenant authorization, automatic
redaction, retention policy, or rate limits yet. Only send sanitized development data;
redact secrets at the producer. Do not expose ingestion publicly until an explicit
authentication/redaction/retention policy and transport body limits are implemented.
JSONB is a storage choice, not a redaction mechanism. No trace data is sent to an LLM.

## Running migrations and PostgreSQL tests on Windows

From the root, start PostgreSQL with the password configured in the ignored root `.env`:

```powershell
docker compose up -d postgres
cd backend
Copy-Item .env.example .env
notepad .env
# Match the root .env password; native POSTGRES_HOST is localhost.
uv sync --frozen
uv run python -m alembic upgrade head
uv run python -m uvicorn app.main:app --reload
```

Offline SQL compilation does not require PostgreSQL or credentials:

```powershell
uv run python -m alembic upgrade head --sql
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest
```

Without `TEST_DATABASE_URL`, PostgreSQL tests skip explicitly. For real integration
testing, create/use a **disposable test database** on a running PostgreSQL server and
set `TEST_DATABASE_URL` in the terminal environment (credentials redacted here):

```powershell
$env:TEST_DATABASE_URL = 'postgresql://agent_debugger:<encoded-local-password>@localhost:5432/agent_debugger_test'
uv run python -m pytest -m postgres -rs
```

This variable is intentionally read from the process environment rather than silently
using the application database. Tests create a uniquely named schema, apply the real
Alembic migration, and remove only that schema afterward. The test role needs schema
creation permission. Ordinary tests use rollback/savepoint isolation; concurrency tests
use separate connections and explicitly clean up their own records. A configured but
unreachable database fails tests instead of being reported as skipped.

CI is configured to run these tests against an ephemeral PostgreSQL service. Its
passwordless trust authentication is confined to the isolated loopback CI test service;
local Compose still requires a password. CI execution has not been claimed locally.

## Preparing for Phase 2

A future SDK/exporter can preserve trace/span IDs, parent context, name, timestamps,
status, span kind and exception events, and map agent/LLM/tool semantics into attributes.
The application project ID is additional routing context outside OpenTelemetry's span
context. `operation_type` is an application classification; it does not replace span kind.

These are immutable ingestion snapshots: preferably export completed trace envelopes
and ended spans, create the project/trace before its batches, and retry original payloads.
Incomplete timings are accepted but cannot be finalized by overwriting an existing ID
in Phase 1. Live lifecycle updates require a separately designed API; the initial Phase 2
exporter should export completed records. Trace flags, tracestate, span links, resource/
scope mapping, dropped-event counters, SDK buffering, transport retries, redaction hooks,
and OTLP/OpenInference adapters are Phase 2 design work, not implemented functionality.
