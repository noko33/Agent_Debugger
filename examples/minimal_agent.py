"""A deterministic Agent example: explicit spans, no LLM/API keys or agent framework."""

import argparse
import os

import httpx
from opentelemetry.trace import Status, StatusCode, format_trace_id

from agent_debugger import AgentDebugger


def run_agent(project_id: str, api_url: str) -> str:
    with AgentDebugger(project_id, api_url) as debugger:
        with debugger.trace(
            "Answer a question", attributes={"agent.name": "minimal-agent"}
        ) as root:
            with debugger.span("Plan", operation_type="agent"):
                with debugger.span("Lookup", operation_type="tool") as lookup:
                    lookup.set_attribute("tool.name", "local_lookup")
                    lookup.add_event("state.changed", {"state": "found"})
                    answer = "Agent Debugger stores execution traces."
            try:
                with debugger.span("Optional enrichment", operation_type="tool"):
                    raise ValueError("Demonstration: optional source unavailable")
            except ValueError:
                # This expected application failure is handled by the Agent, not by the SDK.
                with debugger.span("Fallback", operation_type="runtime"):
                    pass
            with debugger.span("Respond", operation_type="runtime"):
                print(answer)
            root.set_status(Status(StatusCode.OK))
        trace_id = format_trace_id(root.get_span_context().trace_id)
        print("Trace ID:", trace_id)
        print("Export succeeded:", debugger.exporter.last_export_succeeded)
        return trace_id


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
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.post(
                args.api_url.rstrip("/") + "/api/v1/projects", json={"name": "Minimal SDK example"}
            )
            response.raise_for_status()
            project_id = response.json()["id"]
        print("Project ID:", project_id)
    if not project_id:
        parser.error("Provide --project-id or --create-project")
    run_agent(project_id, args.api_url)


if __name__ == "__main__":
    main()
