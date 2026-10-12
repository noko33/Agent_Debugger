import { expect, it } from "vitest";
import { buildTimeline, emptyFilters, filterSpanTree, performanceSummary, recordedTiming, timestampMicros } from "@/lib/performance";
import { buildSpanTree } from "@/lib/span-tree";
import { spanFixture, traceFixture } from "./fixtures";

const at = (ms: number) => `2026-10-12T00:00:00.${String(ms).padStart(3, "0")}000Z`;
const timed = (id: string, parent: string | null, start: number, end: number) =>
  spanFixture(id, parent, { start_time: at(start), end_time: at(end), duration_ms: end - start });

it("retains microseconds and normalizes timezone offsets before subtraction", () => {
  expect(timestampMicros("2026-10-12T08:00:00.000001+08:00")).toBe(timestampMicros(at(0))! + BigInt(1));
  expect(recordedTiming({ start_time: at(0), end_time: "2026-10-12T00:00:00.000001Z" }).durationMs).toBe(0.001);
  expect(recordedTiming({ start_time: at(0), end_time: at(0) }).durationMs).toBe(0);
});
it("rejects malformed, impossible, timezone-free and backwards timing", () => {
  for (const value of ["garbage", "2026-02-30T00:00:00Z", "2026-10-12T24:00:00Z", "2026-10-12T00:00:00", "2026-10-12T00:00:00+25:00"]) {
    expect(timestampMicros(value)).toBeNull();
  }
  expect(recordedTiming({ start_time: at(200), end_time: at(100) })).toMatchObject({ invalid: true, durationMs: null });
  expect(recordedTiming({ start_time: at(0), end_time: null })).toMatchObject({ invalid: false, durationMs: null });
});
it("places concurrent spans on the same axis, independent of nested outline order", () => {
  const timeline = buildTimeline(traceFixture, buildSpanTree([timed("1", null, 0, 900), timed("2", "1", 100, 700), timed("3", "1", 300, 800)]));
  expect(timeline.rows.map(row => [row.offsetMs, row.durationMs])).toEqual([[0, 900], [100, 600], [300, 500]]);
  expect(timeline.maxMs).toBe(1000);
  expect(timeline.minMs).toBe(0);
});
it("does not assign chronology to tied timestamps and handles a point-only trace", () => {
  const timeline = buildTimeline({ ...traceFixture, end_time: at(0) }, buildSpanTree([timed("2", null, 0, 0), timed("1", null, 0, 0)]));
  expect(timeline.rows.every(row => row.offsetMs === 0 && row.durationMs === 0)).toBe(true);
  expect(timeline.maxMs).toBe(0);
});
it("keeps incomplete and invalid spans inspectable and flags trace/parent boundary violations", () => {
  const spans = [timed("1", null, 100, 500), timed("2", "1", 0, 800),
    spanFixture("3", null, { start_time: at(200), end_time: null, duration_ms: null }),
    spanFixture("4", null, { start_time: "invalid", end_time: null }),
    timed("5", null, 200, 100),
    spanFixture("6", null, { start_time: "2026-10-11T23:59:59.900Z", end_time: at(0) })];
  const timeline = buildTimeline(traceFixture, buildSpanTree(spans));
  const row = (id: string) => timeline.rows.find(row => row.node.span.span_id === spanFixture(id).span_id)!;
  expect(row("2").warnings.join()).toContain("parent bounds");
  expect(row("3").durationMs).toBeNull();
  expect(row("3").offsetMs).toBe(200);
  expect(row("4").offsetMs).toBeNull();
  expect(row("5").durationMs).toBeNull();
  expect(row("6").warnings.join()).toContain("trace bounds");
  expect(timeline.minMs).toBe(-100);
  expect(buildTimeline({ ...traceFixture, start_time: "invalid" }, buildSpanTree(spans)).rows.every(row => row.offsetMs === null)).toBe(true);
});
it("aggregates wall-clock, kinds, errors and recorded tokens without summing elapsed spans", () => {
  const spans = [spanFixture("1", null, { operation_type: "agent" }),
    spanFixture("2", "1", { operation_type: "llm", attributes: { "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 0 } }),
    spanFixture("3", "1", { operation_type: "llm", attributes: { "gen_ai.usage.input_tokens": 15 } }),
    spanFixture("4", "1", { operation_type: "tool", status_code: "error" })];
  const summary = performanceSummary(traceFixture, spans);
  expect(summary).toMatchObject({ spanCount: 4, errorCount: 1, llmCount: 2, toolCount: 1, timing: { durationMs: 1000 } });
  expect(summary.inputTokens).toEqual({ total: BigInt(25), recorded: 2, eligible: 2 });
  expect(summary.outputTokens).toEqual({ total: BigInt(0), recorded: 1, eligible: 2 });
  expect(summary.slowest).toHaveLength(4);
});
it("does not fabricate missing tokens, accept unsafe counts, or rank incomplete timing", () => {
  const spans = [spanFixture("1", null, { operation_type: "llm", end_time: null,
    attributes: { "gen_ai.usage.input_tokens": -1, "gen_ai.usage.output_tokens": "123" } }),
  spanFixture("2", null, { operation_type: "llm", start_time: "invalid",
    attributes: { "gen_ai.usage.input_tokens": Number.MAX_SAFE_INTEGER + 1, "gen_ai.usage.output_tokens": 0.5 } })];
  expect(performanceSummary(traceFixture, spans)).toMatchObject({ inputTokens: { total: null }, outputTokens: { total: null }, slowest: [] });
  expect(performanceSummary(traceFixture, []).inputTokens).toEqual({ total: null, recorded: 0, eligible: 0 });
});
it("ranks durations descending, including genuine zero durations", () => {
  const summary = performanceSummary(traceFixture, [timed("1", null, 0, 0), timed("2", null, 0, 500), timed("3", null, 0, 900)]);
  expect(summary.slowest.map(item => item.durationMs)).toEqual([900, 500, 0]);
});
it("combines kind/status/name filters and retains non-matching ancestors, not siblings", () => {
  const tree = buildSpanTree([spanFixture("1"), spanFixture("2", "1"),
    spanFixture("3", "2", { operation_type: "tool", status_code: "error", name: "Lookup catalog" }), spanFixture("4", "1")]);
  const result = filterSpanTree(tree, { kind: "tool", status: "error", name: " LOOKUP " });
  expect(result.matches.size).toBe(1);
  expect(result.contextIds.size).toBe(2);
  expect(result.tree.nodes.map(node => node.span.span_id)).toEqual(["1", "2", "3"].map(id => spanFixture(id).span_id));
  expect(result.tree.nodes[2].parentId).toBe(spanFixture("2").span_id);
  expect(tree.nodes).toHaveLength(4);
  expect(filterSpanTree(tree, emptyFilters).contextIds.size).toBe(0);
  expect(filterSpanTree(tree, { ...emptyFilters, name: "absent" }).tree.nodes).toHaveLength(0);
});
it("filtering respects the validated missing/cyclic parent graph", () => {
  const tree = buildSpanTree([spanFixture("1", "2"), spanFixture("2", "1"), spanFixture("3", "9")]);
  expect(filterSpanTree(tree, { ...emptyFilters, name: "Span 1" }).tree.nodes.length).toBeGreaterThan(0);
  expect(filterSpanTree(tree, { ...emptyFilters, name: "Span 3" }).tree.nodes).toHaveLength(1);
});
