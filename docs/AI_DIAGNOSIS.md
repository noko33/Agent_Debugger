# Phase 5A: evidence-grounded LLM hypotheses

Implementation and local PostgreSQL/HTTP runtime acceptance passed on 2026-10-13.
External analysis is disabled by default. No live paid provider test has been executed.
Mocked outputs verify contracts and safety checks, not real-world diagnostic accuracy.

Phase 5B optionally reuses this validated analysis once per explicit suggestion request;
recommendation wording comes from vetted templates, not a second model request. See
[FIX_SUGGESTIONS.md](FIX_SUGGESTIONS.md) for scope, consent and review boundaries.

## Architecture and public interfaces

`POST /api/v1/traces/{trace_id}/ai-diagnosis` reads the existing consistent diagnostics
snapshot, releases its database transaction/trace read lock, runs the unchanged Phase 4A
rules, builds a compact context and optionally calls a compatible provider. It validates
the model response before returning hypotheses separately from deterministic findings.
No results are persisted. No model-generated commands, code changes or tools are executed.

- `core/ai_config.py`: typed server-only `AISettings`.
- `schemas/ai_diagnosis.py`: request, context report, hypothesis and execution contracts.
- `services/ai_diagnosis/context.py`: `build_context` and allowlisted facts.
- `provider.py`: `AnalysisProvider.analyze(context) -> ProviderResult`, bounded
  `CompatibleProvider` transport and safe `ProviderFailure` codes.
- `validation.py`: `validate_output`, citation and conservative claim checks.
- `service.py`: `AIDiagnosisService.analyze(snapshot, consent)` and `AnalysisGuard`.
- `api/ai_diagnosis.py`: scoped snapshot dependency, transaction boundary and route.

The endpoint uses the existing optional `?project_id=<uuid>` scope. This is a data filter,
not authentication. Existing span-limit, missing trace, malformed stored record and
database-unavailable responses remain 413, 404, 422 and 503 respectively. An invalid
body/trace identifier is 422. The existing GET diagnostics endpoint remains independent.

## Product privacy policy: 5a.1

External processing requires both operator configuration `AI__ENABLED=true` and explicit
request consent. Opening a trace or GET diagnostics never invokes the provider. A normal
successful trace without findings/operation failures returns `not_needed` without a call.
Consent acknowledges sending structural trace metadata to the configured external service;
the operator must assess that provider's retention, region and privacy terms separately.

Allowlisted external fields:

- Trace/span identifiers, parent identifiers, operation type, recorded status and times,
  recorded durations and recorded execution outcome.
- Rule category/nature/severity and deterministic numeric thresholds/counts. Raw rule
  evidence values are not blindly forwarded, since they may contain arbitrary tool labels.
- Exception presence; a fixed set of common exception type names such as `TimeoutError`,
  `ConnectionError` and `ValueError`. Unknown types are omitted with a flag.
- At most ten exception/recovery event indexes and timestamps per included span, plus
  total event count. Arbitrary event names and event attributes are omitted.
- Valid recorded GenAI input/output token counts. Missing counts are never estimated.

Names, prompts, completions, raw tool arguments/results, error messages, stack traces,
arbitrary attributes, tool/model labels, URLs, credentials and project names are omitted.
This is an allowlist rather than a regex redaction pass over unrestricted text. IDs and
timing still reveal structure; policy 5a.1 is not an anonymization guarantee.
Sanitized facts are returned alongside the context report for review. Deterministic
findings in the local API response retain their existing contract; only the allowlisted
context is sent externally. Do not log request bodies or provider headers.

Model input separates system instructions from `untrusted_trace_evidence`. Stored records
are data, not instructions. Output validation rejects unknown references and common unsafe
claims/secret patterns, but neither prompting nor lexical checks establish semantic truth.
Hypotheses must always be reviewed by a developer.

## Context and budgets

Roots and ancestors are prioritized before relevant children, then remaining records by
recorded timestamp and identifier. Identifier tie-breaking is presentation only; identical
timestamps never imply causal order. Parent links and actual overlapping times are retained.
Missing parents, incomplete intervals, absent semantic outcomes and policy omissions are
reported explicitly. Omitted span/finding counts and `truncated` report budget exclusions.
Finding citations are trimmed to spans whose records actually fit in the context.

Defaults are 100 spans, 50 findings and 24,000 serialized context bytes. Admission also
counts UTF-8 bytes of the complete serialized messages (including system/output schema)
plus a 512-unit framing reserve, against `AI__MAX_INPUT_TOKENS=16000`. This is a
conservative admission bound for byte-based compatible tokenizers, not measured usage or
a provider-independent tokenizer guarantee. A provider with materially different chat
framing/tokenization needs a verified adapter before relying on this bound. Very small
configured budgets reject analysis before any charge. Existing records that all disappear
under truncation also reject external analysis. A genuinely empty trace may still support
a data-quality hypothesis; it never acquires invented spans.

The request enforces `max_tokens` for output; provider-reported counts exceeding either
configured token limit are rejected. Only actual provider-reported usage appears in
`execution.input_tokens/output_tokens`; absent usage stays null. Rejection after a provider
response cannot undo cost already incurred. There is no currency estimate or account-wide
spend cap in this phase.

## Configuration and Windows usage

Use the native backend configuration in `backend/.env` (ignored by Git). Never overwrite
existing database credentials. Copy only the AI entries from `backend/.env.example`.
Keep the key blank and enabled false for local mocked tests. To opt in later, an operator
sets `AI__ENABLED=true`, supplies `AI__API_KEY` privately and selects a supported model.
Neither API bodies nor browser variables can override provider configuration.

