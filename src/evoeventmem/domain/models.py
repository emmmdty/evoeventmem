from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class MemoryKind(StrEnum):
    FACT = "fact"
    EVENT = "event"
    EPISODE = "episode"
    PROCEDURE = "procedure"


class EvidenceRef(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None


class MemoryRecord(BaseModel):
    """Starter contract. M06 replaces this with the durable research schema."""

    memory_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(min_length=1)
    kind: MemoryKind = MemoryKind.FACT
    content: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    event_time: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes: UUID | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("event_time", "valid_from", "valid_to", "created_at")
    @classmethod
    def require_aware_temporal_fields(
        cls,
        value: datetime | None,
        info: ValidationInfo,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> MemoryRecord:
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        return self


class MemorySearchHit(BaseModel):
    memory: MemoryRecord
    score: float = Field(ge=0.0)
    reason: str
