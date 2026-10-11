import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { SpanGraph } from "@/components/span-graph";
import { buildSpanTree } from "@/lib/span-tree";
import { spanFixture } from "./fixtures";

it("real React Flow nodes select once and accept externally selected parents without feedback", async () => {
  // jsdom cannot measure a canvas, but the actual React Flow/node components render.
  vi.stubGlobal("ResizeObserver", class {
    timers = new Set<ReturnType<typeof setTimeout>>();
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      const timer = setTimeout(() => {
        this.timers.delete(timer);
        this.callback([{ target, contentRect: { width: 800, height: 600 } } as ResizeObserverEntry],
          this as unknown as ResizeObserver);
      }, 0);
      this.timers.add(timer);
    }
    unobserve() {}
    disconnect() { for (const timer of this.timers) clearTimeout(timer); }
  });
  vi.stubGlobal("DOMMatrixReadOnly", class { m22 = 1; });
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(260);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(100);
  const tree = buildSpanTree([spanFixture("1"), spanFixture("2", "1")]);
  const onSelect = vi.fn();
  const { rerender } = render(<SpanGraph tree={tree} selectedId={null} onSelect={onSelect} />);
  fireEvent.click(await screen.findByRole("button", { name: "Inspect span Span 2" }));
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(spanFixture("2").span_id);
  rerender(<SpanGraph tree={tree} selectedId={spanFixture("1").span_id} onSelect={onSelect} />);
  expect((await screen.findByRole("button", { name: "Inspect span Span 1" })).getAttribute("aria-pressed")).toBe("true");
  expect(onSelect).toHaveBeenCalledTimes(1);
});