| Setting | Default | Purpose |
| --- | --- | --- |
| `AI__BASE_URL` | `https://api.deepseek.com` | HTTPS base; adapter appends `/chat/completions` |
| `AI__MODEL` | `deepseek-chat` | Operator-selected model; current availability must be checked before live use |
| `AI__TIMEOUT_SECONDS` | 10 | Shared attempt deadline and HTTP operation timeout |
| `AI__MAX_RETRIES` | 1 | At most two attempts by default; configurable 0–2 retries |
| `AI__MAX_OUTPUT_TOKENS` | 1000 | Provider output limit, maximum configured 4000 |
| `AI__MAX_INPUT_TOKENS` | 16000 | Conservative input admission units, maximum 64000 |
| `AI__MAX_CONTEXT_BYTES` | 24000 | Serialized context bound, maximum 64000 |
| `AI__MAX_CONTEXT_SPANS/FINDINGS` | 100 / 50 | Maximum records eligible for inclusion |
| `AI__MAX_RESPONSE_BYTES` | 65536 | Streamed response byte bound |
| `AI__COOLDOWN_SECONDS` | 30 | Per-trace/process repeat guard |
| `AI__MAX_CONCURRENT` | 2 | Per-process external analysis concurrency |

Compatible-provider JSON mode uses `response_format={"type":"json_object"}`, a JSON
schema in the system message and local Pydantic validation. See the provider's
[chat API](https://api-docs.deepseek.com/api/create-chat-completion/) and
[JSON-mode guide](https://api-docs.deepseek.com/guides/json_mode/). Live compatibility
of the configured model has not been exercised. An OpenAI-compatible base may include
`/v1`; redirects are disabled and keys never follow a redirect.

HTTP 429/500/502/503/504 and connection errors receive bounded retries. Ambiguous
read/write timeouts, authentication failures, refusals, malformed output and truncation
are not retried. Error responses contain safe codes, never provider bodies or exception
strings. HTTPX operation timeouts plus deadline checks are bounded safeguards rather
than a hard process termination timer. No process proxy settings are changed.

The cooldown is bounded to 1,024 recent keys and also guards in-flight duplicates.
It is per process and resets on restart; it is not a distributed limiter or authorization.
There is no background worker, automatic paid call or frontend AI button.
Compose configuration is unchanged and does not forward native AI environment variables;
container analysis stays disabled by default. Opt-in Compose wiring needs separate review.

For an already migrated native backend:

```powershell
cd D:\Agent_Debugger\backend
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The request below does not authorize external processing and remains safe with analysis
enabled. Replace the trace ID with an existing stored ID:

```powershell
$traceId = '2647313fd3a662d1eb2663982fc4031a'
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/api/v1/traces/$traceId/ai-diagnosis" `
  -ContentType 'application/json' `
  -Body '{"consent_to_external_processing":false}'
```

With operator opt-in and approved external processing, a caller can explicitly send
`{"consent_to_external_processing":true}`. This may incur provider charges. No live
request with that enabled configuration was made during Phase 5A verification.

## Response schema and illustrative mocked hypothesis

The response contains `schema_version`, `trace_id`, unchanged `deterministic` findings
and outcome, `context`, a sanitized `evidence` registry, `hypotheses`, `execution` and
global `limitations`. `execution.status` is one of `disabled`, `consent_required`,
`not_needed`, `rate_limited`, `failed`, or `completed`. Operational provider failures
return HTTP 200 with `failed`, an error code and deterministic results still present.
This makes it possible to display recorded facts even when external analysis fails.

The following is an illustrative **scripted test output**, not a real model diagnosis:

```json
{
  "hypothesis_id": "h1",
  "title": "Recorded operation pattern",
  "claim_kind": "hypothesis",
  "cause_type": "tool_operation_failure",
  "possible_root_cause": "The recorded pattern may explain this operation; the underlying external condition remains unknown.",
  "supporting_evidence": ["f:0"],
  "span_ids": ["0000000000000002"],
  "missing_evidence": ["Underlying service response and semantic task result are unavailable."],
  "limitations": ["Recorded correlation does not establish causality."],
  "support_status": "supported"
}
```

Here `f:0` must exist in the returned registry, identify a `TOOL_EXECUTION_ERROR` observed
failure and cover that real included span. `supported` refers to the observed pattern,
not a confirmed underlying service failure. Heuristic/data-quality findings can be at
most `weakly_supported`; `cannot_determine` uses `cannot_be_determined`.
At most five hypotheses are accepted; missing evidence and limitations are mandatory.
Unknown/omitted span references, mismatched rule categories, duplicate hypothesis IDs,
extra fields, confidence percentages, confirmation language and obvious contradictions
of recorded recovery are rejected. The entire model response is rejected atomically.
Citation validity and category matching cannot prove that free-text causal reasoning
is correct. Conservative lexical guards may also reject benign wording.

## Evaluation and acceptance

Twenty-two labeled traces cover all six rule categories, explicit recovery, later
fallback without proof of recovery, missing parents/spans, incomplete timing, concurrency,
successful operations, unknown status and conflicting status. Scripted responses exercise
the same contract in database-free HTTP tests and dedicated PostgreSQL tests. Separate
tests cover malicious names/attributes/error types, invalid JSON/references/claims,
consent, budgets, concurrency/cooldown, secret-safe errors and HTTP transport failures.
These are deterministic safety/regression fixtures, not a benchmark of model accuracy.

See [actual verification results](VERIFICATION.md). Current outcome: 296 backend tests
(including 96 real PostgreSQL tests) and 59 SDK tests pass, with zero failures or skips.
All 29 original Phase 5A PostgreSQL checks and three added real TCP HTTP/PostgreSQL
checks passed. The existing container API also retrieved the retained recovered trace
and kept external analysis disabled. This supersedes the earlier Docker blocker.
No Phase 5B repair, Phase 5C replay, automatic changes or push to GitHub is included.
