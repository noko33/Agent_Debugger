"use client";

import { useMemo, useState } from "react";
import type { TraceDetail } from "@/hooks/use-trace-explorer";
import { buildSpanTree } from "@/lib/span-tree";
import { durationLabel } from "@/lib/traces";
import { SpanDetail } from "./span-detail";
import { SpanGraph } from "./span-graph";
import { AttributeView, StatusBadge } from "./trace-format";
import { buildTimeline, emptyFilters, filterSpanTree, performanceSummary, type SpanFilters } from "@/lib/performance";
import { SpanTimeline } from "./span-timeline";
import { PerformanceSummary } from "./performance-summary";
import { DiagnosticsPanel } from "./diagnostics-panel";
import { useDiagnostics } from "@/hooks/use-diagnostics";
import { relatedSpanIds, type Finding } from "@/lib/diagnostics";

export function TraceWorkspace({ data }: { data: TraceDetail }) {
  const tree = useMemo(() => buildSpanTree(data.spans.spans), [data.spans.spans]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<"graph" | "list" | "timeline">("graph");
  const [filters, setFilters] = useState<SpanFilters>(emptyFilters);
  const diagnostics = useDiagnostics(data.trace.trace_id, data.trace.project_id);
  const [finding, setFinding] = useState<Finding | null>(null);
  const activeFinding = diagnostics.state.status === "ready" && diagnostics.state.data.findings.includes(finding!) ? finding : null;
  const highlightedIds = new Set(activeFinding ? relatedSpanIds(activeFinding) : []);
  function inspectFinding(next: Finding) {
    setFinding(next);
    const id = relatedSpanIds(next).find(id => tree.nodes.some(node => node.span.span_id === id));
    setSelectedId(id ?? null);
  }
  const filtered = useMemo(() => filterSpanTree(tree, filters), [tree, filters]);
  const timeline = useMemo(() => buildTimeline(data.trace, tree), [data.trace, tree]);
  const summary = useMemo(() => performanceSummary(data.trace, tree.nodes.map(node => node.span)), [data.trace, tree]);
  const visibleIds = new Set(filtered.tree.nodes.map(node => node.span.span_id));
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
      <PerformanceSummary summary={summary} partial={data.spans.truncated} total={data.spans.total} onSelect={setSelectedId} />
      <DiagnosticsPanel state={diagnostics.state} onRetry={diagnostics.retry} spans={data.spans.spans}
        selectedId={selectedId} activeFinding={activeFinding} onFinding={inspectFinding} onSelect={setSelectedId} />
      <section className="execution-panel panel" aria-label="Execution tree">
        <div className="section-heading"><h2>Execution tree</h2>
          <div className="view-switch" aria-label="Execution view">
            <button type="button" aria-pressed={view === "graph"} onClick={() => setView("graph")}>Graph</button>
            <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>Span list</button>
            <button type="button" aria-pressed={view === "timeline"} onClick={() => setView("timeline")}>Timeline</button>
          </div>
        </div>
        <div className="span-filters" aria-label="Span filters">
          <label>Span kind<select value={filters.kind} onChange={event => setFilters({ ...filters, kind: event.target.value as SpanFilters["kind"] })}>
            <option value="all">All kinds</option>{["agent", "llm", "tool", "runtime"].map(kind => <option key={kind} value={kind}>{kind.toUpperCase()}</option>)}
          </select></label>
          <label>Status<select value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value as SpanFilters["status"] })}>
            <option value="all">All statuses</option><option value="ok">OK</option><option value="error">Error</option><option value="unset">Unset</option>
          </select></label>
          <label>Name search<input type="search" value={filters.name} onChange={event => setFilters({ ...filters, name: event.target.value })} /></label>
          <button type="button" className="text-button" onClick={() => setFilters(emptyFilters)}>Clear filters</button>
        </div>
        <p className="muted graph-help">{filtered.matches.size} matching spans; {filtered.contextIds.size} ancestors retained as context. Select any span to inspect it.</p>
        {view === "timeline" ? <p className="muted graph-help">Times relative to trace start. Bars may overlap. Minimum-width markers represent zero or unknown durations. Equal timestamps do not establish call order.</p> :
          <p className="muted graph-help">Pan, zoom or fit the graph using the controls.</p>}
        {view === "timeline" && timeline.traceInvalid && <p className="warning">Trace timing is invalid; recorded span times are shown where available.</p>}
        {selectedId && !visibleIds.has(selectedId) && <p role="status" className="warning">Selected span is hidden by filters; its details remain available.</p>}
        {activeFinding && <p className="warning">Highlighted evidence: {activeFinding.summary}. {Array.from(highlightedIds).filter(id => !visibleIds.has(id)).length} referenced spans are hidden or not loaded. Clear filters to reveal loaded spans.</p>}
        {data.spans.truncated && <p role="alert" className="warning">Partial graph: showing {data.spans.spans.length} of {data.spans.total} spans. Unloaded parents may be missing.</p>}
        {!!tree.warnings.length && <details className="warning"><summary>{tree.warnings.length} relationship warning(s)</summary>
          <ul>{tree.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
        </details>}
        {!tree.nodes.length ? <p className="empty-state">No spans recorded for this trace.</p> : !filtered.tree.nodes.length ?
          <p className="empty-state">No spans match these filters.</p> : view === "graph" ?
          <SpanGraph tree={filtered.tree} contextIds={filtered.contextIds} highlightedIds={highlightedIds} selectedId={selectedId} onSelect={setSelectedId} /> : view === "timeline" ?
          <SpanTimeline timeline={timeline} visibleIds={visibleIds} contextIds={filtered.contextIds} highlightedIds={highlightedIds} selectedId={selectedId} onSelect={setSelectedId} /> :
          <ul className="span-list" aria-label="Spans">{filtered.tree.nodes.map(node => <li key={node.span.span_id} className={filtered.contextIds.has(node.span.span_id) ? "filter-context" : ""}>
            <button type="button" aria-pressed={selectedId === node.span.span_id}
              className={highlightedIds.has(node.span.span_id) ? "diagnostic-related" : ""} onClick={() => setSelectedId(node.span.span_id)}
              style={{ paddingLeft: `${Math.min(node.depth, 6) * 16 + 14}px` }}>
              <span><small>{node.span.operation_type.toUpperCase()}{node.exception ? " · Exception" : ""}{filtered.contextIds.has(node.span.span_id) ? " · Context" : ""}</small>
                <strong>{node.span.name}</strong>{highlightedIds.has(node.span.span_id) && <small>Diagnostic evidence</small>}{node.warning && <small>{node.warning}</small>}</span>
              <span><StatusBadge status={node.span.status_code} /><small>{durationLabel(node.span)}</small></span>
            </button>
          </li>)}</ul>}
      </section>
    </div>
    <SpanDetail span={selected} onSelectParent={setSelectedId}
      parentAvailable={!!selected?.parent_span_id && tree.nodes.some(node => node.span.span_id === selected.parent_span_id)} />
  </div>;
}
