"""Compose existing suggestion, runner and comparison contracts. Never apply a suggestion."""

from pydantic import TypeAdapter

from app.schemas.evaluation import EvaluationDataset, RunnerConfiguration
from app.schemas.fix_suggestions import FixResponse, Suggestion
from app.schemas.fix_validation import DeveloperChange, FixValidationResult, ImplementationReference
from app.schemas.traces import Name
from app.services.evaluation import compare_runs
from app.services.evaluation_runner import TraceResolver, TrustedEvaluationRunner
from app.services.fix_suggestions import stable_id
from app.services.replay import canonical, digest


def _select(response: FixResponse, suggestion_id: str) -> Suggestion:
    diagnostics = response.analysis.deterministic
    if response.trace_id != diagnostics.trace_id or response.project_id != diagnostics.project_id:
        raise ValueError("Suggestion source analysis does not match trace/project")
    findings = {
        stable_id("f", diagnostics.trace_id, f.rule_id, sorted(f.span_ids)): f
        for f in diagnostics.findings
    }
    if set(response.finding_ids) != findings.keys():
        raise ValueError("Suggestion finding references do not match source analysis")
    ids = [s.suggestion_id for s in response.suggestions]
    if len(set(ids)) != len(ids) or suggestion_id not in ids:
        raise ValueError("Suggestion identifier is absent or ambiguous")
    selected = next(s for s in response.suggestions if s.suggestion_id == suggestion_id)
    evidence = {e.evidence_id: e for e in response.evidence}
    if len(evidence) != len(response.evidence) or len(set(response.finding_ids)) != len(
        response.finding_ids
    ):
        raise ValueError("Suggestion evidence/finding identifiers are ambiguous")
    known_spans = {sid for f in diagnostics.findings for sid in f.span_ids} | {
        e.span_id for e in diagnostics.outcome.evidence if e.span_id is not None
    }
    if (
        not set(selected.related_finding_ids) <= findings.keys()
        or not set(selected.supporting_evidence) <= evidence.keys()
        or not set(selected.span_ids) <= known_spans
        or not selected.supporting_evidence
        or any(
            not set(evidence[eid].span_ids) <= known_spans for eid in selected.supporting_evidence
        )
    ):
        raise ValueError("Suggestion contains missing finding/span/evidence references")
    if not set(selected.span_ids) <= {
        sid for eid in selected.supporting_evidence for sid in evidence[eid].span_ids
    }:
        raise ValueError("Suggestion spans lack supporting evidence references")
    if selected.related_finding_ids and not set(selected.span_ids) <= {
        sid for fid in selected.related_finding_ids for sid in findings[fid].span_ids
    }:
        raise ValueError("Suggestion spans do not belong to related findings")
    return selected


