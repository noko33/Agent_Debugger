"use client";

import { useEffect, useState } from "react";
import { apiErrorMessage, getDiagnostics } from "@/lib/api";
import type { Diagnostics } from "@/lib/diagnostics";
import type { LoadState } from "./use-trace-explorer";

export function useDiagnostics(traceId: string, projectId: string) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ traceId: string; projectId: string; attempt: number; state: LoadState<Diagnostics> } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const save = (state: LoadState<Diagnostics>) => {
      if (!controller.signal.aborted) setResult({ traceId, projectId, attempt, state });
    };
    save({ status: "loading" });
    getDiagnostics(traceId, projectId, controller.signal).then(data => save({ status: "ready", data }))
      .catch(error => save({ status: "error", message: apiErrorMessage(error) }));
    return () => controller.abort();
  }, [traceId, projectId, attempt]);
  // Tag state as well as aborting: old results never render during a trace/retry transition.
  const state: LoadState<Diagnostics> = result?.traceId === traceId && result.projectId === projectId && result.attempt === attempt
    ? result.state : { status: "loading" };
  return { state, retry: () => setAttempt(value => value + 1) };
}
