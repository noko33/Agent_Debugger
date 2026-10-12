import { parseDiagnostics, type Diagnostics } from "./diagnostics";

export const suggestionCategories = ["PROMPT", "TOOL_POLICY", "RUNTIME", "INSTRUMENTATION"] as const;
export type SuggestionCategory = typeof suggestionCategories[number];
export interface FixSuggestion {
  suggestion_id: string; category: SuggestionCategory; title: string; explanation: string;
  basis: "recorded_observation" | "heuristic_warning" | "data_quality";
  related_finding_ids: string[]; span_ids: string[]; supporting_evidence: string[]; hypothesis_ids: string[];
  proposed_change: string; expected_observable_improvement: string; risks_and_tradeoffs: string[];
  preconditions: string[]; missing_evidence: string[]; validation_plan: string[]; status: "proposed_unvalidated";
}
export interface FixSuggestions {
  schema_version: "1"; suggestions_version: "5b.1"; trace_id: string; project_id: string;
  analysis: { trace_id: string; deterministic: Diagnostics; hypotheses: { hypothesis_id: string }[];
    execution: { status: string; message: string; error_code: string | null } };
  finding_ids: string[];
  evidence: { evidence_id: string; source: "rule_finding" | "recorded_outcome"; span_ids: string[]; values: Record<string, unknown> }[];
  suggestions: FixSuggestion[]; omitted_suggestion_count: number; limitations: string[];
}
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(s => typeof s === "string" && s.length > 0);
const ids = (v: unknown): v is string[] => strings(v) && v.every(s => /^[0-9a-f]{16}$/.test(s) && /[1-9a-f]/.test(s));
const stable = (v: unknown): v is string[] => strings(v) && v.every(s => /^f_[0-9a-f]{24}$/.test(s));
const unique = (v: string[]) => new Set(v).size === v.length;

export function parseFixSuggestions(value: unknown, traceId: string, projectId: string): FixSuggestions {
  const v = value;
  const fail = (): never => { throw new Error("Trace API returned an unexpected repair suggestions response."); };
  if (!record(v) || v.schema_version !== "1" || v.suggestions_version !== "5b.1" ||
    v.trace_id !== traceId || v.project_id !== projectId || !record(v.analysis) || v.analysis.trace_id !== traceId ||
    !record(v.analysis.execution) || !["completed", "disabled", "consent_required", "not_needed", "rate_limited", "failed"].includes(String(v.analysis.execution.status)) ||
    typeof v.analysis.execution.message !== "string" || !(v.analysis.execution.error_code === null || typeof v.analysis.execution.error_code === "string") ||
    !Array.isArray(v.analysis.hypotheses) || !v.analysis.hypotheses.every(h => record(h) && typeof h.hypothesis_id === "string") ||
    !stable(v.finding_ids) || !unique(v.finding_ids) || !Array.isArray(v.evidence) || !Array.isArray(v.suggestions) ||
    v.suggestions.length > 100 || !strings(v.limitations) || !Number.isSafeInteger(v.omitted_suggestion_count) || Number(v.omitted_suggestion_count) < 0) return fail();
  parseDiagnostics(v.analysis.deterministic, traceId, projectId);
  const evidence = new Map<string, string[]>();
  for (const e of v.evidence) {
    if (!record(e) || typeof e.evidence_id !== "string" || evidence.has(e.evidence_id) ||
      !["rule_finding", "recorded_outcome"].includes(String(e.source)) || !ids(e.span_ids) || !record(e.values)) return fail();
    evidence.set(e.evidence_id, e.span_ids);
  }
  const hypotheses = new Set(v.analysis.hypotheses.map(h => (h as { hypothesis_id: string }).hypothesis_id));
  const seen = new Set();
  for (const s of v.suggestions) {
    if (!record(s) || typeof s.suggestion_id !== "string" || !/^s_[0-9a-f]{24}$/.test(s.suggestion_id) || seen.has(s.suggestion_id) ||
      !suggestionCategories.includes(s.category as SuggestionCategory) || s.status !== "proposed_unvalidated" ||
      !["recorded_observation", "heuristic_warning", "data_quality"].includes(String(s.basis)) ||
      ![s.title, s.explanation, s.proposed_change, s.expected_observable_improvement].every(x => typeof x === "string" && x.length > 0) ||
      ![s.risks_and_tradeoffs, s.preconditions, s.missing_evidence, s.validation_plan].every(x => strings(x) && x.length > 0) ||
      !stable(s.related_finding_ids) || !s.related_finding_ids.every(f => (v.finding_ids as string[]).includes(f)) ||
      !ids(s.span_ids) || !strings(s.hypothesis_ids) || !s.hypothesis_ids.every(h => hypotheses.has(h)) ||
      !strings(s.supporting_evidence) || !s.supporting_evidence.length || !s.supporting_evidence.every(e => evidence.has(e)) ||
      !s.span_ids.every(id => (s.supporting_evidence as string[]).some(e => evidence.get(e)?.includes(id)))) return fail();
    seen.add(s.suggestion_id);
  }
  return v as unknown as FixSuggestions;
}
