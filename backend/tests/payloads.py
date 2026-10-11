TRACE_ID = "0123456789abcdef0123456789abcdef"
PARENT_ID = "0123456789abcdef"
CHILD_ID = "fedcba9876543210"


def trace_payload(project_id_value: str, **overrides: object) -> dict:
    return {
        "trace_id": TRACE_ID,
        "project_id": project_id_value,
        "name": "Answer a question",
        "start_time": "2026-10-11T12:00:00Z",
        "end_time": "2026-10-11T12:00:02Z",
        "status_code": "ok",
        "attributes": {"agent.name": "example", "configuration": {"max_steps": 5}},
        **overrides,
    }


def span_payload(**overrides: object) -> dict:
    return {
        "trace_id": TRACE_ID,
        "span_id": PARENT_ID,
        "name": "Agent run",
        "operation_type": "agent",
        "start_time": "2026-10-11T12:00:00Z",
        "end_time": "2026-10-11T12:00:02Z",
        "status_code": "ok",
        **overrides,
    }
