from uuid import uuid4

import pytest

from app.core.ai_config import AISettings
from tests.diagnostic_fixtures import labeled_cases
from tests.postgres.test_ai_diagnosis_http import live_ai as _live_ai
from tests.postgres.test_diagnostics_postgres import store_case

pytestmark = pytest.mark.postgres
live_ai = _live_ai


@pytest.mark.parametrize("case", labeled_cases(), ids=lambda c: c.name)
def test_postgres_suggestions_are_scoped_read_only_and_reference_real_records(api_client, case):
    trace_id, project_id = store_case(api_client, case)
    base = f"/api/v1/traces/{trace_id}"
    before = api_client.get(base + "/spans?limit=500").json()
    response = api_client.post(
        base + "/fix-suggestions", params={"project_id": project_id}, json={}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis"]["deterministic"]["outcome"]["classification"] == case.outcome.value
    actual = {s.span_id for s in case.spans}
    assert all(set(s["span_ids"]) <= actual for s in result["suggestions"])
    assert result == api_client.post(base + "/fix-suggestions", json={}).json()
    assert before == api_client.get(base + "/spans?limit=500").json()
    assert (
        api_client.post(
            base + "/fix-suggestions", params={"project_id": str(uuid4())}, json={}
        ).status_code
        == 404
    )
    assert api_client.post(base + "/fix-suggestions", json={"suggestions": []}).status_code == 422


def test_real_http_optional_analysis_and_failure_retention(live_ai):
    client, app, provider = live_ai.client, live_ai.app, live_ai.provider
    trace_id, _ = store_case(client, labeled_cases()[1])
    url = f"/api/v1/traces/{trace_id}/fix-suggestions"
    baseline = client.post(url, json={}).json()
    assert baseline["suggestions"] and not provider.calls
    app.state.ai_settings = AISettings(enabled=True, cooldown_seconds=0)
    assert (
        client.post(url, json={"include_ai": True}).json()["analysis"]["execution"]["status"]
        == "consent_required"
    )
    assert not provider.calls
    body = {"include_ai": True, "consent_to_external_processing": True}
    success = client.post(url, json=body).json()
    assert success["analysis"]["execution"]["status"] == "completed" and len(provider.calls) == 1
    assert any(s["hypothesis_ids"] for s in success["suggestions"])
    provider.failure = "provider_timeout"
    failure = client.post(url, json=body).json()
    assert failure["analysis"]["execution"]["error_code"] == "provider_timeout"
    assert failure["suggestions"] == baseline["suggestions"]


def test_real_http_rejects_fabricated_model_evidence_and_retains_recovery(live_ai):
    from tests.ai_fixtures import mock_hypotheses

    client, app, provider = live_ai.client, live_ai.app, live_ai.provider
    case = next(c for c in labeled_cases() if c.name == "explicit_recovery")
    trace_id, _ = store_case(client, case)
    app.state.ai_settings = AISettings(enabled=True, cooldown_seconds=0)
    url = f"/api/v1/traces/{trace_id}/fix-suggestions"
    body = {"include_ai": True, "consent_to_external_processing": True}
    baseline = client.post(url, json=body).json()
    provider.output = mock_hypotheses(provider.calls[-1])
    provider.output["hypotheses"][0]["span_ids"] = ["f" * 16]
    result = client.post(url, json=body).json()
    assert result["analysis"]["execution"]["error_code"] == "invalid_evidence_reference"
    assert result["analysis"]["deterministic"]["outcome"]["classification"] == "recovered_execution"
    assert (
        result["suggestions"][0]["proposed_change"] == baseline["suggestions"][0]["proposed_change"]
    )
