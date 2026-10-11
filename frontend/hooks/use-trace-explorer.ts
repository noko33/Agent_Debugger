"use client";

import { useEffect, useState } from "react";
import { apiErrorMessage, getSpans, getTrace, getTraces, type LoadedSpans } from "@/lib/api";
import type { Page, TraceRecord } from "@/lib/traces";

export type LoadState<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };
export interface TraceDetail { trace: TraceRecord; spans: LoadedSpans }

export function useTraceExplorer() {
  const [offset, setOffset] = useState(0);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);
  const [list, setList] = useState<LoadState<Page<TraceRecord>>>({ status: "loading" });
  const [detail, setDetail] = useState<LoadState<TraceDetail> | null>(null);
  const [listAttempt, setListAttempt] = useState(0);
  const [detailAttempt, setDetailAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setList({ status: "loading" });
    getTraces(offset, controller.signal).then(data => {
      if (!controller.signal.aborted) setList({ status: "ready", data });
    }).catch(error => {
      if (!controller.signal.aborted) setList({ status: "error", message: apiErrorMessage(error) });
    });
    return () => controller.abort();
  }, [offset, listAttempt]);

  useEffect(() => {
    if (!selectedTrace) { setDetail(null); return; }
    const controller = new AbortController();
    setDetail({ status: "loading" });
    Promise.all([getTrace(selectedTrace, controller.signal), getSpans(selectedTrace, controller.signal)])
      .then(([trace, spans]) => {
        if (!controller.signal.aborted) setDetail({ status: "ready", data: { trace, spans } });
      }).catch(error => {
        if (!controller.signal.aborted) setDetail({ status: "error", message: apiErrorMessage(error) });
      });
    return () => controller.abort();
  }, [selectedTrace, detailAttempt]);

  function selectTrace(traceId: string) {
    if (traceId === selectedTrace) return;
    setDetail({ status: "loading" });
    setSelectedTrace(traceId);
  }
  return {
    offset, setOffset, list, detail, selectedTrace, selectTrace,
    refreshList: () => setListAttempt(value => value + 1),
    refreshDetail: () => setDetailAttempt(value => value + 1),
  };
}
