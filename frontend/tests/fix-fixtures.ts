import type { FixSuggestions } from "@/lib/fix-suggestions";
import { diagnosticTool, diagnosticsFixture } from "./diagnostic-fixtures";
import { traceFixture } from "./fixtures";

const findingId = `f_${"a".repeat(24)}`;
export const fixesFixture: FixSuggestions = {
  schema_version: "1", suggestions_version: "5b.1", trace_id: traceFixture.trace_id, project_id: traceFixture.project_id,
  analysis: { trace_id: traceFixture.trace_id, deterministic: diagnosticsFixture, hypotheses: [],
    execution: { status: "disabled", message: "AI enrichment was not requested.", error_code: null } },
  finding_ids: [findingId], evidence: [{ evidence_id: findingId, source: "rule_finding", span_ids: [diagnosticTool.span_id], values: { category: "TOOL_EXECUTION_ERROR" } }],
  suggestions: [{ suggestion_id: `s_${"b".repeat(24)}`, category: "TOOL_POLICY", title: "Validate the existing recovery policy",
    explanation: "A tool operation failed while recovery was recorded.", basis: "recorded_observation", related_finding_ids: [findingId],
    span_ids: [diagnosticTool.span_id], supporting_evidence: [findingId], hypothesis_ids: [],
    proposed_change: "Review the fallback in a controlled local experiment.", expected_observable_improvement: "Recorded recovery meets reviewed checks.",
    risks_and_tradeoffs: ["Fallbacks may return incomplete results."], preconditions: ["Review fallback acceptance criteria."],
    missing_evidence: ["Semantic outcome is not established."], validation_plan: ["Capture a baseline.", "Test success and failure cases."], status: "proposed_unvalidated" }],
  omitted_suggestion_count: 0, limitations: ["No change is applied."],
};
