"""Scripted provider outputs test contracts and safeguards, never diagnosis accuracy."""

import json

from app.services.ai_diagnosis.provider import ProviderFailure, ProviderResult

CAUSES = {
    "TOOL_EXECUTION_ERROR": "tool_operation_failure",
    "LLM_EXECUTION_ERROR": "llm_operation_failure",
    "REPEATED_TOOL_FAILURE": "repeated_operation_failure",
    "POSSIBLE_RETRY_LOOP": "possible_retry_pattern",
    "SLOW_OPERATION": "performance_bottleneck",
    "INCOMPLETE_OR_INVALID_TRACE": "trace_data_gap",
}


def mock_hypotheses(context: dict) -> dict:
    facts = [fact for fact in context["facts"] if fact["source"] == "rule_finding"][:5]
    return {
        "hypotheses": [
            {
                "hypothesis_id": f"h{index + 1}",
                "title": "Recorded operation pattern",
                "claim_kind": "hypothesis",
                "cause_type": CAUSES[fact["values"]["category"]],
                "possible_root_cause": "The recorded pattern may explain this operation; "
                "the underlying external condition remains unknown.",
                "supporting_evidence": [fact["evidence_id"]],
                "span_ids": fact["span_ids"][:20],
                "missing_evidence": [
                    "Underlying service response and semantic task result are unavailable."
                ],
                "limitations": ["Recorded correlation does not establish causality."],
                "support_status": "supported"
                if fact["values"]["nature"] == "observed_failure"
                else "weakly_supported",
            }
            for index, fact in enumerate(facts)
        ]
    }


class ScriptedProvider:
    def __init__(self, output: dict | None = None, failure: str | None = None) -> None:
        self.output = output
        self.failure = failure
        self.calls: list[dict] = []

    def analyze(self, context: dict) -> ProviderResult:
        self.calls.append(context)
        if self.failure:
            raise ProviderFailure(self.failure, 1)
        return ProviderResult(
            json.dumps(self.output if self.output is not None else mock_hypotheses(context)),
            1,
            100,
            50,
        )
