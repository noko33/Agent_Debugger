import type { SpanTree } from "./span-tree";
import type { OperationType, SpanRecord, StatusCode, TraceRecord } from "./traces";

/** PostgreSQL/API timestamps have microsecond precision. Subtract integers before
 * converting to milliseconds so epoch-sized JS numbers don't erase that precision. */
export function timestampMicros(value: string | null): bigint | null {
  if (!value) return null;
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return null;
  const [, date, hours, minutes, seconds, fraction = "", zone] = match;
  const calendar = Date.parse(`${date}T00:00:00Z`);
  if (!Number.isFinite(calendar) || new Date(calendar).toISOString().slice(0, 10) !== date ||
    +hours > 23 || +minutes > 59 || +seconds > 59 ||
    (zone !== "Z" && (+zone.slice(1, 3) > 23 || +zone.slice(4) > 59))) return null;
  const millis = Date.parse(`${date}T${hours}:${minutes}:${seconds}${zone}`);
  return Number.isFinite(millis) ? BigInt(millis) * BigInt(1000) + BigInt(fraction.padEnd(6, "0")) : null;
}

export function recordedTiming(record: Pick<TraceRecord, "start_time" | "end_time">) {
  const start = timestampMicros(record.start_time);
  const end = timestampMicros(record.end_time);
  const invalid = start === null || (record.end_time !== null && (end === null || end < start));
  return { start, end, invalid, durationMs: !invalid && start !== null && end !== null ? Number(end - start) / 1000 : null };
}

export const milliseconds = (value: number | null): string => value === null ? "Not recorded" :
  `${value.toLocaleString("en-US", { maximumFractionDigits: 3 })} ms`;

export function buildTimeline(trace: TraceRecord, tree: SpanTree) {
  const traceTiming = recordedTiming(trace);
  const origin = traceTiming.start;
  let minMs = 0;
  let maxMs = traceTiming.durationMs ?? 0;
  const timings = new Map(tree.nodes.map(node => [node.span.span_id, recordedTiming(node.span)]));
  const rows = tree.nodes.map(node => {
    const timing = timings.get(node.span.span_id)!;
    const offsetMs = origin !== null && timing.start !== null ? Number(timing.start - origin) / 1000 : null;
    const endOffset = offsetMs !== null && timing.durationMs !== null ? offsetMs + timing.durationMs : offsetMs;
    if (offsetMs !== null) { minMs = Math.min(minMs, offsetMs); maxMs = Math.max(maxMs, endOffset!); }
    const warnings: string[] = [];
    if (timing.invalid) warnings.push("Invalid timing; duration unavailable.");
    else if (timing.end === null) warnings.push("End time not recorded; marker shows start only.");
    if (origin === null) warnings.push("Trace start unavailable; relative position unavailable.");
    if (origin !== null && timing.start !== null && (timing.start < origin ||
      (!traceTiming.invalid && traceTiming.end !== null && (timing.end ?? timing.start) > traceTiming.end))) {
      warnings.push("Outside recorded trace bounds.");
    }
    const parent = node.parentId ? timings.get(node.parentId) : undefined;
    if (parent && !parent.invalid && !timing.invalid && parent.start !== null && timing.start !== null &&
      (timing.start < parent.start || (parent.end !== null && (timing.end ?? timing.start) > parent.end))) {
      warnings.push("Outside recorded parent bounds.");
    }
    return { node, offsetMs, durationMs: timing.durationMs, warnings };
  });
  return { rows, minMs, maxMs, originAvailable: origin !== null, traceInvalid: traceTiming.invalid };
}

export function performanceSummary(trace: TraceRecord, spans: SpanRecord[]) {
  const llm = spans.filter(span => span.operation_type === "llm");
  function tokens(key: string) {
    let total = BigInt(0);
    let recorded = 0;
    for (const span of llm) {
      const value = span.attributes[key];
      if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
        total += BigInt(value); recorded++;
      }
    }
    return { total: recorded ? total : null, recorded, eligible: llm.length };
  }
  return {
    timing: recordedTiming(trace), spanCount: spans.length,
    errorCount: spans.filter(span => span.status_code === "error").length,
    llmCount: llm.length, toolCount: spans.filter(span => span.operation_type === "tool").length,
    inputTokens: tokens("gen_ai.usage.input_tokens"), outputTokens: tokens("gen_ai.usage.output_tokens"),
    slowest: spans.map(span => ({ span, durationMs: recordedTiming(span).durationMs }))
      .filter((item): item is { span: SpanRecord; durationMs: number } => item.durationMs !== null)
      .sort((a, b) => b.durationMs - a.durationMs || a.span.span_id.localeCompare(b.span.span_id)).slice(0, 5),
  };
}

export interface SpanFilters { kind: OperationType | "all"; status: StatusCode | "all"; name: string }
export const emptyFilters: SpanFilters = { kind: "all", status: "all", name: "" };

export function filterSpanTree(tree: SpanTree, filters: SpanFilters) {
  const matches = new Set(tree.nodes.filter(({ span }) =>
    (filters.kind === "all" || span.operation_type === filters.kind) &&
    (filters.status === "all" || span.status_code === filters.status) &&
    span.name.toLowerCase().includes(filters.name.trim().toLowerCase())).map(node => node.span.span_id));
  const byId = new Map(tree.nodes.map(node => [node.span.span_id, node]));
  const visible = new Set(matches);
  // Keep all loaded ancestors as context; the validated tree has no cycles.
  for (const id of matches) {
    let parent = byId.get(id)?.parentId;
    while (parent && !visible.has(parent)) { visible.add(parent); parent = byId.get(parent)?.parentId; }
  }
  const nodes = tree.nodes.filter(node => visible.has(node.span.span_id)).map((node, index) =>
    ({ ...node, position: { ...node.position, y: index * 115 } }));
  return { tree: { ...tree, nodes }, matches, contextIds: new Set([...visible].filter(id => !matches.has(id))) };
}
