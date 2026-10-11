import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)


def nonzero(value: str) -> str:
    if int(value, 16) == 0:
        raise ValueError("ID must not be all zeroes")
    return value


TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$"), AfterValidator(nonzero)]
SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$"), AfterValidator(nonzero)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class StatusCode(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class OperationType(StrEnum):
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    RUNTIME = "runtime"


class SpanKind(StrEnum):
    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def postgres_text_fields(cls, value: object) -> object:
        if isinstance(value, str):
            postgres_text(value)
        return value


def postgres_text(value: str) -> None:
    if "\x00" in value:
        raise ValueError("PostgreSQL text cannot contain a NUL character")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("text must contain valid UTF-8 characters") from error


class Attributed(Contract):
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def bounded_attributes(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        pending: list[JsonValue] = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                postgres_text(item)
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        # JSONB cannot represent NaN/Infinity; reject them before opening a transaction.
        try:
            encoded = json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (ValueError, RecursionError) as error:
            raise ValueError("attributes must contain finite, serializable JSON") from error
        if len(encoded) > 32_768:
            raise ValueError("attributes exceed 32 KiB")
        return value


class ProjectCreate(Attributed):
    name: Name
    description: str | None = Field(default=None, max_length=2000)


class ProjectRead(ProjectCreate):
    id: UUID
    created_at: AwareDatetime


class Timed(Attributed):
    name: Name
    start_time: AwareDatetime
    end_time: AwareDatetime | None = None
    status_code: StatusCode = StatusCode.UNSET
    status_message: str | None = Field(default=None, max_length=2000)

    @field_validator("start_time", "end_time")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def ordered_times(self) -> Self:
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must be on or after start_time")
        return self


class TraceCreate(Timed):
    trace_id: TraceId
    project_id: UUID


class Duration(Contract):
    start_time: AwareDatetime
    end_time: AwareDatetime | None = None
    duration_ms: float | None = Field(default=None, json_schema_extra={"readOnly": True})

    @model_validator(mode="after")
    def derive_duration(self) -> Self:
        # A response must also validate when read back from JSON; always derive this value.
        self.duration_ms = (
            (self.end_time - self.start_time).total_seconds() * 1000
            if self.end_time is not None
            else None
        )
        return self


class TraceRead(TraceCreate, Duration):
    pass


class SpanEvent(Attributed):
    name: Name
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class SpanCreate(Timed):
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None
    operation_type: OperationType
    span_kind: SpanKind = SpanKind.INTERNAL
    events: list[SpanEvent] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def no_self_parent(self) -> Self:
        if self.parent_span_id == self.span_id:
            raise ValueError("a span cannot be its own parent")
        return self


class SpanRead(SpanCreate, Duration):
    pass


class SpanBatch(Contract):
    spans: list[SpanCreate] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def bounded_batch(self) -> Self:
        if len(self.model_dump_json().encode("utf-8")) > 1_048_576:
            raise ValueError("batch exceeds 1 MiB of normalized JSON")
        return self


class BatchResult(Contract):
    received: int
    inserted: int
    duplicates: int


class TracePage(Contract):
    items: list[TraceRead]
    total: int
    limit: int
    offset: int


class SpanPage(Contract):
    items: list[SpanRead]
    total: int
    limit: int
    offset: int
