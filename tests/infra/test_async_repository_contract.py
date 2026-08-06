from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from evoeventmem.core.ports import (
    EmbeddingVector,
    ListQuery,
    MemoryQuery,
    PingResult,
    RequestScope,
    SchemaState,
    SearchHit,
    SearchLimit,
    SearchVector,
)
from evoeventmem.domain.models import (
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
)
from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    RepositoryUnavailableError,
)

_PG_LOOP = asyncio.new_event_loop()


def _run(coro: object) -> object:
    asyncio.set_event_loop(_PG_LOOP)
    return _PG_LOOP.run_until_complete(coro)  # type: ignore[arg-type]


@pytest.fixture()
def postgres_repository() -> Iterator[AsyncPostgresMemoryRepository]:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("EEM_DATABASE_URL")
    require = os.environ.get("EEM_REQUIRE_POSTGRES", "0") == "1"
    if not dsn:
        if require:
            pytest.fail("EEM_REQUIRE_POSTGRES=1 but no DATABASE_URL is configured")
        pytest.skip("DATABASE_URL is not set; PostgreSQL integration tests are skipped")
    repository = AsyncPostgresMemoryRepository(
        dsn,
        connect_timeout=5.0,
        operation_timeout=15.0,
        model_id="test-model",
        dimension=4,
    )
    try:
        _run(repository.connect(run_migrations=True))
    except (RepositoryUnavailableError, OSError) as exc:
        if require:
            pytest.fail(f"EEM_REQUIRE_POSTGRES=1 but PostgreSQL connection failed: {exc}")
        pytest.skip(f"PostgreSQL connection failed: {exc}")
    yield repository
    _run(repository.close())


def _scope(tenant: str = "tenant-1", user: str = "contract-user") -> RequestScope:
    return RequestScope(tenant_id=tenant, user_id=user, session_id="session-1")


def _record(
    *,
    content: str = "registry switched to npmmirror",
    user_id: str = "contract-user",
    tenant_id: str = "tenant-1",
    session_id: str | None = "session-1",
) -> MemoryRecord:
    event_time = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    return MemoryRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        memory_kind=MemoryKind.EVENT,
        content=content,
        evidence_refs=[
            EvidenceRef(
                source_type="turn",
                source_id="session-1:1",
                locator="chars=0:20",
                quote="we switched the registry",
            )
        ],
        event_time=event_time,
        valid_from=event_time,
    )


def _vector(*values: float, model_id: str = "test-model", dimension: int = 4) -> EmbeddingVector:
    return EmbeddingVector(values=values, model_id=model_id, dimension=dimension)


def test_async_repository_roundtrip_preserves_fields() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        scope = _scope()
        memory = _record()
        stored = await repository.add(scope, memory, _vector(1.0, 0.0, 0.0, 0.0))
        assert stored == memory
        fetched = await repository.get(scope, stored.memory_id)
        assert fetched is not None
        assert fetched == memory
        assert fetched.tenant_id == "tenant-1"
        assert fetched.content == memory.content

    _run(scenario())


def test_async_repository_write_receives_vector_separately() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        scope = _scope()
        vector = _vector(1.0, 2.0, 3.0, 4.0)
        stored = await repository.add(scope, _record(), vector)
        record, stored_vector = await repository.get_with_vector(scope, stored.memory_id)
        assert record is not None
        assert stored_vector == vector

    _run(scenario())


def test_async_repository_update_changes_record() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        scope = _scope()
        original = await repository.add(scope, _record(), _vector(1.0, 0.0, 0.0, 0.0))
        updated = original.model_copy(update={"content": "updated content"})
        result = await repository.update(
            scope, updated, _vector(0.0, 1.0, 0.0, 0.0)
        )
        assert result.content == "updated content"
        assert result.memory_id == updated.memory_id
        fetched = await repository.get(scope, original.memory_id)
        assert fetched is not None
        assert fetched.content == "updated content"

    _run(scenario())


def test_async_repository_update_missing_raises() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        scope = _scope()
        with pytest.raises(KeyError):
            await repository.update(scope, _record(), _vector(1.0, 0.0, 0.0, 0.0))

    _run(scenario())


