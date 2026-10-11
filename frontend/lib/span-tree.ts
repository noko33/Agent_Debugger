import { hasException, type SpanRecord } from "./traces";

export interface SpanTreeNode {
  span: SpanRecord;
  parentId: string | null;
  depth: number;
  position: { x: number; y: number };
  exception: boolean;
  warning?: string;
}
export interface SpanTree { nodes: SpanTreeNode[]; warnings: string[] }

export function buildSpanTree(spans: SpanRecord[]): SpanTree {
  const warnings: string[] = [];
  const byId = new Map<string, SpanTreeNode>();
  for (const span of spans) {
    if (byId.has(span.span_id)) {
      warnings.push(`Duplicate span ${span.span_id} omitted.`);
      continue;
    }
    byId.set(span.span_id, {
      span, parentId: span.parent_span_id, depth: 0, position: { x: 0, y: 0 },
      exception: hasException(span),
    });
  }
  function detach(node: SpanTreeNode, reason: string) {
    node.parentId = null;
    node.warning = reason;
    warnings.push(`${node.span.name}: ${reason}`);
  }
  for (const node of byId.values()) {
    if (node.parentId === null) continue;
    const parent = byId.get(node.parentId);
    if (node.parentId === node.span.span_id) detach(node, "Self-parent relationship ignored.");
    else if (!parent) detach(node, "Parent not loaded; displayed as a separate root.");
    else if (parent.span.trace_id !== node.span.trace_id) detach(node, "Parent belongs to another trace.");
  }
  // Each node has at most one parent. Iterative walks detect cycles in O(n), without
  // recursive call-stack failures on deeply nested or malformed imported data.
  const done = new Set<string>();
  for (const start of byId.keys()) {
    const path = new Set<string>();
    let id: string | null = start;
    while (id !== null && !done.has(id)) {
      const node: SpanTreeNode = byId.get(id)!;
      if (path.has(id)) { detach(node, "Cycle detected; parent link ignored."); break; }
      path.add(id);
      id = node.parentId;
    }
    for (const visited of path) done.add(visited);
  }
  const ordered = [...byId.values()].sort((a, b) =>
    a.span.start_time.localeCompare(b.span.start_time) || a.span.span_id.localeCompare(b.span.span_id));
  const children = new Map<string | null, SpanTreeNode[]>();
  for (const node of ordered) {
    const siblings = children.get(node.parentId) ?? [];
    siblings.push(node);
    children.set(node.parentId, siblings);
  }
  const stack = (children.get(null) ?? []).slice().reverse();
  const nodes: SpanTreeNode[] = [];
  // A compact top-to-bottom outline layout keeps sibling branches readable without
  // an additional graph-layout dependency. Edges still represent only recorded links.
  while (stack.length) {
    const node = stack.pop()!;
    node.position = { x: node.depth * 300, y: nodes.length * 115 };
    nodes.push(node);
    for (const child of (children.get(node.span.span_id) ?? []).slice().reverse()) {
      child.depth = node.depth + 1;
      stack.push(child);
    }
  }
  return { nodes, warnings };
}
