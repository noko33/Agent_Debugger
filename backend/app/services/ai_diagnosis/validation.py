"""Reference integrity and conservative claim checks, not a semantic truth oracle."""

import re

from pydantic import ValidationError

from app.schemas.ai_diagnosis import ModelOutput
from app.services.ai_diagnosis.context import BuiltContext
from app.services.ai_diagnosis.provider import ProviderFailure

CATEGORIES = {
    "tool_operation_failure": "TOOL_EXECUTION_ERROR",
    "llm_operation_failure": "LLM_EXECUTION_ERROR",
    "repeated_operation_failure": "REPEATED_TOOL_FAILURE",
    "possible_retry_pattern": "POSSIBLE_RETRY_LOOP",
    "performance_bottleneck": "SLOW_OPERATION",
    "trace_data_gap": "INCOMPLETE_OR_INVALID_TRACE",
}
UNSAFE_TEXT = re.compile(
    r"\b(?:confirmed|proven|definitely|certainly|confidence|probability)\b|\b\d+(?:\.\d+)?\s*%|"
    r"已证实|已确认|确定根因|置信度|https?://|\bBearer\s+\S+|"
    r"sk-[a-zA-Z0-9_-]{8,}|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|"
    r"(?:password|api[_ -]?key|secret|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def validate_output(content: str, context: BuiltContext, actual_span_ids: set[str]) -> ModelOutput:
    try:
        output = ModelOutput.model_validate_json(content)
    except (ValidationError, ValueError):
        raise ProviderFailure("invalid_model_output") from None
    registry = {fact.evidence_id: fact for fact in context.facts}
    included = set(context.report.included_span_ids) & actual_span_ids
    ids = [hypothesis.hypothesis_id for hypothesis in output.hypotheses]
    if len(ids) != len(set(ids)):
        raise ProviderFailure("invalid_model_output")
    for hypothesis in output.hypotheses:
        if not set(hypothesis.span_ids) <= included:
            raise ProviderFailure("invalid_evidence_reference")
        if not set(hypothesis.supporting_evidence) <= registry.keys():
            raise ProviderFailure("invalid_evidence_reference")
        citations = [registry[key] for key in hypothesis.supporting_evidence]
        covered = {sid for fact in citations for sid in fact.span_ids}
        if not set(hypothesis.span_ids) <= covered:
            raise ProviderFailure("invalid_evidence_reference")
        if hypothesis.support_status != "cannot_be_determined":
            expected = CATEGORIES.get(hypothesis.cause_type)
            matched = [
                fact
                for fact in citations
                if fact.source == "rule_finding" and fact.values.get("category") == expected
            ]
            if not expected or not matched:
                raise ProviderFailure("unsupported_claim")
            if hypothesis.cause_type != "trace_data_gap" and not hypothesis.span_ids:
                raise ProviderFailure("unsupported_claim")
            matched_spans = {sid for fact in matched for sid in fact.span_ids}
            if not set(hypothesis.span_ids) <= matched_spans:
                raise ProviderFailure("unsupported_claim")
            if hypothesis.support_status == "supported" and not any(
                fact.values.get("nature") == "observed_failure" for fact in matched
            ):
                raise ProviderFailure("unsupported_claim")
        elif hypothesis.cause_type != "cannot_determine":
            raise ProviderFailure("unsupported_claim")
        texts = [
            hypothesis.title,
            hypothesis.possible_root_cause,
            *hypothesis.missing_evidence,
            *hypothesis.limitations,
        ]
        if any(UNSAFE_TEXT.search(value) for value in texts):
            raise ProviderFailure("unsafe_model_claim")
        if any(
            identifier not in included
            for value in texts
            for identifier in re.findall(r"\b[0-9a-f]{16}\b", value)
        ):
            raise ProviderFailure("invalid_evidence_reference")
        if context.payload["recorded_outcome"] in ("recovered_execution", "recorded_success"):
            if any(
                re.search(
                    r"(?:agent|overall|entire execution)\s+(?:execution\s+)?"
                    r"(?:failed|failure)|整体失败|Agent失败",
                    value,
                    re.IGNORECASE,
                )
                for value in texts
            ):
                raise ProviderFailure("outcome_contradiction")
    return output