def test_async_repository_list_scopes_by_tenant_user_session() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        scope = _scope()
        visible = await repository.add(
            scope, _record(content="visible memory"), _vector(1.0, 0.0, 0.0, 0.0)
        )
        await repository.add(
            scope.with_session("session-2"),
            _record(session_id="session-2", content="other session memory"),
            _vector(0.0, 1.0, 0.0, 0.0),
        )
        await repository.add(
            _scope("tenant-1", "other-user"),
            _record(user_id="other-user", content="other user memory"),
            _vector(0.0, 0.0, 0.0, 1.0),
        )

        listed = await repository.list(scope, ListQuery(limit=10))
        assert [item.memory_id for item in listed] == [visible.memory_id]

    _run(scenario())


def test_async_repository_get_requires_scope() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        with pytest.raises(TypeError):
            await repository.get(uuid4())  # type: ignore[call-arg]

    _run(scenario())


def test_async_repository_get_wrong_scope_returns_none() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        stored = await repository.add(
            _scope("tenant-a", "user-a"),
            _record(tenant_id="tenant-a", user_id="user-a"),
            _vector(1.0, 0.0, 0.0, 0.0),
        )
        wrong_user = await repository.get(_scope("tenant-a", "user-b"), stored.memory_id)
        wrong_tenant = await repository.get(_scope("tenant-b", "user-a"), stored.memory_id)
        wrong_session = await repository.get(
            _scope("tenant-a", "user-a").with_session("session-other"),
            stored.memory_id,
        )
        assert wrong_user is None
        assert wrong_tenant is None
        assert wrong_session is None

    _run(scenario())


def test_async_repository_rejects_dimension_mismatch_on_write() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository(dimension=4, model_id="test-model")
        with pytest.raises(ValueError):
            await repository.add(_scope(), _record(), _vector(1.0, 2.0, 3.0, 4.0, 5.0))

    _run(scenario())


def test_async_repository_ping_reports_ready_and_schema() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository(
            dimension=4, model_id="test-model", schema_version="memory.v1"
        )
        ping = await repository.ping()
        assert isinstance(ping, PingResult)
        assert ping.ok is True
        assert ping.schema_state is SchemaState.READY
        assert ping.dimension == 4
        assert ping.model_id == "test-model"

    _run(scenario())


def test_async_repository_close_is_idempotent() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository()
        await repository.close()
        await repository.close()

    _run(scenario())


def test_async_repository_rejects_nonfinite_vector_values() -> None:
    with pytest.raises(ValueError):
        _vector(1.0, float("nan"), 0.0, 0.0)
    with pytest.raises(ValueError):
        _vector(1.0, float("inf"), 0.0, 0.0)


def test_async_repository_embedding_vector_dimension_matches_values() -> None:
    with pytest.raises(ValueError):
        EmbeddingVector(values=(1.0, 2.0), model_id="m", dimension=4)


def test_async_repository_search_vector_is_scoped() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository(dimension=4, model_id="test-model")
        scope = _scope()
        stored = await repository.add(
            scope,
            _record(content="npmmirror registry story"),
            _vector(1.0, 0.0, 0.0, 0.0),
        )

        search = SearchVector(
            query=_vector(1.0, 0.0, 0.0, 0.0),
            scope=scope,
            limit=SearchLimit(state=MemoryQuery.ACTIVE_ONLY, max_results=10),
        )
        hits = await repository.search_vector(search)
        assert hits
        assert isinstance(hits[0], SearchHit)
        assert hits[0].memory.memory_id == stored.memory_id

    _run(scenario())


def test_async_repository_isolation_between_tenants() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository(dimension=4, model_id="test-model")
        tenant_a = _scope("tenant-a", "user-a")
        tenant_b = _scope("tenant-b", "user-b")
        await repository.add(
            tenant_a,
            _record(tenant_id="tenant-a", user_id="user-a", content="a memory"),
            _vector(1.0, 0.0, 0.0, 0.0),
        )
        listed_b = await repository.list(tenant_b, ListQuery(limit=10))
        assert listed_b == []

    _run(scenario())


