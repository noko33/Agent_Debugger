import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { FixSuggestionsPanel } from "@/components/fix-suggestions-panel";
import { TraceWorkspace } from "@/components/trace-workspace";
import * as api from "@/lib/api";
import { parseFixSuggestions, type FixSuggestions } from "@/lib/fix-suggestions";
import type { SpanTree } from "@/lib/span-tree";
import { diagnosticTool, diagnosticsFixture } from "./diagnostic-fixtures";
import { fixesFixture } from "./fix-fixtures";
import { traceFixture } from "./fixtures";

vi.mock("@/lib/api", async original => ({ ...await original<typeof import("@/lib/api")>(), getFixSuggestions: vi.fn(), getDiagnostics: vi.fn() }));
vi.mock("@/components/span-graph", () => ({ SpanGraph: ({ tree, selectedId, onSelect }: {
  tree: SpanTree; selectedId: string | null; onSelect: (id: string) => void;
}) => <div aria-label="Graph double">{tree.nodes.map(({ span }) => <button key={span.span_id}
  aria-pressed={selectedId === span.span_id} onClick={() => onSelect(span.span_id)}>{span.name}</button>)}</div> }));
beforeEach(() => {
  vi.mocked(api.getFixSuggestions).mockReset().mockResolvedValue(fixesFixture);
  vi.mocked(api.getDiagnostics).mockReset().mockResolvedValue(diagnosticsFixture);
});
const props = { traceId: traceFixture.trace_id, projectId: traceFixture.project_id, spans: [diagnosticTool], onSelect: vi.fn() };

it("validates the response and rejects unsupported, duplicate, conflicting and fabricated references", () => {
  expect(parseFixSuggestions(fixesFixture, props.traceId, props.projectId)).toEqual(fixesFixture);
  const first = fixesFixture.suggestions[0];
  for (const changes of [{ category: "AUTOMATIC_REPAIR" }, { status: "repaired" }, { span_ids: ["f".repeat(16)] },
    { supporting_evidence: ["invented"] }, { related_finding_ids: [`f_${"c".repeat(24)}`] }, { hypothesis_ids: ["h999"] }, { validation_plan: [] }]) {
    expect(() => parseFixSuggestions({ ...fixesFixture, suggestions: [{ ...first, ...changes }] }, props.traceId, props.projectId)).toThrow();
  }
  expect(() => parseFixSuggestions({ ...fixesFixture, suggestions: [first, first] }, props.traceId, props.projectId)).toThrow();
  expect(() => parseFixSuggestions({ ...fixesFixture, project_id: "wrong" }, props.traceId, props.projectId)).toThrow();
  expect(() => parseFixSuggestions({ ...fixesFixture, finding_ids: [`s_${"a".repeat(24)}`] }, props.traceId, props.projectId)).toThrow();
});

it("sends explicit scoped POST requests and never exposes raw API errors", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixesFixture)));
  vi.stubGlobal("fetch", fetcher);
  await actual.getFixSuggestions(props.traceId, props.projectId, false, false);
  expect(fetcher.mock.calls[0][0]).toContain(`/fix-suggestions?project_id=${props.projectId}`);
  expect(fetcher.mock.calls[0][1].method).toBe("POST");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ include_ai: false, consent_to_external_processing: false });
  fetcher.mockResolvedValue(new Response("private provider key", { status: 503 }));
  await expect(actual.getFixSuggestions(props.traceId, props.projectId, true, true)).rejects.toThrow("HTTP 503");
});

it("requests only on click, shows risks and validation, and navigates to loaded spans", async () => {
  const onSelect = vi.fn();
  render(<FixSuggestionsPanel {...props} onSelect={onSelect} />);
  expect(api.getFixSuggestions).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  expect(await screen.findByText("Validate the existing recovery policy")).toBeDefined();
  expect(api.getFixSuggestions).toHaveBeenCalledWith(props.traceId, props.projectId, false, false, expect.any(AbortSignal));
  fireEvent.click(screen.getByText("Evidence, risks and validation"));
  expect(screen.getByText("Capture a baseline.")).toBeDefined();
  expect(screen.getByText("Fallbacks may return incomplete results.")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: /Inspect suggested span/ }));
  expect(onSelect).toHaveBeenCalledWith(diagnosticTool.span_id);
});

