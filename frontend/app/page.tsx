import { TraceExplorer } from "@/components/trace-explorer";

export default function Home() {
  return (
    <main className="app-shell">
      <header className="app-header">
        <div><p className="eyebrow">Agent Debugger</p><h1>Trace Explorer</h1></div>
        <p>Inspect executions, follow tool calls, and explore recorded events.</p>
      </header>
      <TraceExplorer />
    </main>
  );
}