def test_async_repository_search_hit_exposes_source_and_fallback_state() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository(dimension=4, model_id="test-model")
        scope = _scope()
        await repository.add(
            scope,
            _record(content="npmmirror registry story"),
            _vector(1.0, 0.0, 0.0, 0.0),
        )
        search = SearchVector(
            query=_vector(1.0, 0.0, 0.0, 0.0),
            scope=scope,
            limit=SearchLimit(state=MemoryQuery.ACTIVE_ONLY, max_results=10),
        )
        hits = await repository.search_vector(search)
        assert hits
        hit = hits[0]
        assert hit.source == "cosine"
        assert hit.fallback is False
        assert hit.fallback_reason is None
        assert hit.score_detail is not None
        assert hit.score_detail["cosine_similarity"] == pytest.approx(hit.score)

    _run(scenario())


def test_async_repository_searches_scoped_candidates() -> None:
    async def scenario() -> None:
        repository = AsyncInMemoryRepository(dimension=4, model_id="test-model")
        scope = _scope()
        await repository.add(
            scope.with_session("session-0"),
            _record(content="shared default session", session_id="session-0"),
            _vector(1.0, 0.0, 0.0, 0.0),
        )
        search = SearchVector(
            query=_vector(1.0, 0.0, 0.0, 0.0),
            scope=scope.with_session("session-0"),
            limit=SearchLimit(state=MemoryQuery.ACTIVE_ONLY, max_results=10),
        )
        hits = await repository.search_vector(search)
        assert len(hits) == 1

    _run(scenario())


# ---------------------------------------------------------------------------
# D3: pooled async PostgreSQL repository (fail-not-skip under EEM_REQUIRE_POSTGRES)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_async_postgres_roundtrip_with_pool(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        repository = postgres_repository
        scope = _scope()
        memory = _record()
        stored = await repository.add(scope, memory, _vector(1.0, 0.0, 0.0, 0.0))
        assert stored.memory_id == memory.memory_id
        fetched = await repository.get(scope, stored.memory_id)
        assert fetched is not None
        assert fetched.content == memory.content

    _run(scenario())


@pytest.mark.postgres
def test_async_postgres_scope_filters_every_lookup(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        repository = postgres_repository
        scope = _scope()
        memory = _record()
        await repository.add(scope, memory, _vector(1.0, 0.0, 0.0, 0.0))
        wrong_user = await repository.get(_scope("tenant-1", "user-x"), memory.memory_id)
        wrong_session = await repository.get(
            scope.with_session("session-other"), memory.memory_id
        )
        assert wrong_user is None
        assert wrong_session is None

    _run(scenario())


@pytest.mark.postgres
def test_async_postgres_uses_asyncpg_pool(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    assert postgres_repository._pool is not None
    assert not hasattr(postgres_repository, "_thread")
    assert not hasattr(postgres_repository, "_conn")


@pytest.mark.postgres
def test_async_postgres_close_is_async_and_idempotent(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        await postgres_repository.close()
        await postgres_repository.close()
        assert not postgres_repository.connected

    _run(scenario())


def test_async_postgres_unreachable_raises_repository_unavailable() -> None:
    async def scenario() -> None:
        repository = AsyncPostgresMemoryRepository(
            "postgresql://user:secret@127.0.0.1:1/evoeventmem",
            connect_timeout=1.0,
            operation_timeout=5.0,
            model_id="test-model",
            dimension=4,
        )
        with pytest.raises(RepositoryUnavailableError):
            await repository.connect(run_migrations=True)
        assert not repository.connected
        with pytest.raises(RepositoryUnavailableError):
            await repository.get(_scope(), uuid4())

    _run(scenario())


@pytest.mark.postgres
def test_async_postgres_ping_reports_ready(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        ping = await postgres_repository.ping()
        assert ping.ok is True
        assert ping.schema_state is SchemaState.READY
        assert ping.dimension == 4
        assert ping.model_id == "test-model"

    _run(scenario())