it("requires separate consent for optional AI and reports unavailable providers without hiding suggestions", async () => {
  const data = structuredClone(fixesFixture);
  data.analysis.execution = { status: "failed", message: "AI unavailable; findings retained.", error_code: "provider_timeout" };
  vi.mocked(api.getFixSuggestions).mockResolvedValue(data);
  render(<FixSuggestionsPanel {...props} />);
  fireEvent.click(screen.getByLabelText("Include optional AI hypotheses"));
  expect(screen.getByRole("button", { name: "Request suggestions" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByLabelText(/I consent/));
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  expect(await screen.findByText(/AI analysis: failed/)).toBeDefined();
  expect(screen.getByText("Validate the existing recovery policy")).toBeDefined();
  expect(api.getFixSuggestions).toHaveBeenCalledWith(props.traceId, props.projectId, true, true, expect.any(AbortSignal));
});

it("renders empty and safe error/retry states", async () => {
  vi.mocked(api.getFixSuggestions).mockRejectedValueOnce(new Error("secret transport error"));
  const view = render(<FixSuggestionsPanel {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  expect((await screen.findByRole("alert")).textContent).not.toContain("secret");
  vi.mocked(api.getFixSuggestions).mockResolvedValue({ ...fixesFixture, suggestions: [] });
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  expect(await screen.findByText(/No evidence-grounded repair suggestion/)).toBeDefined();
  view.unmount();
});

it("escapes untrusted text and disables missing span navigation", async () => {
  const data = structuredClone(fixesFixture);
  data.suggestions[0].proposed_change = "<img src=x onerror=alert(1)>";
  data.suggestions[0].span_ids = ["f".repeat(16)];
  vi.mocked(api.getFixSuggestions).mockResolvedValue(data);
  const { container } = render(<FixSuggestionsPanel {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  await screen.findByText(/<img src=x/);
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByRole("button", { name: /Inspect suggested span/ }).hasAttribute("disabled")).toBe(true);
});

it.each(["resolve", "reject"])("aborts and ignores stale %s when changing traces", async outcome => {
  let resolve!: (value: FixSuggestions) => void;
  let reject!: (reason: Error) => void;
  vi.mocked(api.getFixSuggestions).mockReturnValueOnce(new Promise((r, j) => { resolve = r; reject = j; }));
  const view = render(<FixSuggestionsPanel {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  const signal = vi.mocked(api.getFixSuggestions).mock.calls[0][4]!;
  view.rerender(<FixSuggestionsPanel {...props} traceId={"2".repeat(32)} />);
  expect(signal.aborted).toBe(true);
  await act(async () => { if (outcome === "resolve") resolve(fixesFixture); else reject(new Error("stale secret")); });
  expect(screen.queryByText("Validate the existing recovery policy")).toBeNull();
  view.unmount();
});

it("synchronizes suggestion span navigation with graph, detail and timeline selection", async () => {
  render(<TraceWorkspace data={{ trace: traceFixture, spans: { spans: [diagnosticTool], total: 1, truncated: false } }} />);
  await screen.findByText("Recorded execution: Recovered");
  fireEvent.click(screen.getByRole("button", { name: "Request suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: /Inspect suggested span/ }));
  expect(within(screen.getByLabelText("Graph double")).getByRole("button", { name: "Optional tool" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByLabelText("Span details").textContent).toContain("Optional tool");
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  expect(screen.getByRole("button", { name: "Timeline span Optional tool" }).getAttribute("aria-pressed")).toBe("true");
});
