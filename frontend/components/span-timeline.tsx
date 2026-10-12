import type { buildTimeline } from "@/lib/performance";
import { milliseconds } from "@/lib/performance";
import { StatusBadge } from "./trace-format";

export function SpanTimeline({ timeline, visibleIds, contextIds, selectedId, onSelect, highlightedIds }: {
  timeline: ReturnType<typeof buildTimeline>; visibleIds: Set<string>; contextIds: Set<string>;
  selectedId: string | null; onSelect: (id: string) => void; highlightedIds?: Set<string>;
}) {
  const { minMs, maxMs } = timeline;
  const range = maxMs - minMs;
  const position = (offset: number) => range > 0 ? (offset - minMs) / range * 100 : 0;
  return <div className="timeline-scroll" aria-label="Trace timeline">
    <div className="timeline-content">
      <div className="timeline-row timeline-header"><span>Span / offset / duration</span>
        <div className="timeline-axis">{(range > 0 ? [0, 0.25, 0.5, 0.75, 1] : [0]).map(tick =>
          <span key={tick} style={{ left: `${tick * 100}%` }}>{milliseconds(minMs + tick * range)}</span>)}</div>
      </div>
      {timeline.rows.filter(row => visibleIds.has(row.node.span.span_id)).map(({ node, offsetMs, durationMs, warnings }) => {
        const span = node.span;
        return <button type="button" key={span.span_id}
          aria-label={`Timeline span ${span.name}`} aria-pressed={selectedId === span.span_id}
          className={`timeline-row ${contextIds.has(span.span_id) ? "filter-context" : ""} ${highlightedIds?.has(span.span_id) ? "diagnostic-related" : ""}`}
          onClick={() => onSelect(span.span_id)}>
          <span className="timeline-label" style={{ paddingLeft: `${Math.min(node.depth, 8) * 12 + 8}px` }}>
            <strong>{span.name}</strong><small>{span.operation_type.toUpperCase()} · <StatusBadge status={span.status_code} />
              {contextIds.has(span.span_id) && " · Context"}{node.exception && " · Exception"}{highlightedIds?.has(span.span_id) && " · Diagnostic evidence"}</small>
            <small>Start {milliseconds(offsetMs)} · Duration {milliseconds(durationMs)}</small>
            {warnings.map(warning => <small className="timing-warning" key={warning}>{warning}</small>)}
          </span>
          <span className="timeline-lane">
            {offsetMs === null ? <small>Position unavailable</small> :
              <span data-testid={`bar-${span.span_id}`} title={`Start ${milliseconds(offsetMs)}; duration ${milliseconds(durationMs)}`}
                className={`duration-bar operation-${span.operation_type} ${span.status_code === "error" || node.exception ? "bar-error" : ""} ${durationMs === null ? "bar-incomplete" : ""}`}
                style={{ left: `${position(offsetMs)}%`, width: `${range > 0 && durationMs !== null ? durationMs / range * 100 : 0}%` }} />}
          </span>
        </button>;
      })}
    </div>
  </div>;
}
