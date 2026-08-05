from __future__ import annotations

import builtins
from collections.abc import Awaitable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from evoeventmem.domain.models import MemoryRecord


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChatResponse:
    text: str
    model_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_key: str | None = None


@dataclass(frozen=True)
class EmbeddingResponse:
    vector: tuple[float, ...]
    model_id: str
    cache_key: str | None = None


class ChatModel(Protocol):
    model_id: str

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse: ...


class EmbeddingModel(Protocol):
    model_id: str

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]: ...


class Reranker(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class EntityLexicon(Protocol):
    """Known-entity vocabulary; names are compared case-insensitively."""

    def contains(self, name: str) -> bool: ...


class MemoryRepository(Protocol):
    def add(self, memory: MemoryRecord) -> MemoryRecord: ...

    def get(self, memory_id: UUID) -> MemoryRecord | None: ...

    def update(self, memory: MemoryRecord) -> MemoryRecord: ...

    def list_for_user(self, user_id: str) -> list[MemoryRecord]: ...

    def transaction(self) -> AbstractContextManager[MemoryRepository]: ...


# ---------------------------------------------------------------------------
# Additive async production contracts. Freeze baseline: B0 = 25f7783.
# Contract version: D-SCOPE. These are additive; the synchronous
# MemoryRepository and domain MemoryRecord above are unchanged.
# ---------------------------------------------------------------------------


class ScopeMismatch(BaseModel):
    """Explicit representation of a scope/body identity disagreement."""

    field: Literal["tenant_id", "user_id", "session_id"]
    scope_value: str | None
    body_value: str | None


class RequestScope(BaseModel):
    """Enforced request identity: nonempty tenant and user, optional session."""

    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str | None = None

    @field_validator("tenant_id", "user_id")
    @classmethod
    def _require_nonblank(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must be a nonempty string")
        return value

    def canonical_key(self) -> str:
        if self.session_id is None:
            return f"{self.tenant_id}|{self.user_id}"
        return f"{self.tenant_id}|{self.user_id}|{self.session_id}"

    def mismatch(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        session_id: str | None = None,
    ) -> ScopeMismatch | None:
        if tenant_id is not None and tenant_id != self.tenant_id:
            return ScopeMismatch(
                field="tenant_id", scope_value=self.tenant_id, body_value=tenant_id
            )
        if user_id is not None and user_id != self.user_id:
            return ScopeMismatch(
                field="user_id", scope_value=self.user_id, body_value=user_id
            )
        if session_id is not None and session_id != self.session_id:
            return ScopeMismatch(
                field="session_id", scope_value=self.session_id, body_value=session_id
            )
        return None

    def with_session(self, session_id: str | None) -> RequestScope:
        return self.model_copy(update={"session_id": session_id})


class EmbeddingVector(BaseModel):
    """Typed numeric vector with declared model identity and dimension."""

    values: tuple[float, ...]
    model_id: str
    dimension: int

    @field_validator("values")
    @classmethod
    def _require_finite(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not all(isfinite(value) for value in values):
            raise ValueError("embedding values must be finite")
        return values

    @field_validator("dimension")
    @classmethod
    def _require_positive_dimension(cls, dimension: int) -> int:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        return dimension

    @field_validator("model_id")
    @classmethod
    def _require_model_id(cls, model_id: str) -> str:
        if not model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        return model_id

    @model_validator(mode="after")
    def _require_dimension_matches_values(self) -> EmbeddingVector:
        if self.dimension != len(self.values):
            raise ValueError("dimension must equal the number of embedding values")
        return self

    @property
    def size(self) -> int:
        return len(self.values)


class MemoryQuery(StrEnum):
    ACTIVE_ONLY = "active_only"
    ALL = "all"


class SearchLimit(BaseModel):
    state: MemoryQuery = MemoryQuery.ACTIVE_ONLY
    max_results: int = Field(default=10, ge=1)


class SearchVector(BaseModel):
    """A vector search carrying its own RequestScope and limit."""

    query: EmbeddingVector
    scope: RequestScope
    limit: SearchLimit = Field(default_factory=SearchLimit)


class ListQuery(BaseModel):
    limit: int = Field(default=10, ge=1)
    status: MemoryQuery = MemoryQuery.ALL


class SchemaState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    MISMATCH = "mismatch"


class PingResult(BaseModel):
    ok: bool
    schema_state: SchemaState
    model_id: str | None = None
    dimension: int | None = None
    detail: str | None = None


class SearchHit(BaseModel):
    memory: MemoryRecord
    score: float = Field(ge=0.0)
    reason: str


class AsyncMemoryRepository(Protocol):
    """Scope-aware async persistence production port.

    Every UUID lookup requires a RequestScope. Writes receive the document
    vector separately from the durable MemoryRecord.
    """

    def add(
        self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
    ) -> Awaitable[MemoryRecord]: ...

    def get(self, scope: RequestScope, memory_id: UUID) -> Awaitable[MemoryRecord | None]: ...

    def get_with_vector(
        self, scope: RequestScope, memory_id: UUID
    ) -> Awaitable[tuple[MemoryRecord | None, EmbeddingVector | None]]: ...

    def update(
        self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
    ) -> Awaitable[MemoryRecord]: ...

    def list(
        self, scope: RequestScope, query: ListQuery
    ) -> Awaitable[builtins.list[MemoryRecord]]: ...

    def search_vector(self, search: SearchVector) -> Awaitable[builtins.list[SearchHit]]: ...

    def ping(self) -> Awaitable[PingResult]: ...

    async def close(self) -> None: ...


class AsyncEmbeddingModel(Protocol):
    """Additive async embedding production port."""

    model_id: str

    def dimension(self) -> int: ...

    def embed_query(self, text: str) -> Awaitable[EmbeddingVector]: ...

    def embed_document(self, text: str) -> Awaitable[EmbeddingVector]: ...
