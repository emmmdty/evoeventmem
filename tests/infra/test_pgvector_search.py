from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from evoeventmem.core.ports import (
    EmbeddingVector,
    MemoryQuery,
    RequestScope,
    SearchHit,
    SearchLimit,
    SearchVector,
)
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord, MemoryStatus
from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    RepositoryUnavailableError,
)

_PG_LOOP = asyncio.new_event_loop()


def _run(coro: object) -> object:
    asyncio.set_event_loop(_PG_LOOP)
    return _PG_LOOP.run_until_complete(coro)  # type: ignore[arg-type]


def _require_postgres() -> bool:
    return os.environ.get("EEM_REQUIRE_POSTGRES", "0") == "1"


def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("EEM_DATABASE_URL")


@pytest.fixture(autouse=True)
def _reset_database() -> None:
    """Start each test from a fresh schema so hit-count assertions are
    not polluted by rows written by other tests on the shared DB."""
    dsn = _dsn()
    if not dsn:
        return

    async def wipe() -> None:
        import asyncpg

        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute("DROP EXTENSION IF EXISTS vector CASCADE")
            await connection.execute(
                "DROP TABLE IF EXISTS memory_embeddings, memories, "
                "schema_metadata, schema_migrations CASCADE"
            )
        finally:
            await connection.close()

    _run(wipe())


@pytest.fixture()
def postgres_repository() -> Iterator[AsyncPostgresMemoryRepository]:
    dsn = _dsn()
    if not dsn:
        if _require_postgres():
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
        if _require_postgres():
            pytest.fail(f"EEM_REQUIRE_POSTGRES=1 but PostgreSQL connection failed: {exc}")
        pytest.skip(f"PostgreSQL connection failed: {exc}")
    yield repository
    _run(repository.close())


def _scope(
    tenant: str = "tenant-a", user: str = "user-a", session: str = "session-a"
) -> RequestScope:
    return RequestScope(tenant_id=tenant, user_id=user, session_id=session)


def _record(*, content: str, session_id: str = "session-a") -> MemoryRecord:
    from datetime import UTC, datetime

    event_time = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    return MemoryRecord(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id=session_id,
        memory_kind=MemoryKind.EVENT,
        content=content,
        evidence_refs=[EvidenceRef(source_type="turn", source_id="session-a:1")],
        event_time=event_time,
        valid_from=event_time,
    )


def _vector(*values: float) -> EmbeddingVector:
    return EmbeddingVector(values=values, model_id="test-model", dimension=4)


def _search(query: EmbeddingVector, scope: RequestScope, *, limit: int = 10) -> SearchVector:
    return SearchVector(
        query=query,
        scope=scope,
        limit=SearchLimit(state=MemoryQuery.ACTIVE_ONLY, max_results=limit),
    )


