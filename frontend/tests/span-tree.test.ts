import { describe, expect, it } from "vitest";
import { buildSpanTree } from "@/lib/span-tree";
import { durationLabel, hasException, statusLabel } from "@/lib/traces";
import { spanFixture } from "./fixtures";

describe("span tree", () => {
  it("builds actual parent-child links despite out-of-order rows", () => {
    const tree = buildSpanTree([spanFixture("3", "2"), spanFixture("1"), spanFixture("2", "1")]);
    expect(tree.nodes.map(node => node.span.span_id.at(-1))).toEqual(["1", "2", "3"]);
    expect(tree.nodes.map(node => node.depth)).toEqual([0, 1, 2]);
    expect(tree.nodes[2].parentId).toBe(spanFixture("2").span_id);
    expect(tree.warnings).toEqual([]);
    expect(new Set(tree.nodes.map(node => `${node.position.x}:${node.position.y}`)).size).toBe(3);
  });
  it("keeps missing parents separate without inventing a parent", () => {
    const tree = buildSpanTree([spanFixture("1", "9"), spanFixture("2")]);
    expect(tree.nodes.every(node => node.parentId === null)).toBe(true);
    expect(tree.nodes[0].span.parent_span_id).toBe(spanFixture("9").span_id);
    expect(tree.warnings[0]).toContain("Parent not loaded");
  });
  it("detaches self-parent and cyclic links without losing spans", () => {
    const tree = buildSpanTree([spanFixture("1", "1"), spanFixture("2", "3"), spanFixture("3", "2")]);
    expect(tree.nodes).toHaveLength(3);
    expect(tree.warnings).toHaveLength(2);
    expect(tree.nodes.filter(node => node.parentId !== null)).toHaveLength(1);
  });
  it("deduplicates IDs and rejects cross-trace parent links", () => {
    const tree = buildSpanTree([spanFixture("1"), spanFixture("1"),
      spanFixture("2", "1", { trace_id: "2".repeat(32) })]);
    expect(tree.nodes).toHaveLength(2);
    expect(tree.warnings).toHaveLength(2);
    expect(tree.nodes[1].parentId).toBeNull();
  });
  it("handles a deep chain iteratively", () => {
    const spans = Array.from({ length: 5000 }, (_, index) => spanFixture(
      (index + 1).toString(16), index === 0 ? null : index.toString(16)));
    const tree = buildSpanTree(spans.reverse());
    expect(tree.nodes).toHaveLength(5000);
    expect(tree.nodes.at(-1)?.depth).toBe(4999);
  });
  it("handles an empty execution", () => { expect(buildSpanTree([])).toEqual({ nodes: [], warnings: [] }); });
});

describe("recorded display metadata", () => {
  it("keeps unset distinct from success and recognizes exception events", () => {
    expect(statusLabel("unset")).toBe("Unset");
    expect(statusLabel("error")).toBe("Error");
    expect(statusLabel("ok")).toBe("OK");
    expect(hasException(spanFixture("1", null, { events: [{ name: "exception",
      timestamp: "2026-10-12T00:00:00Z", attributes: { "exception.type": "ValueError" } }] }))).toBe(true);
  });
  it("preserves sub-ms recorded duration and zero; does not fabricate unfinished duration", () => {
    expect(durationLabel({ end_time: "recorded", duration_ms: .003 })).toBe("0.003 ms");
    expect(durationLabel({ end_time: "recorded", duration_ms: 0 })).toBe("0 ms");
    expect(durationLabel({ end_time: null, duration_ms: 12 })).toBe("Not recorded");
    expect(durationLabel({ end_time: "recorded", duration_ms: null })).toBe("Not recorded");
    expect(durationLabel({ end_time: "recorded", duration_ms: -1 })).toBe("Invalid timing");
  });
});
