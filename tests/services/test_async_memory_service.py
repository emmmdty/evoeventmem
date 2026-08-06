from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from evoeventmem.core.ports import (
    EmbeddingVector,
    ListQuery,
    RequestScope,
    SearchHit,
)
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord, MemoryStatus
from evoeventmem.infra.async_embedding import DeterministicAsyncEmbeddingModel
from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.infra.postgres_repository import RepositoryUnavailableError
from evoeventmem.services.async_memory_service import AsyncMemoryService
from evoeventmem.services.memory_service import MemoryIdentityCollisionError


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _scope(
    tenant: str = "tenant-a", user: str = "user-a", session: str = "session-a"
) -> RequestScope:
    return RequestScope(tenant_id=tenant, user_id=user, session_id=session)


def _record(
    *,
    content: str = "the registry switched to npmmirror",
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    session_id: str = "session-a",
) -> MemoryRecord:
    event_time = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    return MemoryRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        memory_kind=MemoryKind.EVENT,
        content=content,
        evidence_refs=[EvidenceRef(source_type="turn", source_id="session-a:1")],
        event_time=event_time,
        valid_from=event_time,
    )


def _embedding() -> DeterministicAsyncEmbeddingModel:
    return DeterministicAsyncEmbeddingModel(model_id="test-model", dimension=4)


def _make_service(
    *, token_overlap: bool = False,
) -> tuple[AsyncMemoryService, AsyncInMemoryRepository]:
    repository = AsyncInMemoryRepository(model_id="test-model", dimension=4)
    service = AsyncMemoryService(
        repository,
        embedding=_embedding(),
        token_overlap_policy=token_overlap,
    )
    return service, repository


def test_async_service_write_embeds_and_persists_scoped() -> None:
    async def scenario() -> None:
        service, repository = _make_service()
        scope = _scope()
        stored = await service.write(scope, _record())
        assert stored.memory_id is not None
        fetched = await repository.get(scope, stored.memory_id)
        assert fetched is not None
        assert fetched.content == "the registry switched to npmmirror"
        record, vector = await repository.get_with_vector(scope, stored.memory_id)
        assert record is not None
        assert vector is not None
        assert vector.model_id == "test-model"
        assert vector.dimension == 4

    _run(scenario())


def test_async_service_write_rejects_scope_body_mismatch() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        mismatched = _record().model_copy(
            update={"tenant_id": "other-tenant", "user_id": "other-user"}
        )
        with pytest.raises(ValueError, match="scope/body identity mismatch"):
            await service.write(_scope(), mismatched)

    _run(scenario())


def test_async_service_write_is_idempotent_within_scope() -> None:
    async def scenario() -> None:
        service, repository = _make_service()
        scope = _scope()
        first = await service.write(scope, _record())
        second = await service.write(scope, _record())
        assert second.memory_id == first.memory_id
        assert len(await repository.list(scope, ListQuery(limit=10))) == 1

    _run(scenario())


def test_async_service_write_same_content_different_scope_is_distinct() -> None:
    async def scenario() -> None:
        service, repository = _make_service()
        scope_a = _scope()
        scope_b = _scope("tenant-b", "user-b", "session-b")
        memory_b = _record().model_copy(
            update={"tenant_id": "tenant-b", "user_id": "user-b", "session_id": "session-b"}
        )
        first = await service.write(scope_a, _record())
        second = await service.write(scope_b, memory_b)
        assert second.memory_id != first.memory_id

    _run(scenario())


def test_async_service_write_memory_id_collision_raises() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        original = _record(content="original content")
        await service.write(scope, original)
        replaced = _record(content="different content").model_copy(
            update={"memory_id": original.memory_id}
        )
        with pytest.raises(MemoryIdentityCollisionError):
            await service.write(scope, replaced)

    _run(scenario())


def test_async_service_search_returns_scoped_cosine_hits() -> None:
    async def scenario() -> None:
        service, repository = _make_service()
        scope = _scope()
        await service.write(scope, _record(content="npmmirror registry switch"))
        await service.write(
            scope.with_session("session-other"),
            _record(content="npmmirror session b", session_id="session-other"),
        )
        hits = await service.search(scope, "npmmirror", limit=5)
        assert hits
        assert all(isinstance(hit, SearchHit) for hit in hits)
        assert all(hit.memory.session_id == "session-a" for hit in hits)
        assert hits[0].source == "cosine"
        assert hits[0].fallback is False
        assert hits[0].score_detail is not None

    _run(scenario())


