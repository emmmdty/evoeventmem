from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from evoeventmem.core.ports import (
    EmbeddingVector,
    ListQuery,
    MemoryQuery,
    PingResult,
    RequestScope,
    SchemaState,
    SearchHit,
    SearchVector,
)
from evoeventmem.domain.models import MemoryRecord, MemoryStatus


def _matches_scope(memory: MemoryRecord, scope: RequestScope) -> bool:
    if memory.tenant_id != scope.tenant_id or memory.user_id != scope.user_id:
        return False
    session = scope.session_id
    if session is None:
        return True
    return memory.session_id == session


def _active(memory: MemoryRecord) -> bool:
    return memory.status is MemoryStatus.ACTIVE


class AsyncInMemoryRepository:
    """Scope-aware async in-memory fake.

    Enforces the same isolation and vector-dimension rules expected from
    PostgreSQL. This is a test/development adapter, not an automatic
    production fallback.
    """

    def __init__(
        self,
        *,
        model_id: str = "test-model",
        dimension: int = 4,
        schema_version: str = "memory.v1",
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._model_id = model_id
        self._dimension = dimension
        self._schema_version = schema_version
        self._items: dict[UUID, MemoryRecord] = {}
        self._vectors: dict[UUID, EmbeddingVector] = {}
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("repository is closed")

    def _check_vector(self, vector: EmbeddingVector) -> None:
        if vector.model_id != self._model_id:
            raise ValueError(
                f"vector model {vector.model_id!r} does not match repository model "
                f"{self._model_id!r}"
            )
        if vector.dimension != self._dimension or len(vector.values) != self._dimension:
            raise ValueError(
                f"vector dimension {vector.dimension} does not match repository "
                f"dimension {self._dimension}"
            )

    async def add(
        self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
    ) -> MemoryRecord:
        self._check_open()
        self._check_vector(vector)
        stored = memory.model_copy(deep=True)
        self._items[stored.memory_id] = stored
        self._vectors[stored.memory_id] = vector.model_copy(deep=True)
        return stored.model_copy(deep=True)

    async def get(self, scope: RequestScope, memory_id: UUID) -> MemoryRecord | None:
        self._check_open()
        memory = self._items.get(memory_id)
        if memory is None or not _matches_scope(memory, scope):
            return None
        return memory.model_copy(deep=True)

    async def get_with_vector(
        self, scope: RequestScope, memory_id: UUID
    ) -> tuple[MemoryRecord | None, EmbeddingVector | None]:
        self._check_open()
        memory = self._items.get(memory_id)
        if memory is None or not _matches_scope(memory, scope):
            return None, None
        vector = self._vectors.get(memory_id)
        return (
            memory.model_copy(deep=True),
            vector.model_copy(deep=True) if vector is not None else None,
        )

    async def update(
        self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
    ) -> MemoryRecord:
        self._check_open()
        self._check_vector(vector)
        existing = self._items.get(memory.memory_id)
        if existing is None:
            raise KeyError(f"no memory with id {memory.memory_id}")
        if not _matches_scope(existing, scope):
            raise KeyError(f"no memory with id {memory.memory_id} in scope")
        stored = memory.model_copy(deep=True)
        stored.updated_at = datetime.now(UTC)
        self._items[stored.memory_id] = stored
        self._vectors[stored.memory_id] = vector.model_copy(deep=True)
        return stored.model_copy(deep=True)

    async def list(
        self, scope: RequestScope, query: ListQuery
    ) -> builtins.list[MemoryRecord]:
        self._check_open()
        items = [item for item in self._items.values() if _matches_scope(item, scope)]
        if query.status is MemoryQuery.ACTIVE_ONLY:
            items = [item for item in items if _active(item)]
        ordered = sorted(items, key=lambda item: item.created_at, reverse=True)
        return [item.model_copy(deep=True) for item in ordered[: query.limit]]

    async def search_vector(self, search: SearchVector) -> builtins.list[SearchHit]:
        self._check_open()
        self._check_vector(search.query)
        scope = search.scope
        candidates = [
            item
            for item in self._items.values()
            if _matches_scope(item, scope)
            and (
                search.limit.state is not MemoryQuery.ACTIVE_ONLY or _active(item)
            )
        ]
        query_values = search.query.values
        scored: builtins.list[tuple[float, MemoryRecord]] = []
        for item in candidates:
            vector = self._vectors.get(item.memory_id)
            if vector is None:
                continue
            scored.append((self._cosine(query_values, vector.values), item))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [
            SearchHit(
                memory=item.model_copy(deep=True),
                score=score,
                reason="cosine",
                source="cosine",
                fallback=False,
                score_detail={"cosine_similarity": score},
            )
            for score, item in scored[: search.limit.max_results]
        ]

    async def ping(self) -> PingResult:
        self._check_open()
        return PingResult(
            ok=True,
            schema_state=SchemaState.READY,
            model_id=self._model_id,
            dimension=self._dimension,
            detail=self._schema_version,
        )

    async def close(self) -> None:
        self._closed = True

    @staticmethod
    def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return cast(float, dot / (norm_a * norm_b))