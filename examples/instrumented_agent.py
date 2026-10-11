"""Explicit Tool/LLM instrumentation without any paid provider or fabricated tokens."""

import argparse
import os

import httpx
from opentelemetry.trace import Status, StatusCode

from agent_debugger import AgentDebugger, CapturePolicy, trace_llm, trace_tool


def run_agent(debugger: AgentDebugger) -> str:
    @trace_llm(debugger, name="Plan action", model="demo-planner", provider="mock")
    def plan(question: str) -> str:
        # This deterministic stand-in has no tokenizer, so no usage is reported.
        return "lookup"

    @trace_tool(debugger, name="Lookup", capture=CapturePolicy(result=True))
    def lookup() -> dict[str, str]:
        with debugger.span("Read local catalog"):
            return {"answer": "Agent Debugger records execution traces."}

    @trace_tool(debugger, name="Optional enrichment")
    def enrich() -> None:
        raise ConnectionError("Demo optional source unavailable")

    @trace_tool(debugger, name="Fallback")
    def fallback() -> str:
        return "Use the local catalog."

    @trace_llm(debugger, name="Final answer", model="demo-writer", provider="mock")
    def answer(facts: dict[str, str]) -> str:
        return facts["answer"]

    with debugger.trace("Instrumented agent", record_exception_details=False) as root:
        plan("What is Agent Debugger?")
        facts = lookup()
        try:
            enrich()
        except ConnectionError:
            root.add_event("recovery", {"strategy": "local_catalog"})
            fallback()
        answer(facts)
        root.set_status(Status(StatusCode.OK))
    return f"{root.get_span_context().trace_id:032x}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url", default=os.getenv("AGENT_DEBUGGER_API_URL", "http://localhost:8000")
    )
    parser.add_argument("--project-id", default=os.getenv("AGENT_DEBUGGER_PROJECT_ID"))
    parser.add_argument("--create-project", action="store_true")
    args = parser.parse_args()
    project_id = args.project_id
    if args.create_project:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(
                f"{args.api_url.rstrip('/')}/api/v1/projects", json={"name": "Phase 2B example"}
            )
            response.raise_for_status()
            project_id = response.json()["id"]
    if not project_id:
        parser.error("Supply --project-id or --create-project")
    with AgentDebugger(project_id, args.api_url) as debugger:
        trace_id = run_agent(debugger)
        print(f"Project: {project_id}\nTrace: {trace_id}")
        print(f"Export succeeded: {debugger.exporter.last_export_succeeded}")


if __name__ == "__main__":
    main()
