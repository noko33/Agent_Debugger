"use client";

import { Background, Controls, Handle, Position, ReactFlow, type Node, type NodeProps } from "@xyflow/react";
import { useMemo } from "react";
import type { SpanTree } from "@/lib/span-tree";
import { durationLabel, type SpanRecord } from "@/lib/traces";
import { StatusBadge } from "./trace-format";

type SpanNode = Node<{
  span: SpanRecord; exception: boolean; warning?: string; onSelect: (id: string) => void;
}, "span">;

function ExecutionNode({ data, selected }: NodeProps<SpanNode>) {
  return <div className={`execution-node operation-${data.span.operation_type} ${data.exception ? "has-exception" : ""} ${selected ? "is-selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    <button type="button" className="nodrag nopan node-content"
      aria-label={`Inspect span ${data.span.name}`} aria-pressed={selected}
      onClick={() => data.onSelect(data.span.span_id)}>
    <span className="node-topline"><span>{data.span.operation_type.toUpperCase()}</span>
      <StatusBadge status={data.span.status_code} /></span>
    <strong>{data.span.name}</strong>
    <span className="node-footline"><span>{durationLabel(data.span)}</span>
      {data.exception && <span className="exception-label">Exception</span>}
      {data.warning && <span title={data.warning}>⚠ Parent link</span>}
    </span>
    </button>
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { span: ExecutionNode };

export function SpanGraph({ tree, selectedId, onSelect }: {
  tree: SpanTree; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const nodes = useMemo<SpanNode[]>(() => tree.nodes.map(node => ({
    id: node.span.span_id, type: "span", position: node.position,
    selected: node.span.span_id === selectedId,
    ariaLabel: `Select span ${node.span.name}`, data: {
      span: node.span, exception: node.exception, warning: node.warning, onSelect,
    },
  })), [tree, selectedId, onSelect]);
  const edges = useMemo(() => tree.nodes.filter(node => node.parentId !== null).map(node => ({
    id: `${node.parentId}-${node.span.span_id}`, source: node.parentId!, target: node.span.span_id,
    type: "smoothstep", style: { stroke: "#64748b", strokeWidth: 1.5 },
  })), [tree]);
  return <div className="graph-canvas" aria-label="Execution graph">
    <ReactFlow<SpanNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes}
      nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} edgesFocusable={false}
      deleteKeyCode={null} colorMode="dark" fitView minZoom={0.05} maxZoom={2}
      fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
    >
      <Background color="#263349" gap={24} />
      <Controls showInteractive={false} />
    </ReactFlow>
  </div>;
}
