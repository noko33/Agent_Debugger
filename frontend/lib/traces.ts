/** Read contracts mirror backend/app/schemas/traces.py; no write API here. */
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type Attributes = Record<string, JsonValue>;
export type StatusCode = "unset" | "ok" | "error";
export type OperationType = "agent" | "llm" | "tool" | "runtime";
export interface TraceRecord {
  trace_id: string;
  project_id: string;
  name: string;
  start_time: string;
  end_time: string | null;
  duration_ms: number | null;
  status_code: StatusCode;
  status_message: string | null;
  attributes: Attributes;
}
export interface SpanEvent {
  name: string;
  timestamp: string;
  attributes: Attributes;
}
export interface SpanRecord extends Omit<TraceRecord, "project_id"> {
  span_id: string;
  parent_span_id: string | null;
  operation_type: OperationType;
  span_kind: "internal" | "server" | "client" | "producer" | "consumer";
  events: SpanEvent[];
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export const statusLabel = (status: StatusCode): string =>
  ({ ok: "OK", error: "Error", unset: "Unset" })[status];

export function durationLabel(record: Pick<TraceRecord, "duration_ms" | "end_time">): string {
  // The API derives this from recorded UTC timestamps with microsecond precision.
  // Date.parse would discard sub-millisecond precision; never replace null with zero.
  if (record.end_time === null || record.duration_ms === null) return "Not recorded";
  if (!Number.isFinite(record.duration_ms) || record.duration_ms < 0) return "Invalid timing";
  const ms = record.duration_ms;
  if (ms === 0) return "0 ms";
  if (ms < 0.001) return "<0.001 ms";
  return `${ms.toLocaleString("en-US", { maximumFractionDigits: 3 })} ms`;
}

export function hasException(span: SpanRecord): boolean {
  return span.events.some(event => event.name === "exception" || "exception.type" in event.attributes);
}
