import { milliseconds, type performanceSummary } from "@/lib/performance";

export function PerformanceSummary({ summary, partial, total, onSelect }: {
  summary: ReturnType<typeof performanceSummary>; partial: boolean; total: number; onSelect: (id: string) => void;
}) {
  const tokens = (value: typeof summary.inputTokens) =>
    `${value.total === null ? "Not recorded" : value.total.toLocaleString("en-US")} (${value.recorded}/${value.eligible} LLM spans recorded)`;
  return <section className="panel performance-summary" aria-label="Performance summary">
    <h2>Performance summary</h2>
    <p className="muted">All loaded spans, independent of filters. Wall-clock duration is not the sum of overlapping span durations.</p>
    {partial && <p className="warning">Partial data: statistics cover {summary.spanCount} of {total} spans.</p>}
    <dl className="performance-stats">
      <div><dt>Trace wall-clock</dt><dd>{summary.timing.invalid ? "Invalid timing" : milliseconds(summary.timing.durationMs)}</dd></div>
      <div><dt>Spans loaded / total</dt><dd>{summary.spanCount} / {total}</dd></div>
      <div><dt>Error spans</dt><dd>{summary.errorCount}</dd></div>
      <div><dt>LLM calls</dt><dd>{summary.llmCount}</dd></div>
      <div><dt>Tool calls</dt><dd>{summary.toolCount}</dd></div>
      <div><dt>Recorded input tokens</dt><dd>{tokens(summary.inputTokens)}</dd></div>
      <div><dt>Recorded output tokens</dt><dd>{tokens(summary.outputTokens)}</dd></div>
    </dl>
    <details><summary>Slowest spans (inclusive duration; ties do not imply call order)</summary>
      {!summary.slowest.length ? <p className="muted">No completed spans with valid timing.</p> :
        <ol className="slowest-spans">{summary.slowest.map(({ span, durationMs }) => <li key={span.span_id}>
          <button className="text-button" type="button" aria-label={`Inspect slow span ${span.name}`} onClick={() => onSelect(span.span_id)}>{span.name}</button>
          <span>{milliseconds(durationMs)}</span>
        </li>)}</ol>}
    </details>
  </section>;
}
