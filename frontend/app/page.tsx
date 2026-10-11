import { BackendStatus } from "@/components/backend-status";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-4">
        <p className="text-sm font-medium uppercase tracking-widest text-teal-300">
          Phase 0 · Project foundation
        </p>
        <h1 className="text-5xl font-semibold tracking-tight">Agent Debugger</h1>
        <p className="max-w-xl text-lg leading-8 text-slate-300">
          Trace-based debugging and failure analysis for LLM agents.
          Understand executions, investigate failures, and improve agent behavior.
        </p>
      </header>
      <BackendStatus />
      <p className="text-sm leading-6 text-slate-400">
        The project infrastructure is being initialized. Trace ingestion,
        execution visualization, and failure diagnosis are planned for later phases.
      </p>
    </main>
  );
}
