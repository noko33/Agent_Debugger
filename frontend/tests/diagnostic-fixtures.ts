import type { Diagnostics, Finding } from "@/lib/diagnostics";
import { spanFixture, traceFixture } from "./fixtures";

export const diagnosticTool = spanFixture("2", "1", { name: "Optional tool", operation_type: "tool", status_code: "error",
  events: [{ name: "exception", timestamp: traceFixture.start_time, attributes: { "exception.type": "ConnectionError" } }] });
export const findingFixture: Finding = {
  rule_id: "tool_execution_error.v1", category: "TOOL_EXECUTION_ERROR", severity: "error", nature: "observed_failure",
  summary: "Tool operation failed", explanation: "Recorded tool error; this does not establish overall failure.",
  trace_id: traceFixture.trace_id, span_ids: [diagnosticTool.span_id], parameters: {},
  evidence: [{ span_id: diagnosticTool.span_id, signal: "operation_status", values: { status_code: "error", operation_type: "tool" } }],
};
export const diagnosticsFixture: Diagnostics = {
  schema_version: "1", rules_version: "4a.1", trace_id: traceFixture.trace_id, project_id: traceFixture.project_id,
  coverage: "all_currently_stored_spans", span_count: 2, operation_failure_count: 1, repeated_failure_group_count: 0,
  parameters: { repeated_tool_failure_threshold: 2, retry_loop_threshold: 3, slow_operation_ms: 1000, max_spans: 5000 },
  outcome: { classification: "recovered_execution", explanation: "Root OK with an explicit recovery event after the failure.", evidence: [] },
  findings: [findingFixture],
};
