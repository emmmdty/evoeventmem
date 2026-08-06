from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from evoeventmem.core.ports import (
    EmbeddingVector,
    MemoryQuery,
    RequestScope,
    SchemaState,
    SearchLimit,
    SearchVector,
)
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.infra.migrations import MIGRATIONS
from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    PostgresMemoryRepository,
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


@pytest.fixture()
def connected_repository() -> Iterator[AsyncPostgresMemoryRepository]:
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


def _record() -> MemoryRecord:
    from datetime import UTC, datetime

    event_time = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    return MemoryRecord(
        tenant_id="tenant-1",
        user_id="contract-user",
        session_id="session-1",
        memory_kind=MemoryKind.EVENT,
        content="registry switched to npmmirror",
        evidence_refs=[EvidenceRef(source_type="turn", source_id="session-1:1")],
        event_time=event_time,
        valid_from=event_time,
    )


def _vector(*values: float) -> EmbeddingVector:
    return EmbeddingVector(values=values, model_id="test-model", dimension=4)


def _scope() -> RequestScope:
    return RequestScope(tenant_id="tenant-1", user_id="contract-user", session_id="session-1")


def test_unreachable_database_url_raises_repository_unavailable() -> None:
    repository = PostgresMemoryRepository(
        "postgresql://user:secret@127.0.0.1:1/evoeventmem",
        connect_timeout=1.0,
        operation_timeout=5.0,
    )
    try:
        with pytest.raises(RepositoryUnavailableError):
            repository.connect(apply_migrations=True)
        assert not repository.connected
    finally:
        repository.close()


def test_ping_false_while_disconnected() -> None:
    repository = PostgresMemoryRepository(
        "postgresql://user:secret@127.0.0.1:1/evoeventmem",
        connect_timeout=1.0,
        operation_timeout=5.0,
    )
    try:
        assert repository.ping() is False
    finally:
        repository.close()


@pytest.mark.postgres
def test_connect_registers_vector_type_after_migrations() -> None:
    """connect() must create the vector extension (migrations) before the
    pgvector codec registration introspects public.vector (regression)."""

    async def scenario() -> None:
        import asyncpg

        dsn = _dsn()
        if not dsn:
            if _require_postgres():
                pytest.fail("EEM_REQUIRE_POSTGRES=1 but no DATABASE_URL is configured")
            pytest.skip("DATABASE_URL is not set; PostgreSQL integration tests are skipped")
        reset = await asyncpg.connect(dsn)
        try:
            await reset.execute("DROP EXTENSION IF EXISTS vector CASCADE")
            await reset.execute(
                "DROP TABLE IF EXISTS memory_embeddings, memories, "
                "schema_metadata, schema_migrations CASCADE"
            )
        finally:
            await reset.close()

        repository = AsyncPostgresMemoryRepository(
            dsn,
            connect_timeout=5.0,
            operation_timeout=15.0,
            model_id="test-model",
            dimension=4,
        )
        try:
            await repository.connect(run_migrations=True)
            assert repository.connected
            scope = _scope()
            added = await repository.add(scope, _record(), _vector(1.0, 0.0, 0.0, 0.0))
            hits = await repository.search_vector(
                SearchVector(
                    query=_vector(1.0, 0.0, 0.0, 0.0),
                    scope=scope,
                    limit=SearchLimit(state=MemoryQuery.ACTIVE_ONLY, max_results=10),
                )
            )
            assert any(hit.memory.memory_id == added.memory_id for hit in hits)
        finally:
            await repository.close()

    _run(scenario())


@pytest.mark.postgres
def test_migrations_are_versioned_and_idempotent(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    assert {version for version, _ in MIGRATIONS} == {
        "0001_core_schema",
        "0002_pgvector",
        "0003_pgvector_hnsw",
    }


@pytest.mark.postgres
def test_migrations_apply_twice_without_changes(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        from evoeventmem.infra.migrations import apply_migrations

        pool = connected_repository._require_pool()
        async with pool.acquire() as connection:
            applied_first = await apply_migrations(connection)
            applied_second = await apply_migrations(connection)
        assert applied_first == []
        assert applied_second == []

    _run(scenario())


@pytest.mark.postgres
def test_schema_metadata_apply_twice_is_idempotent(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        from evoeventmem.infra.migrations import ensure_schema_metadata

        pool = connected_repository._require_pool()
        async with pool.acquire() as connection:
            await ensure_schema_metadata(
                connection,
                schema_version="memory.v1",
                model_id="test-model",
                dimension=4,
            )
            await ensure_schema_metadata(
                connection,
                schema_version="memory.v1",
                model_id="test-model",
                dimension=4,
            )

    _run(scenario())


@pytest.mark.postgres
def test_ping_true_when_connected(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        ping = await connected_repository.ping()
        assert ping.ok is True
        assert ping.schema_state is SchemaState.READY

    _run(scenario())


@pytest.mark.postgres
def test_vector_extension_is_available(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        pool = connected_repository._require_pool()
        async with pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            )
        assert value == 1

    _run(scenario())


@pytest.mark.postgres
def test_embeddings_stored_with_model_and_dimension(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        memory = _record()
        await connected_repository.add(scope, memory, _vector(1.0, 0.0, 0.0, 0.0))
        record, vector = await connected_repository.get_with_vector(scope, memory.memory_id)
        assert record is not None
        assert vector is not None
        assert vector.model_id == "test-model"
        assert vector.dimension == 4
        assert len(vector.values) == 4

    _run(scenario())


@pytest.mark.postgres
def test_schema_metadata_records_model_dimension(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        from evoeventmem.infra.migrations import read_schema_metadata

        pool = connected_repository._require_pool()
        async with pool.acquire() as connection:
            metadata = await read_schema_metadata(connection)
        assert metadata["embedding_model_id"] == "test-model"
        assert metadata["embedding_dimension"] == "4"
        assert metadata["schema_version"] == "memory.v1"

    _run(scenario())


@pytest.mark.postgres
def test_readiness_fails_on_dimension_mismatch(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        from evoeventmem.infra.migrations import SchemaMismatchError

        mismatched = AsyncPostgresMemoryRepository(
            connected_repository._dsn,
            connect_timeout=5.0,
            operation_timeout=15.0,
            model_id="test-model",
            dimension=8,
        )
        with pytest.raises(SchemaMismatchError):
            await mismatched.connect(run_migrations=False)
        assert not mismatched.connected
        await mismatched.close()

    _run(scenario())


@pytest.mark.postgres
def test_dimension_invalid_write_is_rejected(
    connected_repository: AsyncPostgresMemoryRepository,
) -> None:
    async def scenario() -> None:
        scope = _scope()
        with pytest.raises(ValueError):
            await connected_repository.add(
                scope,
                _record(),
                EmbeddingVector(
                    values=(1.0, 0.0, 0.0, 0.0, 0.0),
                    model_id="test-model",
                    dimension=5,
                ),
            )

    _run(scenario())