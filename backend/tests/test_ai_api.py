"""HTTP contract checks with the existing repository double; not PostgreSQL tests."""

import pytest

from tests.diagnostic_fixtures import labeled_cases
from tests.postgres import test_ai_diagnosis_postgres as scenarios


@pytest.mark.parametrize("case", labeled_cases(), ids=lambda case: case.name)
def test_evidence_api_contract(api_client, case):
    scenarios.test_persisted_evidence_and_mocked_analysis(api_client, case)


def test_scope_consent_and_request_validation(api_client):
    scenarios.test_default_disabled_consent_scope_and_validation(api_client)


@pytest.mark.parametrize(
    "failure", ["provider_timeout", "provider_unavailable", "provider_refused"]
)
def test_provider_failure_api_contract(api_client, failure):
    scenarios.test_provider_failure_retains_persisted_diagnostics(api_client, failure)


def test_repeated_requests(api_client):
    scenarios.test_cooldown_does_not_repeat_provider_call(api_client)
