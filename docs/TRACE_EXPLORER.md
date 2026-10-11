# Phase 3A: Trace Explorer

Open `http://localhost:3000/` with the existing Compose services running. Select an
execution from **Traces**. The UI uses only existing read endpoints:

- `GET /api/v1/traces?limit=20&offset=...`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/traces/{trace_id}/spans?limit=500&offset=...`

The browser uses `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`) and existing
explicit backend CORS. This variable is public; never put credentials in it. HTTP errors
show a safe status/message with retry controls; raw response bodies and transport details
are not echoed. Requests time out after ten seconds per page and are aborted when the
selection changes or the component unmounts; stale responses cannot replace a newer trace.

## Navigation and displayed data

Trace metadata includes project/trace IDs, UTC timestamps, recorded duration, status and
attributes. Traces have Previous/Next pagination and explicit Refresh list / Reload trace.
There is no polling or streaming. Refreshing the trace list does not reload an open trace.

The execution graph uses React Flow custom nodes for Agent, LLM, Tool and Runtime. Labels
and badges distinguish OK, Error and Unset; exception-bearing nodes also have a rose
background/border and an exception label. An Unset status is not treated as success.
Pan, zoom and Fit View navigate the graph. Select the button within a node using mouse
or keyboard, or switch to **Span list** for an indented, scrollable alternative. Selecting
a span opens its detail panel; **Inspect parent** navigates to a loaded recorded parent.
On narrow screens, the detail panel follows the execution area vertically.

Details include operation/kind/status, IDs, parent, UTC start/end, duration, attributes,
available token usage, events and exception metadata. The frontend uses the API's
timestamp-derived `duration_ms`, preserving sub-millisecond precision; it never replaces
unknown timing or counts with zero. Zero is displayed only when recorded. Token values
are shown only for non-negative integer `gen_ai.usage.input_tokens/output_tokens`.
Missing values say **Not recorded**. JSON and event contents are escaped React text in
bounded scrolling blocks; no executable HTML, Markdown rendering, or automatic links.
Existing trace content can still be sensitive: local development has no authentication.

## Relationships and limits

Spans are loaded across pages, with a 5,000-span UI bound. A larger trace, or an early empty
page before the reported total, has an explicit **Partial graph** warning. Pagination is
read-committed and not a snapshot; manually reload after ingestion finishes.

Tree construction retains every unique span. Missing/self/cross-trace parent links are
detached with warnings; a cycle is broken at one link with a warning. The original parent
ID remains visible in span details. Duplicate IDs are displayed once with a warning.
Multiple roots are allowed; no synthetic Agent or missing timing is invented.

The outline layout encodes depth and parent links. Within each branch, ordering follows
the stored start time and then span ID for ties. Equal timestamps cannot establish the
original call order: the Phase 2B sample has several equal start times. The graph does
not infer planning/fallback chronology from names. Graph positions are not a timeline.
Very large/deep graphs may need zoom/pan or Span list; graph virtualization and broader
performance benchmarks are outside Phase 3A.

## Real sample

The retained Phase 2B trace `2647313fd3a662d1eb2663982fc4031a` contains:

```text
Instrumented agent [OK; recovery event]
├── Plan action [LLM]
├── Lookup [TOOL]
│   └── Read local catalog [RUNTIME, Unset]
├── Optional enrichment [TOOL, Error; ConnectionError]
├── Fallback [TOOL]
└── Final answer [LLM]
```

The displayed sibling order may differ where recorded timestamps tie; all six parent
links remain exact. No trace ID or example records are hardcoded into the UI. If the
sample is absent, generate another using the existing SDK:

```powershell
uv run --project sdk python examples/instrumented_agent.py --create-project
```

## Development and verification

Frontend tooling needs Node `^22.22.2 || ^24.15.0 || >=26.0.0` (matching the test DOM
runtime). From `frontend/`, use the locked dependencies and run:

```powershell
npm ci
npm test
npm run typecheck
npm run build
```

After changing package dependencies, refresh the existing frontend image once:

```powershell
docker compose up --build -d --no-deps frontend
```

This reuses existing configuration and leaves PostgreSQL volumes/data and backend alone.
Vitest/jsdom tests cover construction, API boundaries/paging, states, selection, stale
responses, safe text and token display. Explorer component tests use a graph double;
the graph regression test renders actual React Flow with DOM measurement shims. Real
graph geometry/interactions need browser verification. See `VERIFICATION.md` for results.

No database, SDK, authentication, tenancy, classification, LLM diagnosis or streaming
changes are part of Phase 3A. Review is required before expanding scope.
