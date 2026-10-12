"""Stateless deterministic proposals enriched only by already validated Phase 5A references."""

import hashlib
import json

from app.schemas.ai_diagnosis import AIDiagnosisResponse, ContextFact
from app.schemas.fix_suggestions import FixRequest, FixResponse, Suggestion
from app.schemas.traces import SpanRead, TraceRead
from app.services.ai_diagnosis.service import AIDiagnosisService
from app.services.fix_templates import TEMPLATES


def stable_id(prefix: str, *values: object) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


def generate_suggestions(analysis: AIDiagnosisResponse, spans: list[SpanRead]) -> FixResponse:
    diagnostics = analysis.deterministic
    by_id = {span.span_id: span for span in spans}
    finding_ids = [
        stable_id("f", diagnostics.trace_id, f.rule_id, sorted(f.span_ids))
        for f in diagnostics.findings
    ]
    evidence = []
    proposals = {}
    repeated = {
        sid
        for f in diagnostics.findings
        if f.category == "REPEATED_TOOL_FAILURE"
        for sid in f.span_ids
    }
    for finding, fid in zip(diagnostics.findings, finding_ids, strict=True):
        ids = sorted({sid for sid in finding.span_ids if sid in by_id})
        keys = []
        if finding.category == "REPEATED_TOOL_FAILURE":
            keys = ["repeated"]
        elif finding.category == "POSSIBLE_RETRY_LOOP":
            keys = ["retry"]
        elif finding.category == "SLOW_OPERATION":
            keys = ["slow"]
        elif finding.category == "INCOMPLETE_OR_INVALID_TRACE":
            keys = ["missing"]
        elif finding.category == "TOOL_EXECUTION_ERROR":
            if not set(ids) <= repeated:
                keys = [
                    {
                        "recovered_execution": "tool_recovered",
                        "unrecovered_execution": "tool_unrecovered",
                    }.get(diagnostics.outcome.classification, "unknown")
                ]
        elif finding.category == "LLM_EXECUTION_ERROR":
            keys = ["unknown"]
            # Strict caller-reported checks, not inference from error strings or model text.
            if ids and all(
                by_id[sid].attributes.get("agent_debugger.validation.kind")
                == "instruction_compliance"
                and by_id[sid].attributes.get("agent_debugger.validation.passed") is False
                for sid in ids
            ):
                keys.append("prompt")
        for key in keys:
            category, title, explanation, change, expected, risk, precondition = TEMPLATES[key]
            sid = stable_id("s", diagnostics.trace_id, "5b.1", key, ids)
            fact_id = fid + (":prompt_check" if key == "prompt" else "")
            values = {
                "rule_id": finding.rule_id,
                "category": finding.category.value,
                "nature": finding.nature.value,
                "related_span_count": len(ids),
            }
            if key == "prompt":
                values.update(validation_kind="instruction_compliance", validation_passed=False)
            fact = ContextFact(
                evidence_id=fact_id, source="rule_finding", span_ids=ids, values=values
            )
            if not any(item.evidence_id == fact_id for item in evidence):
                evidence.append(fact)
            hypothesis_ids = [
                h.hypothesis_id
                for h in analysis.hypotheses
                if h.support_status != "cannot_be_determined"
                and set(h.span_ids) & set(ids)
                and any(
                    f.evidence_id in h.supporting_evidence
                    and f.values.get("category") == finding.category.value
                    for f in analysis.evidence
                    if f.source == "rule_finding"
                )
            ]
            proposals[sid] = Suggestion(
                suggestion_id=sid,
                category=category,
                title=title,
                explanation=explanation,
                basis={"observed_failure": "recorded_observation"}.get(
                    finding.nature.value, finding.nature.value
                ),
                related_finding_ids=[fid],
                span_ids=ids,
                supporting_evidence=[fact_id],
                hypothesis_ids=hypothesis_ids,
                proposed_change=change,
                expected_observable_improvement=expected,
                risks_and_tradeoffs=[risk],
                preconditions=[precondition],
                missing_evidence=[
                    "Actual policy/configuration, semantic acceptance criteria and "
                    "a comparable validation baseline are not established."
                ],
                validation_plan=[
                    "Capture a redacted baseline with the referenced operation and review the "
                    "evidence.",
                    "Change one reviewed policy in a controlled local experiment; include success, "
                    "failure and side-effect cases. No change is executed by Agent Debugger.",
                    "Compare final status, recorded recovery, attempts, durations and result "
                    "checks; "
                    "retain the change only if predefined checks pass and regressions are absent.",
                ],
            )
    if not proposals and (
        diagnostics.operation_failure_count or diagnostics.outcome.classification == "unknown"
    ):
        ids = sorted({e.span_id for e in diagnostics.outcome.evidence if e.span_id in by_id})
        category, title, explanation, change, expected, risk, precondition = TEMPLATES["unknown"]
        evidence.append(
            ContextFact(
                evidence_id="recorded_outcome",
                source="recorded_outcome",
                span_ids=ids,
                values={
                    "classification": diagnostics.outcome.classification.value,
                    "operation_failure_count": diagnostics.operation_failure_count,
                },
            )
        )
        proposal = Suggestion(
            suggestion_id=stable_id("s", diagnostics.trace_id, "5b.1", "outcome_context", ids),
            category=category,
            title=title,
            explanation="Recorded outcome or operation evidence is insufficient to select a fix.",
            basis="data_quality",
            related_finding_ids=[],
            span_ids=ids,
            supporting_evidence=["recorded_outcome"],
            proposed_change=change,
            expected_observable_improvement=expected,
            risks_and_tradeoffs=[risk],
            preconditions=[precondition],
            missing_evidence=["Operation-specific failure cause and handling context are unknown."],
            validation_plan=[
                "Record terminal status and safe error/recovery metadata in a controlled "
                "run; confirm outcome classification and review semantic result checks."
            ],
        )
        proposals[proposal.suggestion_id] = proposal
    selected = sorted(proposals.values(), key=lambda s: s.suggestion_id)[:100]
    used = {eid for s in selected for eid in s.supporting_evidence}
    return FixResponse(
        trace_id=diagnostics.trace_id,
        project_id=diagnostics.project_id,
        analysis=analysis,
        finding_ids=list(dict.fromkeys(finding_ids)),
        evidence=[e for e in evidence if e.evidence_id in used],
        suggestions=selected,
        omitted_suggestion_count=max(0, len(proposals) - len(selected)),
    )


class FixSuggestionService:
    def __init__(self, analysis_service: AIDiagnosisService) -> None:
        self.analysis_service = analysis_service

    def analyze(self, snapshot: tuple[TraceRead, list[SpanRead]], body: FixRequest) -> FixResponse:
        service = self.analysis_service
        if not body.include_ai:
            service = AIDiagnosisService(
                service.settings.model_copy(update={"enabled": False}),
                service.parameters,
                service.guard,
                service.provider,
            )
        analysis = service.analyze(snapshot, body.consent_to_external_processing)
        if not body.include_ai:
            analysis.execution.message = (
                "AI enrichment was not requested; no external analysis ran."
            )
        return generate_suggestions(analysis, snapshot[1])
