from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


def normalize_memory_content(content: str) -> str:
    return " ".join(content.split()).casefold()


def memory_order_key(memory: MemoryRecord) -> tuple[str, ...]:
    """Run-independent ordering prefix for a durable memory.

    ``memory_id`` is a random UUID assigned at write time; using it as the
    primary tie-break makes retrieval and linking order depend on the run.
    This key derives only from content and evidence refs, which are
    identical across runs that share the same extraction snapshot.

    Callers that need a total order append ``str(memory.memory_id)`` as the
    final element: ties on this prefix are semantically equivalent memories,
    so the trailing UUID only breaks ties that cannot change the rendered
    reader input.
    """
    evidence = tuple(
        part
        for ref in sorted(
            memory.evidence_refs,
            key=lambda ref: (
                ref.source_type,
                ref.source_id,
                ref.locator or "",
                ref.quote or "",
            ),
        )
        for part in (
            ref.source_type,
            ref.source_id,
            ref.locator or "",
            ref.quote or "",
        )
    )
    return (normalize_memory_content(memory.content), *evidence)


class MemoryKind(StrEnum):
    FACT = "fact"
    EVENT = "event"
    EPISODE = "episode"
    PROCEDURE = "procedure"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    DELETED = "deleted"


class EntityRef(BaseModel):
    entity_id: str | None = None
    name: str = Field(min_length=1)
    kind: str | None = None
    role: str | None = None


class RelationRef(BaseModel):
    source: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    target: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EvidenceRef(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    """Durable event-memory contract with evidence and temporal provenance."""

    schema_version: Literal["memory.v1"] = "memory.v1"
    memory_id: UUID = Field(default_factory=uuid4)
    tenant_id: str | None = None
    user_id: str = Field(min_length=1)
    session_id: str | None = None
    memory_kind: MemoryKind = Field(
        default=MemoryKind.FACT,
        validation_alias=AliasChoices("memory_kind", "kind"),
    )
    content: str = Field(min_length=1)
    normalized_content: str | None = None
    entities: list[EntityRef] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)
    relations: list[RelationRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_refs", "evidence"),
    )
    event_time: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes: list[UUID] = Field(default_factory=list)
    superseded_by: UUID | None = None
    derived_from: list[UUID] = Field(default_factory=list)
    derivation: str | None = None
    synthetic: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    utility: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def kind(self) -> MemoryKind:
        return self.memory_kind

    @property
    def evidence(self) -> list[EvidenceRef]:
        return self.evidence_refs

    @field_validator("event_time", "valid_from", "valid_to", "created_at", "updated_at")
    @classmethod
    def require_aware_temporal_fields(
        cls,
        value: datetime | None,
        info: ValidationInfo,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None

    @field_validator("entities", mode="before")
    @classmethod
    def coerce_legacy_entities(cls, value: object) -> object:
        if isinstance(value, list):
            return [{"name": item} if isinstance(item, str) else item for item in value]
        return value

    @field_validator("supersedes", "derived_from", mode="before")
    @classmethod
    def coerce_legacy_uuid_links(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, UUID | str):
            return [value]
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> MemoryRecord:
        self.normalized_content = normalize_memory_content(self.content)
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if not self.synthetic and not self.evidence_refs:
            raise ValueError("durable memories require at least one evidence reference")
        if self.status is MemoryStatus.SUPERSEDED and self.superseded_by is None:
            raise ValueError("superseded memories must identify superseded_by")
        if self.status is not MemoryStatus.SUPERSEDED and self.superseded_by is not None:
            raise ValueError("only superseded memories may identify superseded_by")
        linked_ids = [*self.supersedes, *self.derived_from]
        if self.superseded_by is not None:
            linked_ids.append(self.superseded_by)
        if self.memory_id in linked_ids:
            raise ValueError("memory links must not reference the memory itself")
        return self

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class MemorySearchHit(BaseModel):
    memory: MemoryRecord
    score: float = Field(ge=0.0)
    reason: str
