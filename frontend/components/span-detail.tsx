import { AttributeView, StatusBadge } from "./trace-format";
import { durationLabel, hasException, type SpanRecord } from "@/lib/traces";

export function SpanDetail({ span, onSelectParent, parentAvailable }: {
  span: SpanRecord | null;
  onSelectParent: (id: string) => void;
  parentAvailable: boolean;
}) {
  if (!span) return <aside className="detail-panel panel" aria-label="Span details">
    <h2>Span details</h2><p className="muted">Select a span to inspect its execution.</p>
  </aside>;
  const input = span.attributes["gen_ai.usage.input_tokens"];
  const output = span.attributes["gen_ai.usage.output_tokens"];
  const tokens = (value: unknown) => typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value.toLocaleString("en-US") : "Not recorded";
  return <aside className="detail-panel panel" aria-label="Span details">
    <div className="section-heading"><h2>Span details</h2><StatusBadge status={span.status_code} /></div>
    <h3 className="span-title">{span.name}</h3>
    <p className="muted">{span.operation_type.toUpperCase()} · {span.span_kind}</p>
    {hasException(span) && <p className="exception-label">Exception recorded</p>}
    <dl className="metadata">
      <dt>Trace ID</dt><dd>{span.trace_id}</dd>
      <dt>Span ID</dt><dd>{span.span_id}</dd>
      <dt>Parent Span ID</dt><dd>{span.parent_span_id ?? "Root span"}
        {span.parent_span_id && parentAvailable && <button className="text-button" type="button"
          onClick={() => onSelectParent(span.parent_span_id!)}>Inspect parent</button>}
      </dd>
      <dt>Start time (UTC)</dt><dd>{span.start_time}</dd>
      <dt>End time (UTC)</dt><dd>{span.end_time ?? "Not recorded"}</dd>
      <dt>Duration</dt><dd>{durationLabel(span)}</dd>
      {span.status_message !== null && <><dt>Status message</dt><dd>{span.status_message}</dd></>}
    </dl>
    {span.operation_type === "llm" && <section aria-label="Token usage">
      <h3>Token usage</h3>
      <dl className="metadata"><dt>Input tokens</dt><dd>{tokens(input)}</dd>
        <dt>Output tokens</dt><dd>{tokens(output)}</dd></dl>
    </section>}
    <section><h3>Attributes</h3><AttributeView attributes={span.attributes} /></section>
    <section><h3>Events ({span.events.length})</h3>
      {!span.events.length && <p className="muted">No events recorded.</p>}
      <ol className="events">{span.events.map((event, index) => <li key={index}>
        <strong>{event.name}</strong><time>{event.timestamp}</time>
        <AttributeView attributes={event.attributes} />
      </li>)}</ol>
    </section>
  </aside>;
}