def test_async_service_search_token_overlap_is_degraded_only_when_explicit() -> None:
    async def scenario() -> None:
        service, _ = _make_service(token_overlap=True)
        scope = _scope()
        await service.write(scope, _record(content="npmmirror registry switch"))
        hits = await service.search(scope, "npmmirror", limit=5)
        assert hits
        assert hits[0].source == "token_overlap"
        assert hits[0].fallback is True
        assert hits[0].fallback_reason == "development_token_overlap_policy"
        assert "degraded" in hits[0].reason

    _run(scenario())


def test_async_service_search_without_token_overlap_uses_vectors() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        await service.write(scope, _record(content="npmmirror registry switch"))
        hits = await service.search(scope, "npmmirror", limit=5)
        assert hits[0].source == "cosine"
        assert hits[0].fallback is False

    _run(scenario())


def test_async_service_explain_not_found_and_wrong_scope_are_indistinguishable() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        stored = await service.write(scope, _record(content="npmmirror registry"))
        missing = await service.explain(scope, uuid4())
        wrong_user = await service.explain(
            _scope("tenant-a", "user-b"), stored.memory_id
        )
        wrong_tenant = await service.explain(
            _scope("tenant-b", "user-a"), stored.memory_id
        )
        assert missing is None
        assert wrong_user is None
        assert wrong_tenant is None
        explained = await service.explain(scope, stored.memory_id)
        assert explained is not None
        assert explained.memory.memory_id == stored.memory_id
        assert explained.related == []

    _run(scenario())


def test_async_service_feedback_uses_shared_rules() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        stored = await service.write(scope, _record(content="npmmirror registry"))
        updated = await service.feedback(
            scope, stored.memory_id, outcome="useful", rating=0.9, request_id="req-1"
        )
        assert updated is not None
        events = updated.metadata["feedback_events"]
        assert events[-1]["outcome"] == "useful"
        assert events[-1]["rating"] == 0.9
        assert events[-1]["request_id"] == "req-1"
        assert await service.feedback(
            _scope("tenant-b", "user-b"), stored.memory_id, outcome="useless"
        ) is None

    _run(scenario())


def test_async_service_forget_uses_shared_rules() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        stored = await service.write(scope, _record(content="npmmirror registry"))
        forgotten = await service.forget(scope, stored.memory_id, request_id="req-2")
        assert forgotten is not None
        assert forgotten.status is MemoryStatus.DELETED
        assert forgotten.metadata["forgotten_at"] is not None
        assert forgotten.metadata["forget_request_id"] == "req-2"
        assert await service.forget(_scope("tenant-b", "user-b"), stored.memory_id) is None

    _run(scenario())


def test_async_service_forget_removes_from_search() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        stored = await service.write(scope, _record(content="npmmirror registry"))
        await service.forget(scope, stored.memory_id)
        assert await service.search(scope, "npmmirror", limit=5) == []

    _run(scenario())


def test_async_service_write_never_dedups_onto_forgotten_memories() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        scope = _scope()
        original = await service.write(scope, _record(content="npmmirror registry"))
        forgotten = await service.forget(scope, original.memory_id)
        assert forgotten is not None
        assert forgotten.status is MemoryStatus.DELETED
        fresh = await service.write(scope, _record(content="npmmirror registry"))
        assert fresh.memory_id != original.memory_id
        assert fresh.status is not MemoryStatus.DELETED
        assert "forgotten_at" not in fresh.metadata
        hits = await service.search(scope, "npmmirror", limit=5)
        assert any(hit.memory.memory_id == fresh.memory_id for hit in hits)
        assert not any(hit.memory.memory_id == original.memory_id for hit in hits)

    _run(scenario())


def test_async_service_propagates_repository_unavailability() -> None:
    class _UnavailableRepository(AsyncInMemoryRepository):
        async def add(
            self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
        ) -> MemoryRecord:
            raise RepositoryUnavailableError("postgres operation timed out")

    async def scenario() -> None:
        service = AsyncMemoryService(
            _UnavailableRepository(model_id="test-model", dimension=4),
            embedding=_embedding(),
        )
        with pytest.raises(RepositoryUnavailableError):
            await service.write(_scope(), _record())

    _run(scenario())


def test_async_service_write_requires_scope_for_all_uuid_lookups() -> None:
    async def scenario() -> None:
        service, _ = _make_service()
        with pytest.raises(TypeError):
            await service.explain(uuid4())  # type: ignore[call-arg]

    _run(scenario())
