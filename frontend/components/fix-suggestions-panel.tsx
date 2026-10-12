"use client";

import { useState } from "react";
import { useFixSuggestions } from "@/hooks/use-fix-suggestions";
import type { SpanRecord } from "@/lib/traces";

export function FixSuggestionsPanel({ traceId, projectId, spans, onSelect }: {
  traceId: string; projectId: string; spans: SpanRecord[]; onSelect: (id: string) => void;
}) {
  const { state, request } = useFixSuggestions(traceId, projectId);
  const [includeAi, setIncludeAi] = useState(false);
  const [consent, setConsent] = useState(false);
  const names = new Map(spans.map(s => [s.span_id, s.name]));
  return <section aria-label="Repair suggestions" className="repair-suggestions">
    <h3>Repair suggestions</h3>
    <p className="muted">Review-only proposals. Nothing is edited or executed. Expected improvements must be tested.</p>
    <label><input type="checkbox" checked={includeAi} onChange={e => { setIncludeAi(e.target.checked); setConsent(false); }} /> Include optional AI hypotheses</label>
    {includeAi && <label><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /> I consent to sending allowlisted trace metadata to the configured provider; charges may apply.</label>}
    <p><button type="button" disabled={state?.status === "loading" || (includeAi && !consent)} onClick={() => void request(includeAi, consent)}>Request suggestions</button></p>
    {state?.status === "loading" && <p role="status">Preparing suggestions…</p>}
    {state?.status === "error" && <p role="alert">{state.message} Use Request suggestions to retry.</p>}
    {state?.status === "ready" && <>
      <p className="muted">AI analysis: {state.data.analysis.execution.status}. {state.data.analysis.execution.message}</p>
      <p className="muted">Recorded outcome for this snapshot: {state.data.analysis.deterministic.outcome.classification}. Suggestions and graph may be separate snapshots.</p>
      {!state.data.suggestions.length && <p>No evidence-grounded repair suggestion is available. This does not prove correctness.</p>}
      {state.data.suggestions.map(s => <article key={s.suggestion_id} className="diagnostic-card">
        <h4>{s.title}</h4><p>{s.category} · {s.basis.replaceAll("_", " ")} · Proposed, unvalidated</p>
        <p>{s.explanation}</p><p><strong>Proposed change: </strong>{s.proposed_change}</p>
        <p><strong>Expected observable improvement: </strong>{s.expected_observable_improvement}</p>
        {!!s.hypothesis_ids.length && <p className="warning">Related AI hypotheses: {s.hypothesis_ids.join(", ")}. These are unverified explanations, not causes established by evidence.</p>}
        <details><summary>Evidence, risks and validation</summary>
          <p>Finding references: {s.related_finding_ids.join(", ")}</p>
          <pre className="json-view">{JSON.stringify(state.data.evidence.filter(e => s.supporting_evidence.includes(e.evidence_id)), null, 2)}</pre>
          <h5>Risks and trade-offs</h5><ul>{s.risks_and_tradeoffs.map((x, i) => <li key={i}>{x}</li>)}</ul>
          <h5>Preconditions</h5><ul>{s.preconditions.map((x, i) => <li key={i}>{x}</li>)}</ul>
          <h5>Missing evidence</h5><ul>{s.missing_evidence.map((x, i) => <li key={i}>{x}</li>)}</ul>
          <h5>Validation plan</h5><ol>{s.validation_plan.map((x, i) => <li key={i}>{x}</li>)}</ol>
        </details>
        {!s.span_ids.length && <p className="muted">Trace-level proposal; no stored span reference.</p>}
        {s.span_ids.map(id => <p key={id}><button type="button" className="text-button" disabled={!names.has(id)} onClick={() => onSelect(id)}>Inspect suggested span {names.get(id) ?? id} · {id}</button>
          {!names.has(id) && <span className="warning"> Referenced span is not loaded; reload the trace.</span>}</p>)}
      </article>)}
      {!!state.data.omitted_suggestion_count && <p className="warning">{state.data.omitted_suggestion_count} proposals omitted by the response limit.</p>}
    </>}
  </section>;
}
