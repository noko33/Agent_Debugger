import { expect, it, vi } from "vitest";
import { apiErrorMessage, getSpans, getTrace, getTraces } from "@/lib/api";
import { pageFixture, spanFixture, traceFixture } from "./fixtures";

function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }

it("uses existing trace pagination and validates responses", async () => {
  const fetcher = vi.fn().mockResolvedValue(response(pageFixture([traceFixture], 25, 20, 20)));
  vi.stubGlobal("fetch", fetcher);
  expect((await getTraces(20)).items[0].trace_id).toBe(traceFixture.trace_id);
  expect(fetcher.mock.calls[0][0]).toContain("limit=20&offset=20");
});
it("loads span pages beyond the default API page and includes events", async () => {
  const first = Array.from({ length: 500 }, (_, i) => spanFixture((i + 1).toString(16)));
  const fetcher = vi.fn().mockResolvedValueOnce(response(pageFixture(first, 501, 500)))
    .mockResolvedValueOnce(response(pageFixture([spanFixture("1f6")], 501, 500, 500)));
  vi.stubGlobal("fetch", fetcher);
  const loaded = await getSpans(traceFixture.trace_id);
  expect(loaded.spans).toHaveLength(501);
  expect(loaded.truncated).toBe(false);
  expect(fetcher.mock.calls[1][0]).toContain("offset=500");
});
it("clearly reports the large-graph bound", async () => {
  const spans = Array.from({ length: 500 }, (_, i) => spanFixture((i + 1).toString(16)));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => pageFixture(spans, 5001, 500) }));
  const loaded = await getSpans(traceFixture.trace_id);
  expect(loaded.spans).toHaveLength(5000);
  expect(loaded.truncated).toBe(true);
});
it("does not treat early empty pages as a complete tree", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(pageFixture([], 9, 500))));
  expect((await getSpans(traceFixture.trace_id)).truncated).toBe(true);
});
it("surfaces HTTP status without exposing untrusted error bodies", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ secret: "private" }, 503)));
  await expect(getTraces()).rejects.toThrow("HTTP 503");
  expect(apiErrorMessage(new Error("private transport"))).not.toContain("private");
});
it("rejects malformed pages and invalid IDs", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ items: [{ name: "bad" }], total: 1, limit: 20, offset: 0 })));
  await expect(getTraces()).rejects.toThrow("unexpected response");
  await expect(getTrace("bad/id")).rejects.toThrow("Invalid trace ID");
});
it("rejects mismatched trace identities", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...traceFixture, trace_id: "2".repeat(32) })));
  await expect(getTrace(traceFixture.trace_id)).rejects.toThrow("unexpected trace");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(pageFixture([
    spanFixture("1", null, { trace_id: "2".repeat(32) }),
  ], 1, 500))));
  await expect(getSpans(traceFixture.trace_id)).rejects.toThrow("mismatch");
});
