export const diagnosticCategories = [
  "TOOL_EXECUTION_ERROR", "LLM_EXECUTION_ERROR", "REPEATED_TOOL_FAILURE",
  "POSSIBLE_RETRY_LOOP", "SLOW_OPERATION", "INCOMPLETE_OR_INVALID_TRACE",
] as const;
export type DiagnosticCategory = typeof diagnosticCategories[number];
export type Severity = "error" | "warning";
export type Nature = "observed_failure" | "heuristic_warning" | "data_quality";
export type Outcome = "recorded_success" | "recovered_execution" | "unrecovered_execution" | "unknown";
type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export interface Evidence { span_id: string | null; signal: string; values: Record<string, Json> }
export interface Finding {
  rule_id: string; category: DiagnosticCategory; severity: Severity; nature: Nature;
  summary: string; explanation: string; trace_id: string; span_ids: string[];
  evidence: Evidence[]; parameters: Record<string, Json>;
}
export interface Diagnostics {
  schema_version: string; rules_version: string; trace_id: string; project_id: string;
  coverage: "all_currently_stored_spans"; span_count: number; operation_failure_count: number;
  repeated_failure_group_count: number;
  parameters: { repeated_tool_failure_threshold: number; retry_loop_threshold: number; slow_operation_ms: number; max_spans: number };
  outcome: { classification: Outcome; explanation: string; evidence: Evidence[] };
  findings: Finding[];
}
export const outcomeLabels: Record<Outcome, string> = {
  recorded_success: "Recorded success", recovered_execution: "Recovered",
  unrecovered_execution: "Unrecovered", unknown: "Unknown",
};
export const natureLabels: Record<Nature, string> = {
  observed_failure: "Observed failure", heuristic_warning: "Heuristic warning", data_quality: "Structural / data quality",
};
export const categoryLabel = (category: DiagnosticCategory) => category.toLowerCase().replaceAll("_", " ");
export function relatedSpanIds(finding: Finding): string[] {
  return [...new Set([...finding.span_ids, ...finding.evidence.flatMap(item => item.span_id ? [item.span_id] : [])])];
}
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const id = (v: unknown, length: number) => typeof v === "string" && v.length === length && /^[0-9a-f]+$/.test(v) && /[1-9a-f]/.test(v);
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
function json(v: unknown): boolean {
  return v === null || typeof v === "string" || typeof v === "boolean" ||
    (typeof v === "number" && Number.isFinite(v)) || (Array.isArray(v) ? v.every(json) : record(v) && Object.values(v).every(json));
}
const jsonRecord = (v: unknown) => record(v) && json(v);
const evidence = (v: unknown) => record(v) && (v.span_id === null || id(v.span_id, 16)) && typeof v.signal === "string" && jsonRecord(v.values);
const evidenceList = (v: unknown) => Array.isArray(v) && v.every(evidence);
export function parseDiagnostics(value: unknown, traceId: string, projectId: string): Diagnostics {
  const v = value;
  if (!record(v) || v.schema_version !== "1" || typeof v.rules_version !== "string" ||
    !id(v.trace_id, 32) || v.trace_id !== traceId || v.project_id !== projectId ||
    v.coverage !== "all_currently_stored_spans" || !count(v.span_count) || !count(v.operation_failure_count) ||
    !count(v.repeated_failure_group_count) || !record(v.parameters) ||
    !count(v.parameters.repeated_tool_failure_threshold) || v.parameters.repeated_tool_failure_threshold < 2 ||
    !count(v.parameters.retry_loop_threshold) || v.parameters.retry_loop_threshold < 3 ||
    !count(v.parameters.max_spans) || v.parameters.max_spans < 1 ||
    typeof v.parameters.slow_operation_ms !== "number" || !Number.isFinite(v.parameters.slow_operation_ms) || v.parameters.slow_operation_ms <= 0 ||
    !record(v.outcome) || typeof v.outcome.classification !== "string" || !Object.hasOwn(outcomeLabels, v.outcome.classification) ||
    typeof v.outcome.explanation !== "string" || !evidenceList(v.outcome.evidence) ||
    !Array.isArray(v.findings) || !v.findings.every(f => record(f) && typeof f.rule_id === "string" &&
      diagnosticCategories.includes(f.category as DiagnosticCategory) && typeof f.severity === "string" && ["error", "warning"].includes(f.severity) &&
      typeof f.nature === "string" && Object.hasOwn(natureLabels, f.nature) && typeof f.summary === "string" && typeof f.explanation === "string" &&
      f.trace_id === traceId && Array.isArray(f.span_ids) && f.span_ids.every(s => id(s, 16)) && evidenceList(f.evidence) && jsonRecord(f.parameters))) {
    throw new Error("Trace API returned an unexpected diagnostics response.");
  }
  return v as unknown as Diagnostics;
}
