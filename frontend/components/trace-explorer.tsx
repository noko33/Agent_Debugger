"use client";

import { useTraceExplorer } from "@/hooks/use-trace-explorer";
import { durationLabel } from "@/lib/traces";
import { StatusBadge } from "./trace-format";
import { TraceWorkspace } from "./trace-workspace";

export function TraceExplorer() {
  const explorer = useTraceExplorer();
  const { list, detail } = explorer;
  return <div className="explorer">
    <aside className="trace-sidebar panel" aria-label="Trace browser">
      <div className="section-heading"><h2>Traces</h2>
        <button type="button" className="text-button" onClick={explorer.refreshList}>Refresh list</button>
      </div>
      <p className="muted">Stored executions · newest first</p>
      {list.status === "loading" && <p role="status" className="empty-state">Loading traces…</p>}
      {list.status === "error" && <div role="alert" className="error-state"><p>{list.message}</p>
        <button type="button" onClick={explorer.refreshList}>Retry traces</button></div>}
      {list.status === "ready" && <>
        {!list.data.items.length && <p className="empty-state">No traces found. Export an execution using the Python SDK to get started.</p>}
        <ul className="trace-list">{list.data.items.map(trace => <li key={trace.trace_id}>
          <button type="button" aria-pressed={explorer.selectedTrace === trace.trace_id}
            onClick={() => explorer.selectTrace(trace.trace_id)}>
            <span className="section-heading"><strong>{trace.name}</strong><StatusBadge status={trace.status_code} /></span>
            <time>{trace.start_time}</time><span className="trace-id">{trace.trace_id}</span>
            <span>{durationLabel(trace)}</span>
          </button>
        </li>)}</ul>
        <div className="pagination" aria-label="Trace pagination">
          <button type="button" disabled={explorer.offset === 0}
            onClick={() => explorer.setOffset(Math.max(0, explorer.offset - 20))}>Previous</button>
          <span>{list.data.total ? `${explorer.offset + 1}–${explorer.offset + list.data.items.length}` : "0"} / {list.data.total}</span>
          <button type="button" disabled={explorer.offset + 20 >= list.data.total}
            onClick={() => explorer.setOffset(explorer.offset + 20)}>Next</button>
        </div>
      </>}
    </aside>
    <div className="trace-content">
      {!detail && <section className="panel empty-state"><h2>Explore an execution</h2>
        <p>Select a trace to view its spans, events and parent-child relationships.</p></section>}
      {detail?.status === "loading" && <section className="panel empty-state" role="status">Loading trace and spans…</section>}
      {detail?.status === "error" && <section className="panel error-state" role="alert"><h2>Trace unavailable</h2>
        <p>{detail.message}</p><button type="button" onClick={explorer.refreshDetail}>Retry trace</button></section>}
      {detail?.status === "ready" && <div>
        <div className="workspace-toolbar"><button type="button" className="text-button" onClick={explorer.refreshDetail}>Reload trace</button></div>
        <TraceWorkspace key={detail.data.trace.trace_id} data={detail.data} />
      </div>}
    </div>
  </div>;
}
