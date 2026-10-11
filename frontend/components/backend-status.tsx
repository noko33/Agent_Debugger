"use client";

import { useBackendHealth } from "@/hooks/use-backend-health";
import { apiBaseUrl } from "@/lib/api";

export function BackendStatus() {
  const { connection, retry } = useBackendHealth();
  const label = {
    checking: "Checking backend…",
    connected: "Backend connected",
    unavailable: "Backend unavailable",
  }[connection.status];

  return (
    <section className="rounded-xl border border-slate-700 bg-slate-900 p-6" aria-label="Backend connection">
      <div role="status" aria-live="polite">
        <p className={connection.status === "connected" ? "text-teal-300" : "text-slate-200"}>
          {label}
        </p>
        <p className="mt-2 break-all text-sm text-slate-400">{apiBaseUrl}/health</p>
        {connection.status === "unavailable" && (
          <p className="mt-3 text-sm text-slate-300">{connection.message}</p>
        )}
      </div>
      <button
        type="button"
        onClick={retry}
        disabled={connection.status === "checking"}
        className="mt-4 rounded-md border border-slate-600 px-4 py-2 text-sm hover:bg-slate-800 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-300 disabled:opacity-50"
      >
        Check again
      </button>
    </section>
  );
}
