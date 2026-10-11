import type { Page, SpanRecord, TraceRecord } from "./traces";

export type HealthResponse = {
  status: "ok";
  service: "agent-debugger-api";
};

export const apiBaseUrl = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/health`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${response.status}`);
  }
  const data: unknown = await response.json();
  if (
    typeof data !== "object" || data === null ||
    !("status" in data) || data.status !== "ok" ||
    !("service" in data) || data.service !== "agent-debugger-api"
  ) {
    throw new Error("Backend returned an unexpected health response");
  }
  return { status: "ok", service: "agent-debugger-api" };
}

// Validate response boundaries without adding a second schema library.
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const timestamp = (value: unknown): value is string =>
  typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const identifier = (value: unknown, length: number): value is string =>
  typeof value === "string" && value.length === length && /^[0-9a-f]+$/.test(value) && /[1-9a-f]/.test(value);
const timed = (value: unknown): value is Record<string, unknown> =>
  object(value) && typeof value.name === "string" && timestamp(value.start_time) &&
  (value.end_time === null || timestamp(value.end_time)) &&
  (value.duration_ms === null || (typeof value.duration_ms === "number" && Number.isFinite(value.duration_ms))) &&
  ["ok", "unset", "error"].includes(String(value.status_code)) &&
  (value.status_message === null || typeof value.status_message === "string") && object(value.attributes);

function isTrace(value: unknown): value is TraceRecord {
  return timed(value) && identifier(value.trace_id, 32) && typeof value.project_id === "string";
}
function isSpan(value: unknown): value is SpanRecord {
  return timed(value) && identifier(value.trace_id, 32) && identifier(value.span_id, 16) &&
    (value.parent_span_id === null || identifier(value.parent_span_id, 16)) &&
    ["agent", "llm", "tool", "runtime"].includes(String(value.operation_type)) &&
    ["internal", "server", "client", "producer", "consumer"].includes(String(value.span_kind)) &&
    Array.isArray(value.events) && value.events.every(event =>
      object(event) && typeof event.name === "string" && timestamp(event.timestamp) && object(event.attributes));
}

async function request(path: string, signal?: AbortSignal): Promise<unknown> {
  const timeout = AbortSignal.timeout(10_000);
  const response = await fetch(`${apiBaseUrl}/api/v1${path}`, {
    headers: { Accept: "application/json" }, cache: "no-store",
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  // Do not display raw backend response bodies or transport errors in the UI.
  if (!response.ok) throw new Error(`Trace API returned HTTP ${response.status}.`);
  return response.json();
}
function page<T>(value: unknown, guard: (item: unknown) => item is T): Page<T> {
  if (!object(value) || !Array.isArray(value.items) || !value.items.every(guard) ||
    !Number.isInteger(value.total) || Number(value.total) < 0 ||
    !Number.isInteger(value.limit) || Number(value.limit) < 1 ||
    !Number.isInteger(value.offset) || Number(value.offset) < 0) {
    throw new Error("Trace API returned an unexpected response.");
  }
  return value as unknown as Page<T>;
}

export async function getTraces(offset = 0, signal?: AbortSignal): Promise<Page<TraceRecord>> {
  return page(await request(`/traces?limit=20&offset=${offset}`, signal), isTrace);
}
export async function getTrace(traceId: string, signal?: AbortSignal): Promise<TraceRecord> {
  if (!identifier(traceId, 32)) throw new Error("Invalid trace ID.");
  const value = await request(`/traces/${traceId}`, signal);
  if (!isTrace(value) || value.trace_id !== traceId) throw new Error("Trace API returned an unexpected trace.");
  return value;
}

export interface LoadedSpans { spans: SpanRecord[]; total: number; truncated: boolean }
export const MAX_GRAPH_SPANS = 5000;
export async function getSpans(traceId: string, signal?: AbortSignal): Promise<LoadedSpans> {
  if (!identifier(traceId, 32)) throw new Error("Invalid trace ID.");
  const spans: SpanRecord[] = [];
  let total = 0;
  // Trace records can accumulate more than a single ingestion batch. Load all pages
  // up to an explicit UI bound; never silently show a complete-looking partial tree.
  do {
    const result = page(await request(
      `/traces/${traceId}/spans?limit=500&offset=${spans.length}`, signal), isSpan);
    if (result.items.some(span => span.trace_id !== traceId)) throw new Error("Span trace ID mismatch.");
    total = result.total;
    if (result.items.length === 0) break;
    spans.push(...result.items);
  } while (spans.length < total && spans.length < MAX_GRAPH_SPANS);
  return { spans, total, truncated: spans.length < total };
}

export function apiErrorMessage(error: unknown): string {
  return error instanceof Error && error.message.startsWith("Trace API returned")
    ? error.message : "Could not load trace data. Check the backend connection and try again.";
}