@pytest.mark.postgres
def test_pgvector_counts_cosine_ordering(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        await postgres_repository.add(
            scope, _record(content="npmmirror registry switch"), _vector(1.0, 0.0, 0.0, 0.0)
        )
        await postgres_repository.add(
            scope, _record(content="caroline joined support group"), _vector(0.0, 1.0, 0.0, 0.0)
        )
        await postgres_repository.add(
            scope, _record(content="npmmirror npm mirror"), _vector(0.9, 0.1, 0.0, 0.0)
        )

        hits = await postgres_repository.search_vector(_search(_vector(1.0, 0.0, 0.0, 0.0), scope))

        assert len(hits) == 3
        assert hits[0].memory.content == "npmmirror registry switch"
        assert hits[0].reason == "pgvector cosine"
        assert hits[1].memory.content == "npmmirror npm mirror"
        assert isinstance(hits[0], SearchHit)
        assert hits[0].score >= hits[1].score >= hits[2].score

    _run(scenario())


@pytest.mark.postgres
def test_pgvector_filters_active_status(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        active = await postgres_repository.add(
            scope, _record(content="npmmirror registry switch"), _vector(1.0, 0.0, 0.0, 0.0)
        )
        deleted = await postgres_repository.add(
            scope, _record(content="npmmirror old registry"), _vector(0.9, 0.0, 0.0, 0.0)
        )
        deleted_record = deleted.model_copy(
            update={
                "status": MemoryStatus.DELETED,
                "metadata": {"forgotten_at": "2024-06-01T00:00:00+00:00"},
            }
        )
        await postgres_repository.update(scope, deleted_record, _vector(0.9, 0.0, 0.0, 0.0))

        hits = await postgres_repository.search_vector(_search(_vector(1.0, 0.0, 0.0, 0.0), scope))
        assert [hit.memory.memory_id for hit in hits] == [active.memory_id]

    _run(scenario())


@pytest.mark.postgres
def test_pgvector_isolates_tenant_user_session(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        await postgres_repository.add(
            scope, _record(content="npmmirror registry switch"), _vector(1.0, 0.0, 0.0, 0.0)
        )
        await postgres_repository.add(
            scope.with_session("session-b"),
            _record(content="npmmirror session b", session_id="session-b"),
            _vector(1.0, 0.0, 0.0, 0.0),
        )
        await postgres_repository.add(
            _scope("tenant-b", "user-b"),
            _record(content="npmmirror tenant b", session_id="session-a").model_copy(
                update={"tenant_id": "tenant-b", "user_id": "user-b"}
            ),
            _vector(1.0, 0.0, 0.0, 0.0),
        )

        hits = await postgres_repository.search_vector(_search(_vector(1.0, 0.0, 0.0, 0.0), scope))
        assert len(hits) == 1
        assert hits[0].memory.tenant_id == "tenant-a"
        assert hits[0].memory.user_id == "user-a"
        assert hits[0].memory.session_id == "session-a"

    _run(scenario())


@pytest.mark.postgres
def test_pgvector_source_and_score_decomposition(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        await postgres_repository.add(
            scope, _record(content="npmmirror registry switch"), _vector(1.0, 0.0, 0.0, 0.0)
        )
        hits = await postgres_repository.search_vector(_search(_vector(1.0, 0.0, 0.0, 0.0), scope))
        assert hits
        assert hits[0].reason == "pgvector cosine"
        assert fits(hits[0].score)
        assert hits[0].source == "pgvector"
        assert hits[0].fallback is False
        assert hits[0].fallback_reason is None
        assert hits[0].score_detail is not None
        assert hits[0].score_detail["cosine_similarity"] == pytest.approx(hits[0].score)

    _run(scenario())


def fits(score: float) -> bool:
    return -1.0 <= score <= 1.0


@pytest.mark.postgres
def test_pgvector_explain_uses_vector_capable_path(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        await postgres_repository.add(
            scope, _record(content="npmmirror registry switch"), _vector(1.0, 0.0, 0.0, 0.0)
        )
        pool = postgres_repository._require_pool()
        async with pool.acquire() as connection:
            await connection.execute("SET enable_seqscan = off")
            try:
                plan_rows = await connection.fetch(
                    "EXPLAIN SELECT memory_id FROM memory_embeddings "
                    "ORDER BY embedding <=> '[1,0,0,0]'::vector LIMIT 5"
                )
            finally:
                await connection.execute("SET enable_seqscan = on")
        plan = "\n".join(str(row[0]) for row in plan_rows)
        # The HNSW vector index must be a candidate for the ordered vector query.
        # Only the index name is asserted, never the exact plan layout.
        assert "memory_embeddings_embedding_hnsw_idx" in plan

    _run(scenario())


@pytest.mark.postgres
def test_pgvector_query_vector_dimension_mismatch_rejected(
    postgres_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        bad = EmbeddingVector(
            values=(1.0, 2.0, 3.0, 4.0, 5.0), model_id="test-model", dimension=5
        )
        with pytest.raises(ValueError):
            await postgres_repository.search_vector(_search(bad, scope))

    _run(scenario())