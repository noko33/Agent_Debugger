"""Initial project, trace, span and event tables.

Keep this migration self-contained; later model changes must not alter historical DDL.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def timed_columns() -> list[sa.Column]:
    return [
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("status_code", sa.String(5), nullable=False, server_default="unset"),
        sa.Column("status_message", sa.Text()),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "duration_ms",
            sa.Float(),
            sa.Computed("EXTRACT(EPOCH FROM (end_time - start_time)) * 1000", persisted=True),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(32), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *timed_columns(),
        sa.CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)", name="ck_trace_id"
        ),
        sa.CheckConstraint("end_time IS NULL OR end_time >= start_time", name="ck_trace_times"),
        sa.CheckConstraint("status_code IN ('unset', 'ok', 'error')", name="ck_trace_status"),
    )
    op.create_index("ix_traces_project_start", "traces", ["project_id", "start_time", "trace_id"])
    op.create_index("ix_traces_start", "traces", ["start_time", "trace_id"])
    op.create_index("ix_traces_status_start", "traces", ["status_code", "start_time", "trace_id"])
    op.create_table(
        "spans",
        sa.Column(
            "trace_id",
            sa.String(32),
            sa.ForeignKey("traces.trace_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("span_id", sa.String(16), primary_key=True),
        sa.Column("parent_span_id", sa.String(16)),
        sa.Column("operation_type", sa.String(7), nullable=False),
        sa.Column("span_kind", sa.String(8), nullable=False, server_default="internal"),
        *timed_columns(),
        sa.CheckConstraint(
            "span_id ~ '^[0-9a-f]{16}$' AND span_id <> repeat('0', 16)", name="ck_span_id"
        ),
        sa.CheckConstraint(
            "parent_span_id IS NULL OR (parent_span_id ~ '^[0-9a-f]{16}$' "
            "AND parent_span_id <> repeat('0', 16) AND parent_span_id <> span_id)",
            name="ck_span_parent",
        ),
        sa.CheckConstraint("end_time IS NULL OR end_time >= start_time", name="ck_span_times"),
        sa.CheckConstraint("status_code IN ('unset', 'ok', 'error')", name="ck_span_status"),
        sa.CheckConstraint(
            "operation_type IN ('agent', 'llm', 'tool', 'runtime')", name="ck_span_operation"
        ),
        sa.CheckConstraint(
            "span_kind IN ('internal', 'server', 'client', 'producer', 'consumer')",
            name="ck_span_kind",
        ),
    )
    op.create_index("ix_spans_trace_parent", "spans", ["trace_id", "parent_span_id"])
    op.create_index("ix_spans_trace_start", "spans", ["trace_id", "start_time", "span_id"])
    op.create_table(
        "span_events",
        sa.Column("trace_id", sa.String(32), primary_key=True),
        sa.Column("span_id", sa.String(16), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(
            ["trace_id", "span_id"], ["spans.trace_id", "spans.span_id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("position >= 0", name="ck_event_position"),
    )


def downgrade() -> None:
    op.drop_table("span_events")
    op.drop_table("spans")
    op.drop_table("traces")
    op.drop_table("projects")
