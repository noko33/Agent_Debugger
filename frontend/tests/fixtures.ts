import type { SpanRecord, TraceRecord } from "@/lib/traces";

export const traceFixture: TraceRecord = {
  name: "Test agent", trace_id: "1".repeat(32), project_id: "11111111-1111-4111-8111-111111111111",
  start_time: "2026-10-12T00:00:00.000000+00:00", end_time: "2026-10-12T00:00:01+00:00",
  duration_ms: 1000, status_code: "ok", status_message: null, attributes: {},
};
export function spanFixture(id: string, parent: string | null = null, extra: Partial<SpanRecord> = {}): SpanRecord {
  const { project_id: _project, ...timed } = traceFixture;
  return { ...timed, name: `Span ${id}`, span_id: id.padStart(16, "0"),
    parent_span_id: parent?.padStart(16, "0") ?? null,
    operation_type: "runtime", span_kind: "internal", events: [], ...extra };
}
export const pageFixture = <T>(items: T[], total = items.length, limit = 20, offset = 0) =>
  ({ items, total, limit, offset });
