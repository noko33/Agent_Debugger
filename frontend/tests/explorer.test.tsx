import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { TraceExplorer } from "@/components/trace-explorer";
import { SpanDetail } from "@/components/span-detail";
import { TraceWorkspace } from "@/components/trace-workspace";
import * as api from "@/lib/api";
import type { SpanTree } from "@/lib/span-tree";
import { pageFixture, spanFixture, traceFixture } from "./fixtures";
import { diagnosticsFixture } from "./diagnostic-fixtures";

vi.mock("@/lib/api", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getTraces: vi.fn(), getTrace: vi.fn(), getSpans: vi.fn(), getDiagnostics: vi.fn(),
}));
// jsdom has no measured canvas; actual React Flow interactions are checked in browser.
vi.mock("@/components/span-graph", () => ({ SpanGraph: ({ tree, onSelect, selectedId }: {
  tree: SpanTree; onSelect: (id: string) => void; selectedId: string | null;
}) => <div aria-label="Graph test double">{tree.nodes.map(node => <button key={node.span.span_id}
  aria-pressed={selectedId === node.span.span_id}
  onClick={() => onSelect(node.span.span_id)}>{node.span.name}</button>)}</div> }));

const root = spanFixture("1", null, { name: "Root agent", operation_type: "agent" });
const tool = spanFixture("2", "1", { name: "Optional tool", operation_type: "tool", status_code: "error",
  events: [{ name: "exception", timestamp: root.start_time, attributes: { "exception.type": "ConnectionError" } }] });
const loaded = { trace: traceFixture, spans: { spans: [root, tool], total: 2, truncated: false } };

beforeEach(() => {
  vi.mocked(api.getDiagnostics).mockReset().mockResolvedValue(diagnosticsFixture);
  vi.mocked(api.getTraces).mockReset().mockResolvedValue(pageFixture([traceFixture]));
  vi.mocked(api.getTrace).mockReset().mockResolvedValue(traceFixture);
  vi.mocked(api.getSpans).mockReset().mockResolvedValue(loaded.spans);
});

