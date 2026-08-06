from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from evoeventmem.core.ports import (
    AsyncEmbeddingModel,
    AsyncMemoryRepository,
    EmbeddingVector,
    ListQuery,
    MemoryQuery,
    RequestScope,
    SearchHit,
    SearchLimit,
    SearchVector,
)
from evoeventmem.domain.models import MemoryRecord, MemoryStatus
from evoeventmem.services import memory_rules
from evoeventmem.services.memory_service import (
    MemoryExplainResult,
    MemoryIdentityCollisionError,
)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

_SCOPE_MISMATCH_MESSAGE = "scope/body identity mismatch"


class ScopeMismatchError(ValueError):
    """A record identity disagrees with the request scope (stable reason code)."""

    def __init__(self) -> None:
        super().__init__(_SCOPE_MISMATCH_MESSAGE)

# The token-overlap search is the development baseline mirror of the
# synchronous starter search. It is active only under an explicit
# development_token_overlap policy; every result records degradation.
_TOKEN_OVERLAP_REASON = "degraded token-overlap baseline"
_TOKEN_OVERLAP_FALLBACK_REASON = "development_token_overlap_policy"


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def token_overlap_score(query: str, content: str) -> float:
    """Jaccard-style token overlap between a query and memory content."""
    query_tokens = _tokens(query)
    memory_tokens = _tokens(content)
    union = query_tokens | memory_tokens
    if not union:
        return 0.0
    return len(query_tokens & memory_tokens) / len(union)


class AsyncMemoryService:
    """Scoped async application service over the production async ports.

    Uses the same pure business rules as the synchronous ``MemoryService``
    (collision/idempotency identity, scope consistency, feedback, forget) and
    awaits the scope-aware async repository. All UUID lookups require a
    ``RequestScope``; missing or wrong-scope records return ``None`` so the
    API cannot distinguish not-found from out-of-scope.
    """

    def __init__(
        self,
        repository: AsyncMemoryRepository,
        embedding: AsyncEmbeddingModel | None = None,
        *,
        token_overlap_policy: bool = False,
    ) -> None:
        self._repository = repository
        self._embedding = embedding
        self._token_overlap_policy = token_overlap_policy

    @property
    def token_overlap_policy(self) -> bool:
        return self._token_overlap_policy

    async def write(self, scope: RequestScope, memory: MemoryRecord) -> MemoryRecord:
        if not memory_rules.scope_matches_memory(
            memory,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            session_id=scope.session_id,
        ):
            raise ScopeMismatchError
        existing = await self._repository.get(scope, memory.memory_id)
        if existing is not None:
            if memory_rules.legacy_write_identity(
                existing
            ) != memory_rules.legacy_write_identity(memory):
                raise MemoryIdentityCollisionError
            return existing
        scoped = await self._repository.list(
            scope,
            ListQuery(limit=1000, status=MemoryQuery.ALL),
        )
        for item in scoped:
            if item.status is MemoryStatus.DELETED:
                continue
            if item.normalized_content == memory.normalized_content:
                return item
        vector = await self._embed_document(memory)
        return await self._repository.add(scope, memory, vector)

    async def search(self, scope: RequestScope, query: str, limit: int = 5) -> list[SearchHit]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if self._token_overlap_policy:
            return await self._search_token_overlap(scope, query, limit)
        if self._embedding is None:
            raise RuntimeError("no embedding model configured for vector search")
        query_vector = await self._embedding.embed_query(query)
        return await self._repository.search_vector(
            SearchVector(
                query=query_vector,
                scope=scope,
                limit=SearchLimit(state=MemoryQuery.ACTIVE_ONLY, max_results=limit),
            )
        )

    async def explain(self, scope: RequestScope, memory_id: UUID) -> MemoryExplainResult | None:
        memory = await self._repository.get(scope, memory_id)
        if memory is None:
            return None
        linked_ids = set(memory.supersedes)
        linked_ids.update(memory.derived_from)
        if memory.superseded_by is not None:
            linked_ids.add(memory.superseded_by)
        scoped = await self._repository.list(
            scope,
            ListQuery(limit=1000, status=MemoryQuery.ALL),
        )
        related = [
            item
            for item in scoped
            if item.status is not MemoryStatus.DELETED and item.memory_id in linked_ids
        ]
        return MemoryExplainResult(memory=memory, related=related)

    async def feedback(
        self,
        scope: RequestScope,
        memory_id: UUID,
        *,
        outcome: str,
        rating: float | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord | None:
        memory, vector = await self._get_memory_with_vector(scope, memory_id)
        if memory is None:
            return None
        recorded_at = datetime.now(UTC)
        updated = memory.model_copy(
            update=memory_rules.apply_feedback(
                memory,
                outcome=outcome,
                rating=rating,
                recorded_at=recorded_at,
                request_id=request_id,
            )
        )
        return await self._repository.update(scope, updated, vector)

    async def forget(
        self,
        scope: RequestScope,
        memory_id: UUID,
        *,
        request_id: str | None = None,
    ) -> MemoryRecord | None:
        memory, vector = await self._get_memory_with_vector(scope, memory_id)
        if memory is None:
            return None
        forgotten_at = datetime.now(UTC)
        updated = memory.model_copy(
            update=memory_rules.apply_forget(
                memory,
                forgotten_at=forgotten_at,
                request_id=request_id,
            )
        )
        return await self._repository.update(scope, updated, vector)

    async def _get_memory_with_vector(
        self, scope: RequestScope, memory_id: UUID
    ) -> tuple[MemoryRecord | None, EmbeddingVector]:
        memory, vector = await self._repository.get_with_vector(scope, memory_id)
        if memory is None:
            return None, None  # type: ignore[return-value]
        if vector is None:
            raise RuntimeError("memory has no stored embedding; cannot update it")
        return memory, vector

    async def _embed_document(self, memory: MemoryRecord) -> EmbeddingVector:
        if self._embedding is None:
            raise RuntimeError("no embedding model configured")
        return await self._embedding.embed_document(memory.content)

    async def _search_token_overlap(
        self, scope: RequestScope, query: str, limit: int
    ) -> list[SearchHit]:
        scoped = await self._repository.list(
            scope,
            ListQuery(limit=1000, status=MemoryQuery.ACTIVE_ONLY),
        )
        scored: list[tuple[float, MemoryRecord]] = []
        for item in scoped:
            entity_text = " ".join(entity.name for entity in item.entities)
            score = token_overlap_score(query, f"{item.content} {entity_text}")
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].memory_id)))
        return [
            SearchHit(
                memory=item,
                score=score,
                reason=_TOKEN_OVERLAP_REASON,
                source="token_overlap",
                fallback=True,
                fallback_reason=_TOKEN_OVERLAP_FALLBACK_REASON,
                score_detail={"token_overlap": score},
            )
            for score, item in scored[:limit]
        ]
