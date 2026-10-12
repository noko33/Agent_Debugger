import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.api.ai_diagnosis import get_ai_service
from app.core.ai_config import AISettings
from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app
from app.services.ai_diagnosis.service import AIDiagnosisService, AnalysisGuard
from tests.ai_fixtures import ScriptedProvider
from tests.diagnostic_fixtures import labeled_cases
from tests.postgres.test_diagnostics_postgres import store_case

pytestmark = pytest.mark.postgres


def configure(client, provider=None, **settings):
    scripted = provider or ScriptedProvider()
    service = AIDiagnosisService(
        AISettings(enabled=True, cooldown_seconds=0, **settings),
        client.app.state.diagnostic_parameters,
        AnalysisGuard(),
        scripted,
    )
    client.app.dependency_overrides[get_ai_service] = lambda: service
    return scripted


@pytest.mark.parametrize("case", labeled_cases(), ids=lambda case: case.name)
def test_persisted_evidence_and_mocked_analysis(api_client, case):
    provider = configure(api_client)
    trace_id, project_id = store_case(api_client, case)
    url = f"/api/v1/traces/{trace_id}"
    before = api_client.get(url + "/spans?limit=500").json()
    result = api_client.post(
        url + "/ai-diagnosis",
        params={"project_id": project_id},
        json={"consent_to_external_processing": True},
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["deterministic"]["outcome"]["classification"] == case.outcome.value
    assert {f["category"] for f in data["deterministic"]["findings"]} == {
        c.value for c in case.expected
    }
    needs_analysis = bool(case.expected or data["deterministic"]["operation_failure_count"])
    assert data["execution"]["status"] == ("completed" if needs_analysis else "not_needed")
    assert bool(provider.calls) == needs_analysis
    actual = {s.span_id for s in case.spans}
    assert all(set(h["span_ids"]) <= actual for h in data["hypotheses"])
    assert api_client.get(url + "/spans?limit=500").json() == before


def test_default_disabled_consent_scope_and_validation(api_client):
    trace_id, _ = store_case(api_client, labeled_cases()[1])
    url = f"/api/v1/traces/{trace_id}/ai-diagnosis"
    assert api_client.post(url, json={}).json()["execution"]["status"] == "disabled"
    provider = configure(api_client)
    assert api_client.post(url, json={}).json()["execution"]["status"] == "consent_required"
    assert api_client.post(url, params={"project_id": str(uuid4())}, json={}).status_code == 404
    assert api_client.post(f"/api/v1/traces/{uuid4().hex}/ai-diagnosis", json={}).status_code == 404
    assert api_client.post(url, json={"consent_to_external_processing": "yes"}).status_code == 422
    assert api_client.post(url, json={"model": "untrusted-override"}).status_code == 422
    assert not provider.calls


def test_limits_and_invalid_stored_data(api_client, database_sessions):
    trace_id, _ = store_case(api_client, labeled_cases()[1])
    url = f"/api/v1/traces/{trace_id}/ai-diagnosis"
    provider = configure(api_client)
    old = api_client.app.state.diagnostic_parameters
    api_client.app.state.diagnostic_parameters = old.model_copy(update={"max_spans": 1})
    assert api_client.post(url, json={}).status_code == 413
    api_client.app.state.diagnostic_parameters = old
    with database_sessions.begin() as session:
        session.execute(
            text("UPDATE spans SET attributes = CAST(:value AS jsonb) WHERE trace_id = :id"),
            {"value": json.dumps("malformed stored attributes"), "id": trace_id},
        )
    assert api_client.post(url, json={}).status_code == 422
    assert not provider.calls


@pytest.mark.parametrize(
    "failure", ["provider_timeout", "provider_unavailable", "provider_refused"]
)
def test_provider_failure_retains_persisted_diagnostics(api_client, failure):
    configure(api_client, ScriptedProvider(failure=failure))
    trace_id, _ = store_case(api_client, labeled_cases()[1])
    url = f"/api/v1/traces/{trace_id}"
    deterministic = api_client.get(url + "/diagnostics").json()
    data = api_client.post(
        url + "/ai-diagnosis", json={"consent_to_external_processing": True}
    ).json()
    assert data["execution"]["error_code"] == failure
    assert data["deterministic"] == deterministic and not data["hypotheses"]


def test_cooldown_does_not_repeat_provider_call(api_client):
    provider = ScriptedProvider()
    service = AIDiagnosisService(
        AISettings(enabled=True),
        api_client.app.state.diagnostic_parameters,
        AnalysisGuard(),
        provider,
    )
    api_client.app.dependency_overrides[get_ai_service] = lambda: service
    trace_id, _ = store_case(api_client, labeled_cases()[1])
    url = f"/api/v1/traces/{trace_id}/ai-diagnosis"
    for expected in ("completed", "rate_limited"):
        assert (
            api_client.post(url, json={"consent_to_external_processing": True}).json()["execution"][
                "status"
            ]
            == expected
        )
    assert len(provider.calls) == 1


def test_read_lock_released_before_provider_call(postgres_engine):
    # Independent committed connections in the generated disposable schema test real locks.
    sessions = sessionmaker(postgres_engine, expire_on_commit=False)
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))

    def session_dependency():
        with sessions.begin() as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency
    with TestClient(app) as client:
        trace_id, _ = store_case(client, labeled_cases()[1])

        class LockCheckingProvider(ScriptedProvider):
            def analyze(self, context):
                with sessions.begin() as session:
                    session.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    session.execute(
                        text("SELECT trace_id FROM traces WHERE trace_id = :id FOR UPDATE"),
                        {"id": trace_id},
                    )
                return super().analyze(context)

        provider = configure(client, LockCheckingProvider())
        result = client.post(
            f"/api/v1/traces/{trace_id}/ai-diagnosis", json={"consent_to_external_processing": True}
        )
        assert result.json()["execution"]["status"] == "completed", result.text
        assert len(provider.calls) == 1
