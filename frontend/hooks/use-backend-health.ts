"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";

type ConnectionState =
  | { status: "checking" }
  | { status: "connected" }
  | { status: "unavailable"; message: string };

export function useBackendHealth() {
  const [connection, setConnection] = useState<ConnectionState>({ status: "checking" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = setTimeout(() => controller.abort(), 5000);

    getHealth(controller.signal)
      .then(() => {
        if (active) setConnection({ status: "connected" });
      })
      .catch(() => {
        if (active) {
          setConnection({
            status: "unavailable",
            message: "Check that the API is running and its URL and CORS settings match.",
          });
        }
      })
      .finally(() => clearTimeout(timeout));

    return () => {
      active = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  function retry() {
    setConnection({ status: "checking" });
    setAttempt((value) => value + 1);
  }

  return { connection, retry };
}
