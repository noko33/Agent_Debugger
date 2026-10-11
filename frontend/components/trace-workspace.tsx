"use client";

import { useMemo, useState } from "react";
import type { TraceDetail } from "@/hooks/use-trace-explorer";
import { buildSpanTree } from "@/lib/span-tree";
import { durationLabel } from "@/lib/traces";
import { SpanDetail } from "./span-detail";
import { SpanGraph } from "./span-graph";
import { AttributeView, StatusBadge } from "./trace-format";

export function TraceWorkspace({ data }: { data: TraceDetail }) {
  const tree = useMemo(() => buildSpanTree(data.spans.spans), [data.spans.spans]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<"graph" | "list">("graph");
  const selected = tree.nodes.find(node => node.span.span_id === selectedId)?.span ?? null;
  const { trace } = data;
  return <div className="workspace">
    <div className="execution-column">
      <section className="trace-summary panel" aria-label="Trace metadata">
        <div className="section-heading"><h2>{trace.name}</h2><StatusBadge status={trace.status_code} /></div>
        <dl className="metadata summary-metadata">
          <dt>Trace ID</dt><dd>{trace.trace_id}</dd>
          <dt>Project ID</dt><dd>{trace.project_id}</dd>
          <dt>Start (UTC)</dt><dd>{trace.start_time}</dd>
          <dt>End (UTC)</dt><dd>{trace.end_time ?? "Not recorded"}</dd>
          <dt>Duration</dt><dd>{durationLabel(trace)}</dd>
          <dt>Spans loaded</dt><dd>{data.spans.spans.length} / {data.spans.total}</dd>
        </dl>
        {trace.status_message && <p>{trace.status_message}</p>}
        <details><summary>Trace attributes</summary><AttributeView attributes={trace.attributes} /></details>
      </section>
      <section className="execution-panel panel" aria-label="Execution tree">
        <div className="section-heading"><h2>Execution tree</h2>
          <div className="view-switch" aria-label="Execution view">
            <button type="button" aria-pressed={view === "graph"} onClick={() => setView("graph")}>Graph</button>
            <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>Span list</button>
          </div>
        </div>
        <p className="muted graph-help">Select a span to inspect it. Pan, zoom or fit the graph using the controls.</p>
        {data.spans.truncated && <p role="alert" className="warning">Partial graph: showing {data.spans.spans.length} of {data.spans.total} spans. Unloaded parents may be missing.</p>}
        {!!tree.warnings.length && <details className="warning"><summary>{tree.warnings.length} relationship warning(s)</summary>
          <ul>{tree.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
        </details>}
        {!tree.nodes.length ? <p className="empty-state">No spans recorded for this trace.</p> : view === "graph" ?
          <SpanGraph tree={tree} selectedId={selectedId} onSelect={setSelectedId} /> :
          <ul className="span-list" aria-label="Spans">{tree.nodes.map(node => <li key={node.span.span_id}>
            <button type="button" aria-pressed={selectedId === node.span.span_id}
              onClick={() => setSelectedId(node.span.span_id)}
              style={{ paddingLeft: `${Math.min(node.depth, 6) * 16 + 14}px` }}>
              <span><small>{node.span.operation_type.toUpperCase()}{node.exception ? " · Exception" : ""}</small>
                <strong>{node.span.name}</strong>{node.warning && <small>{node.warning}</small>}</span>
              <span><StatusBadge status={node.span.status_code} /><small>{durationLabel(node.span)}</small></span>
            </button>
          </li>)}</ul>}
      </section>
    </div>
    <SpanDetail span={selected} onSelectParent={setSelectedId}
      parentAvailable={!!selected?.parent_span_id && tree.nodes.some(node => node.span.span_id === selected.parent_span_id)} />
  </div>;
}
