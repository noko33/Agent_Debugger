from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimedColumns:
    name: Mapped[str] = mapped_column(String(200))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_code: Mapped[str] = mapped_column(String(5), default="unset", server_default="unset")
    status_message: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    duration_ms: Mapped[float | None] = mapped_column(
        Float, Computed("EXTRACT(EPOCH FROM (end_time - start_time)) * 1000", persisted=True)
    )


class Trace(TimedColumns, Base):
    __tablename__ = "traces"
    __table_args__ = (
        CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)", name="ck_trace_id"
        ),
        CheckConstraint("end_time IS NULL OR end_time >= start_time", name="ck_trace_times"),
        CheckConstraint("status_code IN ('unset', 'ok', 'error')", name="ck_trace_status"),
        Index("ix_traces_project_start", "project_id", "start_time", "trace_id"),
        Index("ix_traces_start", "start_time", "trace_id"),
        Index("ix_traces_status_start", "status_code", "start_time", "trace_id"),
    )

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))


class Span(TimedColumns, Base):
    __tablename__ = "spans"
    __table_args__ = (
        CheckConstraint(
            "span_id ~ '^[0-9a-f]{16}$' AND span_id <> repeat('0', 16)", name="ck_span_id"
        ),
        CheckConstraint(
            "parent_span_id IS NULL OR (parent_span_id ~ '^[0-9a-f]{16}$' "
            "AND parent_span_id <> repeat('0', 16) AND parent_span_id <> span_id)",
            name="ck_span_parent",
        ),
        CheckConstraint("end_time IS NULL OR end_time >= start_time", name="ck_span_times"),
        CheckConstraint("status_code IN ('unset', 'ok', 'error')", name="ck_span_status"),
        CheckConstraint(
            "operation_type IN ('agent', 'llm', 'tool', 'runtime')", name="ck_span_operation"
        ),
        CheckConstraint(
            "span_kind IN ('internal', 'server', 'client', 'producer', 'consumer')",
            name="ck_span_kind",
        ),
        Index("ix_spans_trace_parent", "trace_id", "parent_span_id"),
        Index("ix_spans_trace_start", "trace_id", "start_time", "span_id"),
    )

    trace_id: Mapped[str] = mapped_column(
        ForeignKey("traces.trace_id", ondelete="CASCADE"), primary_key=True
    )
    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    # No parent FK: parent spans may be exported later or never exported (sampling).
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    operation_type: Mapped[str] = mapped_column(String(7))
    span_kind: Mapped[str] = mapped_column(String(8), default="internal", server_default="internal")
    events: Mapped[list["SpanEventRow"]] = relationship(
        cascade="all, delete-orphan", order_by="SpanEventRow.position", lazy="raise"
    )


class SpanEventRow(Base):
    __tablename__ = "span_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trace_id", "span_id"], ["spans.trace_id", "spans.span_id"], ondelete="CASCADE"
        ),
        CheckConstraint("position >= 0", name="ck_event_position"),
    )

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
