# Phase 3A/3B/4B: Trace Explorer

Open `http://localhost:3000/` with the existing Compose services running. Select an
execution from **Traces**. The UI uses only existing read endpoints:

- `GET /api/v1/traces?limit=20&offset=...`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/traces/{trace_id}/spans?limit=500&offset=...`
- `GET /api/v1/traces/{trace_id}/diagnostics?project_id=...`

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

No database, SDK, authentication, tenancy, LLM diagnosis or streaming changes are part
of the Explorer. Phase 4B displays existing Phase 4A classifications without new rules.

## Timeline and performance analysis (Phase 3B)

Switch to **Timeline** beside Graph / Span list. Each row is a recorded span, indented
using the same validated parent tree. Its horizontal bar is positioned by actual start
offset from the trace start, with width derived from end minus start. Concurrent spans
overlap on the same axis even when their rows are separated by nested children. Row
ordering follows the existing outline; tied timestamps never imply execution order.

Timestamp subtraction uses integer microseconds (including timezone offsets) before
conversion to milliseconds; it does not use truncated JavaScript epoch milliseconds.
Labels show up to three decimal places in milliseconds, matching API precision. The
axis includes the trace duration and any span outside its bounds. Negative offsets and
spans outside trace/parent bounds are retained and warned about, not silently clamped.
Filtering never rescales the axis. Zero-length spans use a minimum three-pixel marker;
missing ends use a hollow marker at the recorded start, without inventing a duration.
Invalid timing cannot create a negative bar. An invalid/missing trace start prevents
relative positioning; rows remain selectable with an explicit warning. The API response
guard also rejects malformed timestamp strings before they reach the workspace.

Agent/LLM/Tool/Runtime colors match the graph; errors/exceptions use rose highlights.
Click or press Enter on a row to select it. Graph, Timeline, Span list, slowest-span
buttons and Inspect parent all use the original workspace selection. Switching views
preserves it. Narrow screens use horizontal scrolling; this is a fixed-scale waterfall,
with no timeline zoom, virtualization or live updates.

**Performance summary** always describes all loaded unique spans, independent of filters:

- Trace wall-clock duration is recorded end minus start, never a sum of child spans.
- Loaded / backend-total counts expose partial loading; error count means status `error`,
  while LLM/Tool counts use `operation_type`.
- Input/output totals use recorded non-negative safe integer GenAI token attributes on
  LLM spans only, with separate recorded / eligible coverage. A recorded zero counts;
  absent, fractional, negative, string or unsafe values do not. No readings means
  **Not recorded**, not zero. Totals use integer arithmetic to avoid overflow on addition.
  There is no deduplication across a user's separately instrumented nested LLM calls.
- The five slowest completed, valid spans are ranked by inclusive duration, including
  parent spans. They do not represent exclusive work, a critical path, or additive elapsed
  time. Ties use span IDs for deterministic display only. Incomplete spans are excluded.

**Span filters** combine operation kind, status and case-insensitive name substring.
The same filtered tree feeds Graph, Timeline and Span list. Loaded ancestors of matches
remain as dimmed **Context** rows/nodes; siblings and descendants that do not match are
hidden. Original parent IDs and warnings remain intact. If a selected span is hidden,
its details stay open with a notice. Parent/slowest navigation may select a hidden span;
Clear filters reveals it without losing the selection. Summaries remain unfiltered.

The bounded 5,000-span loader is unchanged. Partial statistics are labeled, and missing
parents cannot be reconstructed. Unit tests cover malformed timestamps/relationships;
live verification uses valid records through the existing API, including incomplete
spans. See the Phase 3B section of `VERIFICATION.md` for exact test and browser results.

## Interactive diagnostics and evidence navigation (Phase 4B)

Select a trace to fetch the existing deterministic diagnostics response. The panel is
independent of span loading: a diagnostic request failure does not prevent browsing
spans. Loading, safe HTTP/contract errors, Retry diagnostics and Reload diagnostics are
explicit. Requests reuse the API client's ten-second timeout and cancellation. Responses
are validated for schema version, trace/project identity, outcome, findings, evidence
and parameters. Trace switches abort previous requests and discard late resolutions or
rejections. Reload trace refreshes spans and diagnostics; Reload diagnostics only refreshes
analysis. There is no polling or persisted diagnosis.

Start with **Recorded execution**. Recovered, Unrecovered, Unknown and Recorded success
map directly to Phase 4A's response values. The backend explanation and expandable outcome
evidence clarify the classification. No child error, later successful operation, finding
count or frontend status inference changes this outcome. Recorded success/recovery does
not prove semantic task correctness.

Findings are grouped by returned category. Each card shows severity, nature (Observed
failure, Heuristic warning, Structural / data quality), summary, explanation, stable rule
ID and associated Span IDs. Expand **Supporting evidence and rule parameters** to inspect
recorded signals, event indices/timestamps and the actual rule thresholds. Expand coverage
to see effective server parameters. Slow-operation and possible-retry findings remain
warnings; structural findings describe data quality, not automatically Agent failure.

To investigate:

1. Click a finding title. The first loaded associated span is selected; every referenced
   span is highlighted in amber in Graph, Timeline and Span list. This selection is the
   same workspace state used by direct node/row clicks and Inspect parent.
2. For a multi-span finding, click each **Inspect [name] · [Span ID]** button. Supporting
   evidence also links its span. Switching Graph/Timeline preserves selection and the
   existing detail panel shows recorded attributes and exception events as escaped text.
3. Use kind/status/name filters as before. A hidden selected span keeps its details and
   an explicit notice. Clear filters reveals loaded evidence spans without losing the
   selection. Missing/unloaded references show IDs and a reload/limit explanation with
   disabled navigation; if none are loaded, selecting the finding clears unrelated details.
   Trace-level findings have an explicit no-span-reference message.
4. Diagnostic severity/category filters combine and affect cards only. Counts always
   describe all returned findings. Filtering out an active card retains the selected
   evidence and its named highlight notice; span filters remain independent. Clear
   diagnostic filters restores cards. No aggregate risk score is computed.

The retained seven-span example shows Recovered plus one tool-error finding. Selecting
it locates Optional enrichment (`518cc84e627bd3df`), whose detail panel contains the
ConnectionError exception event. The existing rule engine requires the root's explicit
recovery evidence; the UI does not infer recovery from the Fallback span.

Known limits: the diagnostics snapshot and paged span reads are independent; newly
ingested spans may require Reload trace. The 5,000-span graph limit remains. Analysis
HTTP 413/422/503 errors have no fabricated partial cards. An old response schema or unknown
enum fails safely instead of inventing labels. Existing detail metadata may be sensitive;
diagnostic evidence retains the backend allowlist and no new capture/export is introduced.
Large finding lists are not virtualized. Local services still have no authentication;
project filtering is not access control. No LLM diagnosis, suggested repair or new rules.

See the Phase 4B section of `VERIFICATION.md` for automated and real browser results.
# Phase 5B repair proposals

The existing Diagnostics Panel includes **Request suggestions**. It makes no request
until clicked. Default proposals are deterministic and require no external model.
Optional AI requires its checkbox plus separate external-processing consent; operator
configuration still controls availability, and the response shows disabled/failed status.
No suggestion is an applied or verified repair.

Each proposal displays its category/basis, change and expected observable validation target.
Expand **Evidence, risks and validation** for references, preconditions, missing evidence,
trade-offs and the concrete manual plan. **Inspect suggested span** uses the same graph/
timeline/detail selection as existing navigation. Unloaded references are disabled; filters
can hide a selected span while details remain available. Trace changes abort requests and
ignore late responses. Suggestions and graph may be separate read snapshots.

There is no Apply/Fix/Deploy action. See `FIX_SUGGESTIONS.md` for evidence gates and limits.
Actual browser interaction acceptance passed during Final QA on 2026-10-13: explicit
request/rendering, consent gating and suggested-span navigation on real persisted traces.
Graph/timeline/details selection, filtering, trace switching, recorded outcomes and
concurrent timing were also exercised. A controlled backend stop showed the connection-error
message; Retry restored the persisted trace after restart. Global empty-list and deliberately
injected HTTP status-code browser scenarios remain deferred; unit tests are separate evidence. See `VERIFICATION.md`
and [the usability review](UI_USABILITY_REVIEW.md). No UI redesign was performed.
