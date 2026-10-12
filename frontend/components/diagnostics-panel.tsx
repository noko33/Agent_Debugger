"use client";

import { useState } from "react";
import type { LoadState } from "@/hooks/use-trace-explorer";
import { categoryLabel, diagnosticCategories, natureLabels, outcomeLabels, relatedSpanIds, type Diagnostics, type DiagnosticCategory, type Evidence, type Finding, type Severity } from "@/lib/diagnostics";
import type { SpanRecord } from "@/lib/traces";
import { FixSuggestionsPanel } from "./fix-suggestions-panel";

function EvidenceView({ items, onSelect, loaded }: { items: Evidence[]; onSelect: (id: string) => void; loaded: Set<string> }) {
  return <ul className="diagnostic-evidence">{items.map((item, index) => <li key={index}>
    <strong>{item.signal}</strong>{item.span_id && <button type="button" className="text-button" disabled={!loaded.has(item.span_id)}
      onClick={() => onSelect(item.span_id!)}>Inspect evidence span {item.span_id}</button>}
    {item.span_id && !loaded.has(item.span_id) && <p className="warning">Evidence span {item.span_id} is not loaded. Reload the trace; it may be missing or beyond the display limit.</p>}
    <pre className="json-view">{JSON.stringify(item.values, null, 2)}</pre>
  </li>)}</ul>;
}

export function DiagnosticsPanel({ state, onRetry, spans, selectedId, activeFinding, onFinding, onSelect }: {
  state: LoadState<Diagnostics>; onRetry: () => void; spans: SpanRecord[]; selectedId: string | null;
  activeFinding: Finding | null; onFinding: (finding: Finding) => void; onSelect: (id: string) => void;
}) {
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [category, setCategory] = useState<DiagnosticCategory | "all">("all");
  const loaded = new Set(spans.map(span => span.span_id));
  const names = new Map(spans.map(span => [span.span_id, span.name]));
  return <section className="diagnostics-panel panel" aria-label="Diagnostics">
    <div className="section-heading"><h2>Diagnostics</h2><button type="button" className="text-button" onClick={onRetry}>Reload diagnostics</button></div>
    {state.status === "loading" && <p role="status">Loading diagnostics…</p>}
    {state.status === "error" && <div role="alert" className="error-state"><p>{state.message}</p><button type="button" onClick={onRetry}>Retry diagnostics</button></div>}
    {state.status === "ready" && <>
      <div className={`diagnostic-outcome outcome-${state.data.outcome.classification}`}>
        <h3>Recorded execution: {outcomeLabels[state.data.outcome.classification]}</h3><p>{state.data.outcome.explanation}</p>
        {!!state.data.outcome.evidence.length && <details><summary>Execution outcome evidence</summary><EvidenceView items={state.data.outcome.evidence} loaded={loaded} onSelect={onSelect} /></details>}
      </div>
      <p className="muted">{state.data.findings.length} findings · {state.data.operation_failure_count} failed operations · {state.data.span_count} currently stored spans · Rules {state.data.rules_version}</p>
      <p className="muted">Operation failures do not automatically mean overall execution failure. Recovery is recorded evidence, not proof of answer correctness.</p>
      <div className="span-filters diagnostic-filters">
        <label>Diagnostic severity<select value={severity} onChange={e => setSeverity(e.target.value as Severity | "all")}><option value="all">All severities</option><option value="error">Error</option><option value="warning">Warning</option></select></label>
        <label>Diagnostic category<select value={category} onChange={e => setCategory(e.target.value as DiagnosticCategory | "all")}><option value="all">All categories</option>{diagnosticCategories.map(c => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
        <button type="button" className="text-button" onClick={() => { setCategory("all"); setSeverity("all"); }}>Clear diagnostic filters</button>
      </div>
      <ul className="diagnostic-counts" aria-label="Finding counts by category">{diagnosticCategories.map(c => <li key={c}>{categoryLabel(c)}: {state.data.findings.filter(f => f.category === c).length}</li>)}</ul>
      {!state.data.findings.length ? <p className="empty-state">No diagnostic findings in currently stored data. This does not prove semantic correctness.</p> : <>
        {!state.data.findings.some(f => (severity === "all" || f.severity === severity) && (category === "all" || f.category === category)) && <p className="empty-state">No findings match these diagnostic filters.</p>}
        {diagnosticCategories.filter(c => category === "all" || category === c).map(c => {
          const findings = state.data.findings.filter(f => f.category === c && (severity === "all" || f.severity === severity));
          return findings.length ? <section key={c} aria-label={categoryLabel(c)} className="diagnostic-group"><h3>{categoryLabel(c)} ({findings.length})</h3>{findings.map((finding, index) => {
            const ids = relatedSpanIds(finding);
            return <article key={`${finding.rule_id}-${index}`} className={`diagnostic-card ${activeFinding === finding ? "active-finding" : ""}`}>
              <button type="button" className="finding-title" aria-pressed={activeFinding === finding} onClick={() => onFinding(finding)}>{finding.summary}</button>
              <p><span className={`status-badge status-${finding.severity === "error" ? "error" : "unset"}`}>{finding.severity}</span> · {natureLabels[finding.nature]}</p>
              <p>{finding.explanation}</p><p className="muted">Rule: {finding.rule_id}</p>
              {!ids.length && <p className="muted">Trace-level finding; no span reference was provided.</p>}
              <ul className="related-spans" aria-label={`Related spans for ${finding.summary}`}>{ids.map(id => <li key={id}>
                <button type="button" className="text-button" disabled={!loaded.has(id)} aria-pressed={selectedId === id}
                  onClick={() => { onFinding(finding); onSelect(id); }}>Inspect {names.get(id) ?? id} · {id}</button>
                {!loaded.has(id) && <p className="warning">Referenced span {id} is not loaded. Reload the trace; it may be missing or beyond the display limit.</p>}
              </li>)}</ul>
              <details><summary>Supporting evidence and rule parameters</summary><EvidenceView items={finding.evidence} loaded={loaded} onSelect={id => { onFinding(finding); onSelect(id); }} />
                <p>Rule parameters</p><pre className="json-view">{JSON.stringify(finding.parameters, null, 2)}</pre></details>
            </article>;
          })}</section> : null;
        })}
      </>}
      <details><summary>Analysis coverage and effective parameters</summary><p>All currently stored spans; export completeness is not guaranteed. Span views may be partially loaded or have been fetched at a different time. Reload trace to refresh all views.</p><pre className="json-view">{JSON.stringify(state.data.parameters, null, 2)}</pre></details>
      <FixSuggestionsPanel key={`${state.data.project_id}:${state.data.trace_id}`} traceId={state.data.trace_id} projectId={state.data.project_id} spans={spans} onSelect={onSelect} />
    </>}
  </section>;
}