it("synchronizes graph, timeline, details and parent selection across filters", () => {
  render(<TraceWorkspace data={loaded} />);
  fireEvent.click(screen.getByRole("button", { name: "Optional tool" }));
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  expect(screen.getByRole("button", { name: "Timeline span Optional tool" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.change(screen.getByLabelText("Span kind"), { target: { value: "tool" } });
  expect(screen.getByRole("button", { name: "Timeline span Root agent" }).textContent).toContain("Context");
  fireEvent.click(screen.getByRole("button", { name: "Timeline span Root agent" }));
  fireEvent.click(screen.getByRole("button", { name: "Graph" }));
  expect(screen.getByRole("button", { name: "Root agent" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.change(screen.getByLabelText("Name search"), { target: { value: "missing" } });
  expect(screen.getByText("No spans match these filters.")).toBeDefined();
  expect(screen.getByText(/Selected span is hidden by filters/)).toBeDefined();
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Root agent")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "error" } });
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  fireEvent.click(screen.getByRole("button", { name: "Timeline span Optional tool" }));
  fireEvent.click(screen.getByRole("button", { name: "Inspect parent" }));
  expect(screen.getByRole("button", { name: "Timeline span Root agent" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("region", { name: "Performance summary" }).textContent).toContain("2 / 2");
});

it("shows overlapping bar geometry, zero markers and incomplete spans without inventing duration", () => {
  const spans = [root,
    { ...tool, start_time: "2026-10-12T00:00:00.100Z", end_time: "2026-10-12T00:00:00.700Z", duration_ms: 600 },
    spanFixture("3", "1", { start_time: "2026-10-12T00:00:00.300Z", end_time: "2026-10-12T00:00:00.800Z", duration_ms: 500 }),
    spanFixture("4", "1", { end_time: null, duration_ms: null }),
    spanFixture("5", "1", { end_time: root.start_time, duration_ms: 0 })];
  render(<TraceWorkspace data={{ ...loaded, spans: { spans, total: 5, truncated: false } }} />);
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  const first = screen.getByTestId(`bar-${tool.span_id}`);
  const second = screen.getByTestId(`bar-${spanFixture("3").span_id}`);
  expect(first.style.left).toBe("10%"); expect(first.style.width).toBe("60%");
  expect(second.style.left).toBe("30%"); expect(second.style.width).toBe("50%");
  expect(screen.getByTestId(`bar-${spanFixture("4").span_id}`).className).toContain("bar-incomplete");
  expect(screen.getByTestId(`bar-${spanFixture("5").span_id}`).style.width).toBe("0%");
});

it("marks partial statistics and selects a slowest span even when filtered out", () => {
  render(<TraceWorkspace data={{ ...loaded, spans: { ...loaded.spans, total: 10, truncated: true } }} />);
  const summary = screen.getByRole("region", { name: "Performance summary" });
  expect(summary.textContent).toContain("statistics cover 2 of 10");
  fireEvent.change(screen.getByLabelText("Name search"), { target: { value: "missing" } });
  fireEvent.click(within(summary).getByRole("button", { name: "Inspect slow span Optional tool", hidden: true }));
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Optional tool")).toBeDefined();
});

it("keeps invalid timing rows selectable without rendering a negative duration bar", () => {
  const span = { ...tool, start_time: "2026-10-12T00:00:00.700Z", end_time: "2026-10-12T00:00:00.100Z", duration_ms: -600 };
  render(<TraceWorkspace data={{ ...loaded, spans: { spans: [root, span], total: 2, truncated: false } }} />);
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  expect(screen.getByText("Invalid timing; duration unavailable.")).toBeDefined();
  expect(screen.getByTestId(`bar-${tool.span_id}`).style.width).toBe("0%");
  fireEvent.click(screen.getByRole("button", { name: "Timeline span Optional tool" }));
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Invalid timing")).toBeDefined();
});

it("shows loading and initial trace selection guidance", () => {
  vi.mocked(api.getTraces).mockReturnValue(new Promise(() => {}));
  render(<TraceExplorer />);
  expect(screen.getByRole("status").textContent).toContain("Loading traces");
  expect(screen.getByText("Explore an execution")).toBeDefined();
});
it("handles empty trace results", async () => {
  vi.mocked(api.getTraces).mockResolvedValue(pageFixture([]));
  render(<TraceExplorer />);
  expect(await screen.findByText(/No traces found/)).toBeDefined();
});
it("handles API errors and retries without showing transport details", async () => {
  vi.mocked(api.getTraces).mockRejectedValueOnce(new Error("secret response"));
  render(<TraceExplorer />);
  expect((await screen.findByRole("alert")).textContent).not.toContain("secret");
  fireEvent.click(screen.getByRole("button", { name: "Retry traces" }));
  expect(await screen.findByRole("button", { name: /Test agent/ })).toBeDefined();
});
it("loads selected trace, selects a graph span, and navigates to its parent", async () => {
  render(<TraceExplorer />);
  fireEvent.click(await screen.findByRole("button", { name: /Test agent/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Optional tool" }));
  const details = screen.getByRole("complementary", { name: "Span details" });
  expect(within(details).getByText("Optional tool")).toBeDefined();
  expect(within(details).getByText("Exception recorded")).toBeDefined();
  expect(within(details).getByText(/ConnectionError/)).toBeDefined();
  fireEvent.click(within(details).getByRole("button", { name: "Inspect parent" }));
  expect(within(details).getByText("Root agent")).toBeDefined();
  expect(api.getTrace).toHaveBeenCalledWith(traceFixture.trace_id, expect.any(AbortSignal));
});
it("supports keyboard-accessible span list selection", () => {
  render(<TraceWorkspace data={loaded} />);
  fireEvent.click(screen.getByRole("button", { name: "Span list" }));
  fireEvent.click(within(screen.getByRole("list", { name: "Spans" })).getByRole("button", { name: /Optional tool/ }));
  expect(within(screen.getByRole("complementary", { name: "Span details" })).getByText("Optional tool")).toBeDefined();
});
it("shows empty spans and incomplete graph warnings", () => {
  render(<TraceWorkspace data={{ ...loaded, spans: { spans: [], total: 2, truncated: true } }} />);
  expect(screen.getByText("No spans recorded for this trace.")).toBeDefined();
  expect(screen.getByRole("alert").textContent).toContain("Partial graph");
});
it("handles detail API errors with a targeted retry", async () => {
  vi.mocked(api.getSpans).mockRejectedValueOnce(new Error("Trace API returned HTTP 503."));
  render(<TraceExplorer />);
  fireEvent.click(await screen.findByRole("button", { name: /Test agent/ }));
  expect((await screen.findByRole("alert")).textContent).toContain("503");
  fireEvent.click(screen.getByRole("button", { name: "Retry trace" }));
  expect(await screen.findByRole("button", { name: "Root agent" })).toBeDefined();
});
it("paginates the trace list", async () => {
  vi.mocked(api.getTraces).mockResolvedValue(pageFixture([traceFixture], 21));
  render(<TraceExplorer />);
  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() => expect(api.getTraces).toHaveBeenLastCalledWith(20, expect.any(AbortSignal)));
});
it("renders untrusted attributes as text and never invents token usage", () => {
  const span = spanFixture("1", null, { operation_type: "llm", attributes: {
    payload: '<img src=x onerror="alert(1)">', "gen_ai.usage.input_tokens": 0,
  } });
  const { container } = render(<SpanDetail span={span} parentAvailable={false} onSelectParent={() => {}} />);
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain("<img");
  expect(screen.getByText("0")).toBeDefined();
  expect(screen.getByText("Not recorded")).toBeDefined();
});
it("ignores stale detail responses after a different trace is selected", async () => {
  const second = { ...traceFixture, name: "Second trace", trace_id: "2".repeat(32) };
  vi.mocked(api.getTraces).mockResolvedValue(pageFixture([traceFixture, second]));
  let resolveFirst!: (trace: typeof traceFixture) => void;
  vi.mocked(api.getTrace).mockImplementation(id => id === traceFixture.trace_id
    ? new Promise(resolve => { resolveFirst = resolve; }) : Promise.resolve(second));
  render(<TraceExplorer />);
  fireEvent.click(await screen.findByRole("button", { name: /Test agent/ }));
  fireEvent.click(screen.getByRole("button", { name: /Second trace/ }));
  const summary = await screen.findByRole("region", { name: "Trace metadata" });
  expect(summary.textContent).toContain("Second trace");
  resolveFirst(traceFixture);
  await waitFor(() => expect(summary.textContent).not.toContain("Test agent"));
});
