"use client";

import { useEffect, useRef, useState } from "react";
import { apiErrorMessage, getFixSuggestions } from "@/lib/api";
import type { FixSuggestions } from "@/lib/fix-suggestions";
import type { LoadState } from "./use-trace-explorer";

export function useFixSuggestions(traceId: string, projectId: string) {
  const active = useRef<AbortController | null>(null);
  const [result, setResult] = useState<{ key: string; state: LoadState<FixSuggestions> } | null>(null);
  const key = `${projectId}:${traceId}`;
  useEffect(() => { setResult(null); return () => { active.current?.abort(); }; }, [key]);
  async function request(includeAi: boolean, consent: boolean) {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setResult({ key, state: { status: "loading" } });
    try {
      const data = await getFixSuggestions(traceId, projectId, includeAi, consent, controller.signal);
      if (!controller.signal.aborted) setResult({ key, state: { status: "ready", data } });
    } catch (error) {
      if (!controller.signal.aborted) setResult({ key, state: { status: "error", message: apiErrorMessage(error) } });
    }
  }
  return { state: result?.key === key ? result.state : null, request };
}
