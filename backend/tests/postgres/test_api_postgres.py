"""Run the same HTTP behavior contract against the real PostgreSQL repository."""

import pytest

from tests.test_trace_api import (  # noqa: F401
    test_child_before_parent_and_retry,
    test_conflicting_retry_rolls_back_batch,
    test_cycle_in_batch_is_atomic,
    test_cycles_rejected_across_batches,
    test_duplicate_within_one_batch,
    test_missing_project_and_trace,
    test_span_ids_are_scoped_to_trace,
    test_trace_pagination_and_filters,
    test_trace_roundtrip,
)

pytestmark = pytest.mark.postgres
