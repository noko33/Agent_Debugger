import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { DiagnosticsPanel } from "@/components/diagnostics-panel";
import { TraceWorkspace } from "@/components/trace-workspace";
import { useDiagnostics } from "@/hooks/use-diagnostics";
import * as api from "@/lib/api";
import { parseDiagnostics, relatedSpanIds, type Diagnostics, type Finding } from "@/lib/diagnostics";
import type { SpanTree } from "@/lib/span-tree";
import { diagnosticTool, diagnosticsFixture, findingFixture } from "./diagnostic-fixtures";
import { spanFixture, traceFixture } from "./fixtures";

vi.mock("@/lib/api", async original => ({ ...await original<typeof import("@/lib/api")>(), getDiagnostics: vi.fn() }));
vi.mock("@/components/span-graph", () => ({ SpanGraph: ({ tree, selectedId, highlightedIds, onSelect }: {
  tree: SpanTree; selectedId: string | null; highlightedIds: Set<string>; onSelect: (id: string) => void;
}) => <div aria-label="Graph double">{tree.nodes.map(({ span }) => <button key={span.span_id}
  aria-pressed={selectedId === span.span_id} data-highlighted={highlightedIds.has(span.span_id)}
  onClick={() => onSelect(span.span_id)}>{span.name}</button>)}</div> }));
beforeEach(() => vi.mocked(api.getDiagnostics).mockReset().mockResolvedValue(diagnosticsFixture));

it("parses the real contract shape and rejects wrong identities, versions, enum values and malformed evidence", () => {
  expect(parseDiagnostics(diagnosticsFixture, traceFixture.trace_id, traceFixture.project_id)).toEqual(diagnosticsFixture);
  for (const extra of [{ schema_version: "2" }, { project_id: "other" }, { trace_id: "2".repeat(32) },
    { outcome: { classification: "success", explanation: "", evidence: [] } }, { parameters: {} },
    { findings: [{ ...findingFixture, category: "MADE_UP" }] },
    { findings: [{ ...findingFixture, severity: "critical" }] },
    { findings: [{ ...findingFixture, evidence: [{ span_id: "bad", signal: "error", values: {} }] }] },
    { findings: [{ ...findingFixture, trace_id: "2".repeat(32) }] }]) {
    expect(() => parseDiagnostics({ ...diagnosticsFixture, ...extra }, traceFixture.trace_id, traceFixture.project_id)).toThrow("unexpected diagnostics");
  }
});

it("fetches diagnostics with existing timeout/cancellation and project scope, rejecting raw HTTP bodies", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(diagnosticsFixture)));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  expect(await actual.getDiagnostics(traceFixture.trace_id, traceFixture.project_id, controller.signal)).toEqual(diagnosticsFixture);
  expect(fetcher.mock.calls[0][0]).toContain(`/diagnostics?project_id=${traceFixture.project_id}`);
  expect(fetcher.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  fetcher.mockResolvedValue(new Response("private secret", { status: 413 }));
  await expect(actual.getDiagnostics(traceFixture.trace_id, traceFixture.project_id)).rejects.toThrow("HTTP 413");
});