class FixValidationService:
    def __init__(self, runner: TrustedEvaluationRunner) -> None:
        self.runner = runner

    def validate(
        self,
        response: FixResponse,
        suggestion_id: str,
        dataset: EvaluationDataset,
        *,
        validation_id: str,
        baseline: ImplementationReference,
        candidate: ImplementationReference,
        change: DeveloperChange,
        configuration: RunnerConfiguration | None = None,
        resolve_trace: TraceResolver | None = None,
        provenance: dict[str, str] | None = None,
    ) -> FixValidationResult:
        # Detach/revalidate all caller-owned objects before executing any Agent.
        validation_id = TypeAdapter(Name).validate_python(validation_id)
        response = FixResponse.model_validate_json(response.model_dump_json())
        selected = _select(response, suggestion_id)
        change = DeveloperChange.model_validate_json(change.model_dump_json())
        if not change.approved:
            raise ValueError("Candidate change requires explicit developer approval")
        baseline = ImplementationReference.model_validate_json(baseline.model_dump_json())
        candidate = ImplementationReference.model_validate_json(candidate.model_dump_json())
        if (baseline.implementation_id, baseline.configuration_id) == (
            candidate.implementation_id,
            candidate.configuration_id,
        ):
            raise ValueError(
                "Candidate must identify a developer-supplied implementation/config change"
            )
        self.runner.require_distinct_agents(baseline.registered_name, candidate.registered_name)
        # These bytes, not mutable dataset objects, define both executions.
        frozen_dataset = canonical(dataset.model_dump(mode="json"))
        dataset = EvaluationDataset.model_validate_json(frozen_dataset)
        config_bytes = (configuration or RunnerConfiguration()).model_dump_json()
        config = RunnerConfiguration.model_validate_json(config_bytes)
        common = {
            **(provenance or {}),
            "validation_id": validation_id,
            "suggestion_id": selected.suggestion_id,
            "dataset_digest": digest(dataset.model_dump(mode="json")),
        }
        runs = []
        for role, implementation in (("baseline", baseline), ("candidate", candidate)):
            runs.append(
                self.runner.run(
                    implementation.registered_name,
                    EvaluationDataset.model_validate_json(frozen_dataset),
                    run_id=stable_id("run", validation_id, role),
                    configuration=RunnerConfiguration.model_validate_json(config_bytes),
                    resolve_trace=resolve_trace,
                    provenance={
                        **common,
                        "role": role,
                        "implementation_id": implementation.implementation_id,
                        "configuration_id": implementation.configuration_id,
                    },
                )
            )
        comparison = compare_runs(*runs)
        missing = list(selected.missing_evidence)
        baseline_ids = {sid for r in runs[0].results for sid in r.trace_ids}
        candidate_ids = {sid for r in runs[1].results for sid in r.trace_ids}
        stale = bool(
            baseline_ids & candidate_ids
            or response.trace_id in baseline_ids | candidate_ids
            or any(
                len({sid for r in run.results for sid in r.trace_ids})
                != sum(len(r.trace_ids) for r in run.results)
                for run in runs
            )
        )
        if stale:
            missing.append(
                "Reused historical/shared trace IDs do not establish new execution evidence"
            )
        for role, run in zip(("baseline", "candidate"), runs, strict=True):
            for result in run.results:
                if result.observation is None or not result.observation.complete:
                    missing.append(
                        f"{role}/{result.case_id}: complete trace observation unavailable"
                    )
                if result.outcome == "unknown":
                    missing.append(f"{role}/{result.case_id}: task correctness unknown")
        tradeoffs = []
        for metric in (
            "tool_errors",
            "llm_errors",
            "execution_duration_ms",
            "trace_wall_duration_ms",
            "input_tokens",
            "output_tokens",
        ):
            delta = comparison.metric_differences.get(metric)
            if delta is None:
                missing.append(f"{metric}: comparable measurement/coverage unavailable")
            if delta is not None and delta > 0:
                tradeoffs.append(
                    f"{metric} increased by {delta:g}; review cost/latency/failure trade-off"
                )
        trace_ids = sorted(baseline_ids | candidate_ids)
        return FixValidationResult(
            validation_id=validation_id,
            suggestion=selected,
            source_trace_id=response.trace_id,
            source_analysis_digest=digest(response.analysis.model_dump(mode="json")),
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            dataset_digest=common["dataset_digest"],
            configuration=config,
            baseline=baseline,
            candidate=candidate,
            developer_change=change,
            comparison=comparison,
            classification="inconclusive" if stale else comparison.direction,
            trace_ids=trace_ids,
            missing_evidence=list(dict.fromkeys(missing)),
            tradeoffs=tradeoffs,
            limitations=[
                *comparison.limitations,
                "Developer approval and provenance are local assertions, not authentication.",
                "Classification measures declared task criteria, not fewer findings "
                "or causal repair proof.",
                "Trusted spawned execution is not a security sandbox; "
                "timeouts cannot undo side effects.",
                "Synchronous work outside the worker (or descendants) may outlive a timeout.",
                "Single-run timing includes process overhead; "
                "no statistical significance is claimed.",
            ],
        )
