export type HealthResponse = {
  status: "ok";
  service: "agent-debugger-api";
};

export const apiBaseUrl = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/health`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${response.status}`);
  }
  const data: unknown = await response.json();
  if (
    typeof data !== "object" || data === null ||
    !("status" in data) || data.status !== "ok" ||
    !("service" in data) || data.service !== "agent-debugger-api"
  ) {
    throw new Error("Backend returned an unexpected health response");
  }
  return { status: "ok", service: "agent-debugger-api" };
}
