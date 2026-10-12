import json

import pytest
from pydantic import ValidationError

from app.schemas.diagnostics import DiagnosticParameters
from app.services.replay import AnalysisSnapshot, load_snapshot, replay
from tests.diagnostic_fixtures import labeled_cases, root, span, trace

PARAMETERS = DiagnosticParameters()


@pytest.mark.parametrize("case", labeled_cases(), ids=lambda case: case.name)
def test_replay_fixture_stability(case) -> None:
    snapshot = AnalysisSnapshot.capture(case.trace, case.spans)
    initial = replay(snapshot, PARAMETERS)
    result = replay(snapshot, PARAMETERS, original=initial)
    assert result.observation.analysis.model_dump() == initial.observation.analysis.model_dump()
    assert {f.category for f in result.observation.analysis.findings} == case.expected
    assert result.observation.analysis.outcome.classification == case.outcome
    assert result.comparison.compatible and result.comparison.same_snapshot
    assert not result.comparison.changed_findings
    assert not result.comparison.missing_evidence
    assert len(result.comparison.stable_findings) == len(initial.observation.analysis.findings)
    assert AnalysisSnapshot.capture(case.trace, list(reversed(case.spans))) == snapshot


def test_snapshot_detaches_mutations_and_decoding() -> None:
    record, spans = trace(), [root(), span(2, attributes={"nested": {"x": [1]}})]
    snapshot = AnalysisSnapshot.capture(record, spans)
    original = snapshot.sha256
    spans[1].attributes["nested"]["x"].append(2)
    record.name = "mutated"
    result = replay(snapshot, PARAMETERS)
    result.observation.analysis.findings.clear()
    assert snapshot.sha256 == original
    assert b"mutated" not in snapshot.payload
    assert b'"x":[1]' in snapshot.payload
    with pytest.raises(AttributeError):
        snapshot.payload = b"changed"


def test_rule_configuration_changes_are_not_missing_evidence() -> None:
    snapshot = AnalysisSnapshot.capture(
        trace(), [root(), span(2, status="error"), span(3, status="error")]
    )
    before = replay(snapshot, PARAMETERS)
    after = replay(
        snapshot,
        DiagnosticParameters(repeated_tool_failure_threshold=3, slow_operation_ms=50),
        original=before,
    )
    assert after.comparison.removed_findings and after.comparison.added_findings
    assert not after.comparison.missing_evidence
    assert after.parameters.slow_operation_ms == 50


def test_changed_finding_parameters_and_missing_references() -> None:
    snapshot = AnalysisSnapshot.capture(trace(), [root(), span(2, status="error")])
    before = replay(snapshot, DiagnosticParameters(slow_operation_ms=50))
    changed = replay(snapshot, DiagnosticParameters(slow_operation_ms=40), original=before)
    assert changed.comparison.changed_findings
    missing = replay(AnalysisSnapshot.capture(trace(), [root()]), PARAMETERS, original=before)
    assert missing.comparison.missing_evidence
    assert not missing.comparison.same_snapshot


def test_missing_event_evidence() -> None:
    s = span(
        2,
        status="error",
        events=[{"name": "exception", "timestamp": span(2).end_time, "attributes": {}}],
    )
    before = replay(AnalysisSnapshot.capture(trace(), [root(), s]), PARAMETERS)
    s.events.clear()
    after = replay(AnalysisSnapshot.capture(trace(), [root(), s]), PARAMETERS, original=before)
    assert after.comparison.missing_evidence


def test_missing_exception_attribute_is_not_stable_evidence() -> None:
    s = span(2, status="unset", attributes={"error.type": "ValueError"})
    before = replay(AnalysisSnapshot.capture(trace(), [root(), s]), PARAMETERS)
    s.attributes.clear()
    after = replay(AnalysisSnapshot.capture(trace(), [root(), s]), PARAMETERS, original=before)
    assert after.comparison.missing_evidence


@pytest.mark.parametrize("payload", [b"not json", b"{}", b'{"secret":"never echo"}'])
def test_malformed_snapshots_safe(payload: bytes) -> None:
    result = replay(AnalysisSnapshot(payload), PARAMETERS)
    assert result.status == "invalid" and result.observation is None
    assert "never echo" not in result.model_dump_json()


def test_missing_and_incompatible_original() -> None:
    missing = replay(None, PARAMETERS)
    assert missing.status == "missing"
    snapshot = AnalysisSnapshot.capture(trace(), [root()])
    result = replay(snapshot, PARAMETERS, original=missing)
    assert not result.comparison.compatible
    original = replay(snapshot, PARAMETERS)
    original.observation.analysis.rules_version = "future"
    assert not replay(snapshot, PARAMETERS, original=original).comparison.compatible


def test_truncated_root_ok_remains_unknown() -> None:
    result = replay(AnalysisSnapshot.capture(trace(), [root()], truncated=True), PARAMETERS)
    assert result.status == "incomplete"
    assert not result.observation.complete
    assert result.observation.analysis.outcome.classification == "unknown"
    assert result.observation.analysis.coverage == "partial_snapshot"


def test_revalidation_rejects_bypassed_contract_and_limit() -> None:
    with pytest.warns(UserWarning, match="Pydantic serializer"), pytest.raises(ValidationError):
        AnalysisSnapshot.capture(trace(), [span(2).model_copy(update={"end_time": "invalid"})])
    snapshot = AnalysisSnapshot.capture(trace(), [root(), span(2)])
    assert replay(snapshot, DiagnosticParameters(max_spans=1)).issues == ["diagnostic_span_limit"]
    data = json.loads(snapshot.payload)
    data["spans"][1]["parent_span_id"] = data["spans"][1]["span_id"]
    assert replay(AnalysisSnapshot(json.dumps(data).encode()), PARAMETERS).status == "invalid"


def test_load_reuses_repository_scope_and_bound() -> None:
    class Repository:
        def snapshot(self, trace_id, project_id, max_spans):
            assert trace_id == trace().trace_id
            assert project_id == trace().project_id
            assert max_spans == PARAMETERS.max_spans
            return trace(), [root()]

    loaded = load_snapshot(Repository(), trace().trace_id, PARAMETERS, trace().project_id)
    assert replay(loaded, PARAMETERS).status == "complete"


def test_repository_missing_trace() -> None:
    class Repository:
        def snapshot(self, *args):
            return None

    assert load_snapshot(Repository(), trace().trace_id, PARAMETERS) is None