function panel(data = diagnosticsFixture, onFinding = vi.fn(), onSelect = vi.fn()) {
  return render(<DiagnosticsPanel state={{ status: "ready", data }} onRetry={vi.fn()} spans={[diagnosticTool]}
    selectedId={null} activeFinding={null} onFinding={onFinding} onSelect={onSelect} />);
}
it("renders actual recovery, observed failures, rule parameters and escaped evidence", () => {
  const data = { ...diagnosticsFixture, findings: [{ ...findingFixture, evidence: [{ span_id: diagnosticTool.span_id, signal: "operation_status", values: { text: "<img src=x onerror=alert(1)>" } }] }] };
  const { container } = panel(data);
  expect(screen.getByText("Recorded execution: Recovered")).toBeDefined();
  expect(screen.getByText(/Observed failure/)).toBeDefined();
  fireEvent.click(screen.getByText("Supporting evidence and rule parameters"));
  expect(screen.getByText("Rule parameters")).toBeDefined();
  expect(container.querySelector("img")).toBeNull(); expect(container.textContent).toContain("<img");
});
it.each(["recorded_success", "unrecovered_execution", "unknown"] as const)("uses backend outcome %s without inference from child errors", classification => {
  panel({ ...diagnosticsFixture, outcome: { ...diagnosticsFixture.outcome, classification } });
  const labels = { recorded_success: "Recorded success", unrecovered_execution: "Unrecovered", unknown: "Unknown" };
  expect(screen.getByText(`Recorded execution: ${labels[classification]}`)).toBeDefined();
});
it("groups by category, counts all findings, and combines severity/category filters", () => {
  panel({ ...diagnosticsFixture, findings: [findingFixture, { ...findingFixture, rule_id: "slow_operation.v1", summary: "Slow call", category: "SLOW_OPERATION", severity: "warning", nature: "heuristic_warning" }] });
  expect(screen.getByLabelText("Finding counts by category").textContent).toContain("slow operation: 1");
  fireEvent.change(screen.getByLabelText("Diagnostic severity"), { target: { value: "warning" } });
  expect(screen.queryByRole("button", { name: findingFixture.summary })).toBeNull();
  expect(screen.getByText(/Heuristic warning/)).toBeDefined();
  fireEvent.change(screen.getByLabelText("Diagnostic category"), { target: { value: "TOOL_EXECUTION_ERROR" } });
  expect(screen.getByText("No findings match these diagnostic filters.")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Clear diagnostic filters" }));
  expect(screen.getByRole("button", { name: findingFixture.summary })).toBeDefined();
});
it("supports several references and missing references without fabricating spans", () => {
  const missing = "f".repeat(16);
  const finding: Finding = { ...findingFixture, span_ids: [missing, diagnosticTool.span_id], evidence: [...findingFixture.evidence, { span_id: diagnosticTool.span_id, signal: "extra", values: {} }] };
  expect(relatedSpanIds(finding)).toEqual([missing, diagnosticTool.span_id]);
  const onFinding = vi.fn(), onSelect = vi.fn();
  panel({ ...diagnosticsFixture, findings: [finding] }, onFinding, onSelect);
  const refs = screen.getByRole("list", { name: `Related spans for ${finding.summary}` });
  expect(within(refs).getAllByRole("button")).toHaveLength(2);
  expect(within(refs).getByRole("button", { name: `Inspect ${missing} · ${missing}` }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByText(/Referenced span .* is not loaded/)).toBeDefined();
  fireEvent.click(within(refs).getByRole("button", { name: /Inspect Optional tool/ }));
  expect(onSelect).toHaveBeenCalledWith(diagnosticTool.span_id); expect(onFinding).toHaveBeenCalledWith(finding);
});
it("displays trace-level findings and no-finding state independently of execution status", () => {
  const view = panel({ ...diagnosticsFixture, findings: [{ ...findingFixture, span_ids: [], evidence: [] }] });
  expect(screen.getByText(/Trace-level finding/)).toBeDefined();
  view.unmount(); panel({ ...diagnosticsFixture, findings: [] });
  expect(screen.getByText(/No diagnostic findings in currently stored data/)).toBeDefined();
  expect(screen.getByText("Recorded execution: Recovered")).toBeDefined();
});
it("handles loading, safe errors and retries while keeping span browsing available", async () => {
  vi.mocked(api.getDiagnostics).mockRejectedValueOnce(new Error("private transport credential"));
  render(<TraceWorkspace data={{ trace: traceFixture, spans: { spans: [diagnosticTool], total: 1, truncated: false } }} />);
  expect(screen.getByText("Loading diagnostics…")).toBeDefined();
  expect((await screen.findByRole("alert")).textContent).not.toContain("credential");
  fireEvent.click(screen.getByRole("button", { name: "Optional tool" }));
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Optional tool")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Retry diagnostics" }));
  expect(await screen.findByText("Recorded execution: Recovered")).toBeDefined();
  expect(api.getDiagnostics).toHaveBeenCalledTimes(2);
});
it("ignores late responses and errors after a trace switch and aborts old requests", async () => {
  let resolve!: (data: Diagnostics) => void;
  vi.mocked(api.getDiagnostics).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const second = "2".repeat(32);
  vi.mocked(api.getDiagnostics).mockResolvedValueOnce({ ...diagnosticsFixture, trace_id: second, findings: [], outcome: { classification: "unknown", explanation: "Second trace", evidence: [] } });
  function Probe({ id }: { id: string }) { const { state } = useDiagnostics(id, traceFixture.project_id); return <p>{state.status === "ready" ? state.data.outcome.explanation : state.status}</p>; }
  const view = render(<Probe id={traceFixture.trace_id} />);
  const signal = vi.mocked(api.getDiagnostics).mock.calls[0][2]!;
  view.rerender(<Probe id={second} />);
  expect(screen.queryByText(diagnosticsFixture.outcome.explanation)).toBeNull(); expect(signal.aborted).toBe(true);
  expect(await screen.findByText("Second trace")).toBeDefined();
  await act(async () => resolve(diagnosticsFixture));
  expect(screen.getByText("Second trace")).toBeDefined();
});
it("ignores late rejection and suppresses old results while reloading", async () => {
  let reject!: (error: Error) => void;
  vi.mocked(api.getDiagnostics).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
  const second = "2".repeat(32);
  function Probe({ id }: { id: string }) {
    const { state, retry } = useDiagnostics(id, traceFixture.project_id);
    return <><p>{state.status === "ready" ? state.data.outcome.explanation : state.status}</p><button onClick={retry}>Retry probe</button></>;
  }
  const view = render(<Probe id={traceFixture.trace_id} />);
  view.rerender(<Probe id={second} />);
  expect(await screen.findByText(diagnosticsFixture.outcome.explanation)).toBeDefined();
  await act(async () => reject(new Error("late private failure")));
  expect(screen.queryByText("error")).toBeNull();
  vi.mocked(api.getDiagnostics).mockReturnValueOnce(new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: "Retry probe" }));
  expect(screen.getByText("loading")).toBeDefined();
  expect(screen.queryByText(diagnosticsFixture.outcome.explanation)).toBeNull();
});
it("selects the first loaded reference and clears unrelated selection if all references are missing", async () => {
  const missing = "f".repeat(16);
  const partial = { ...findingFixture, summary: "Some references missing", span_ids: [missing, diagnosticTool.span_id], evidence: [] };
  const unavailable = { ...findingFixture, summary: "All references missing", span_ids: [missing], evidence: [] };
  vi.mocked(api.getDiagnostics).mockResolvedValue({ ...diagnosticsFixture, findings: [partial, unavailable] });
  render(<TraceWorkspace data={{ trace: traceFixture, spans: { spans: [diagnosticTool], total: 1, truncated: false } }} />);
  fireEvent.click(await screen.findByRole("button", { name: partial.summary }));
  expect(screen.getByRole("button", { name: "Optional tool" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: unavailable.summary }));
  expect(screen.getByRole("button", { name: "Optional tool" }).getAttribute("aria-pressed")).toBe("false");
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Select a span to inspect its execution.")).toBeDefined();
});
it("synchronizes multi-span finding navigation with graph, timeline, details and filtered parents", async () => {
  const root = spanFixture("1", null, { name: "Root agent", operation_type: "agent" });
  const other = spanFixture("3", "1", { name: "Second tool", operation_type: "tool", status_code: "error" });
  const finding = { ...findingFixture, category: "REPEATED_TOOL_FAILURE" as const, span_ids: [diagnosticTool.span_id, other.span_id] };
  vi.mocked(api.getDiagnostics).mockResolvedValue({ ...diagnosticsFixture, findings: [finding] });
  render(<TraceWorkspace data={{ trace: traceFixture, spans: { spans: [root, diagnosticTool, other], total: 3, truncated: false } }} />);
  fireEvent.change(screen.getByLabelText("Name search"), { target: { value: "Root" } });
  fireEvent.click(await screen.findByRole("button", { name: finding.summary }));
  expect(screen.getByText(/Selected span is hidden by filters/)).toBeDefined();
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText(/ConnectionError/)).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByRole("button", { name: "Optional tool" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Second tool" }).getAttribute("data-highlighted")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  const first = screen.getByRole("button", { name: "Timeline span Optional tool" });
  expect(first.getAttribute("aria-pressed")).toBe("true"); expect(first.className).toContain("diagnostic-related");
  fireEvent.click(screen.getByRole("button", { name: /Inspect Second tool ·/ }));
  expect(screen.getByRole("button", { name: "Timeline span Second tool" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Graph" }));
  expect(screen.getByRole("button", { name: "Second tool" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Root agent" }));
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Root agent")).toBeDefined();
});
