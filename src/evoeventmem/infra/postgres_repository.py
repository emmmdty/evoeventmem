from __future__ import annotations

import asyncio
import builtins
import json
import threading
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager, suppress
from typing import Any, TypeVar
from uuid import UUID

import asyncpg
from pgvector.asyncpg import register_vector

from evoeventmem.core.ports import (
    EmbeddingVector,
    ListQuery,
    MemoryQuery,
    MemoryRepository,
    PingResult,
    RequestScope,
    SchemaState,
    SearchHit,
    SearchVector,
)
from evoeventmem.domain.models import (
    EntityRef,
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    RelationRef,
    normalize_memory_content,
)
from evoeventmem.infra.config import coerce_dsn
from evoeventmem.infra.migrations import (
    apply_migrations,
    ensure_schema_metadata,
    read_schema_metadata,
)

T = TypeVar("T")

_SELECT_COLUMNS = """
m.memory_id, m.schema_version, m.tenant_id, m.user_id, m.session_id, m.memory_kind,
m.content, m.normalized_content, m.entities, m.roles, m.relations, m.evidence_refs,
m.event_time, m.valid_from, m.valid_to, m.status, m.supersedes, m.superseded_by,
m.derived_from, m.derivation, m.synthetic, m.confidence, m.utility, m.embedding_version,
m.metadata, m.created_at, m.updated_at
"""


class RepositoryUnavailableError(RuntimeError):
    """Raised when the PostgreSQL store cannot be reached or times out."""


class SchemaMismatchError(RuntimeError):
    """Raised when persisted schema/model/dimension disagree with configuration."""


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _memory_to_row(memory: MemoryRecord) -> dict[str, Any]:
    return {
        "memory_id": memory.memory_id,
        "schema_version": memory.schema_version,
        "tenant_id": memory.tenant_id,
        "user_id": memory.user_id,
        "session_id": memory.session_id,
        "memory_kind": memory.memory_kind.value,
        "content": memory.content,
        "normalized_content": normalize_memory_content(memory.content),
        "entities": _to_json([entity.model_dump(mode="json") for entity in memory.entities]),
        "roles": _to_json(memory.roles),
        "relations": _to_json(
            [relation.model_dump(mode="json") for relation in memory.relations]
        ),
        "evidence_refs": _to_json(
            [evidence.model_dump(mode="json") for evidence in memory.evidence_refs]
        ),
        "event_time": memory.event_time,
        "valid_from": memory.valid_from,
        "valid_to": memory.valid_to,
        "status": memory.status.value,
        "supersedes": list(memory.supersedes),
        "superseded_by": memory.superseded_by,
        "derived_from": list(memory.derived_from),
        "derivation": memory.derivation,
        "synthetic": memory.synthetic,
        "confidence": memory.confidence,
        "utility": memory.utility,
        "embedding_version": memory.embedding_version,
        "metadata": _to_json(memory.metadata),
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def _memory_from_row(row: asyncpg.Record) -> MemoryRecord:
    return MemoryRecord(
        memory_id=row["memory_id"],
        schema_version=row["schema_version"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        memory_kind=MemoryKind(row["memory_kind"]),
        content=row["content"],
        normalized_content=row["normalized_content"],
        entities=[EntityRef(**entity) for entity in _json(row["entities"])],
        roles=_json(row["roles"]),
        relations=[RelationRef(**relation) for relation in _json(row["relations"])],
        evidence_refs=[EvidenceRef(**evidence) for evidence in _json(row["evidence_refs"])],
        event_time=row["event_time"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        status=MemoryStatus(row["status"]),
        supersedes=list(row["supersedes"]),
        superseded_by=row["superseded_by"],
        derived_from=list(row["derived_from"]),
        derivation=row["derivation"],
        synthetic=row["synthetic"],
        confidence=row["confidence"],
        utility=row["utility"],
        embedding_version=row["embedding_version"],
        metadata=_json(row["metadata"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _scope_clause(scope: RequestScope) -> tuple[str, list[Any]]:
    if scope.session_id is None:
        return (
            "m.tenant_id = $1 AND m.user_id = $2",
            [scope.tenant_id, scope.user_id],
        )
    return (
        "m.tenant_id = $1 AND m.user_id = $2 AND m.session_id = $3",
        [scope.tenant_id, scope.user_id, scope.session_id],
    )


class AsyncPostgresMemoryRepository:
    """Scope-aware async PostgreSQL repository backed by an asyncpg pool.

    Each operation acquires a connection from the pool and releases it
    afterwards; there is no dedicated event-loop thread or shared single
    connection. All scope values are passed as bound parameters, never
    interpolated into SQL.

    This is the production persistence path. The synchronous
    ``PostgresMemoryRepository`` remains only for the research ``MemoryRepository``
    contract.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout: float = 10.0,
        operation_timeout: float = 30.0,
        statement_timeout_ms: int = 10_000,
        db_connect_timeout: float = 10.0,
        db_operation_timeout: float = 30.0,
        model_id: str = "test-embed",
        dimension: int = 4,
        schema_version: str = "memory.v1",
        pool_min_size: int = 1,
        pool_max_size: int = 10,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dsn = coerce_dsn(dsn)
        self._connect_timeout = connect_timeout
        self._operation_timeout = operation_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._model_id = model_id
        self._dimension = dimension
        self._schema_version = schema_version
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._closed = False

    async def connect(self, *, run_migrations: bool = True) -> None:
        if self._closed:
            raise RepositoryUnavailableError("repository is closed")
        try:
            pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                timeout=self._connect_timeout,
                command_timeout=self._statement_timeout_ms / 1000.0,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise RepositoryUnavailableError(f"postgres connect failed: {exc}") from exc
        self._pool = pool
        try:
            async with pool.acquire() as connection:
                if run_migrations:
                    await apply_migrations(connection, dimension=self._dimension)
                await register_vector(connection)
                await ensure_schema_metadata(
                    connection,
                    schema_version=self._schema_version,
                    model_id=self._model_id,
                    dimension=self._dimension,
                )
        except BaseException:
            await pool.close()
            self._pool = None
            raise

    @property
    def connected(self) -> bool:
        return self._pool is not None and not self._closed

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None or self._closed:
            raise RepositoryUnavailableError("repository is not connected")
        return self._pool

    async def _acquire(self) -> asyncpg.Connection:
        pool = self._require_pool()
        try:
            return await pool.acquire()
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise RepositoryUnavailableError(f"postgres acquire failed: {exc}") from exc

    async def _release(self, connection: asyncpg.Connection) -> None:
        pool = self._pool
        if pool is not None:
            with suppress(Exception):
                await pool.release(connection)

    async def add(
        self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
    ) -> MemoryRecord:
        self._validate_vector(vector)
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                return await self._add_inner(connection, memory, vector)
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def get(
        self, scope: RequestScope, memory_id: UUID
    ) -> MemoryRecord | None:
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                clause, params = _scope_clause(scope)
                row = await connection.fetchrow(
                    f"SELECT {_SELECT_COLUMNS} FROM memories m WHERE {clause} "
                    f"AND m.memory_id = ${len(params) + 1}",
                    *params,
                    memory_id,
                )
            return _memory_from_row(row) if row is not None else None
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def get_with_vector(
        self, scope: RequestScope, memory_id: UUID
    ) -> tuple[MemoryRecord | None, EmbeddingVector | None]:
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                clause, params = _scope_clause(scope)
                row = await connection.fetchrow(
                    f"""SELECT {_SELECT_COLUMNS}, me.embedding, me.model_id AS emb_model,
                               me.dimension AS emb_dim
                        FROM memories m
                        LEFT JOIN memory_embeddings me ON me.memory_id = m.memory_id
                        WHERE {clause} AND m.memory_id = ${len(params) + 1}""",
                    *params,
                    memory_id,
                )
            if row is None:
                return None, None
            memory = _memory_from_row(row)
            vector = None
            if row["embedding"] is not None:
                values = tuple(float(value) for value in row["embedding"].to_list())
                vector = EmbeddingVector(
                    values=values,
                    model_id=str(row["emb_model"]),
                    dimension=int(row["emb_dim"]),
                )
            return memory, vector
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def update(
        self, scope: RequestScope, memory: MemoryRecord, vector: EmbeddingVector
    ) -> MemoryRecord:
        self._validate_vector(vector)
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                return await self._update_inner(connection, memory, vector)
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def list(
        self, scope: RequestScope, query: ListQuery
    ) -> builtins.list[MemoryRecord]:
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                clause, params = _scope_clause(scope)
                sql = f"SELECT {_SELECT_COLUMNS} FROM memories m WHERE {clause}"
                if query.status is MemoryQuery.ACTIVE_ONLY:
                    sql += " AND m.status = $"
                    sql += str(len(params) + 1)
                    params.append("active")
                sql += " ORDER BY m.created_at DESC, m.memory_id ASC LIMIT $"
                sql += str(len(params) + 1)
                params.append(query.limit)
                rows = await connection.fetch(sql, *params)
            return [_memory_from_row(row) for row in rows]
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def find_by_normalized_content(
        self, scope: RequestScope, normalized_content: str
    ) -> MemoryRecord | None:
        """Find an active memory by its normalized content within a scope.

        Uses the unique index on (tenant_id, user_id, normalized_content) for O(1) lookup
        instead of O(n) full table scan.
        """
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                clause, params = _scope_clause(scope)
                row = await connection.fetchrow(
                    f"SELECT {_SELECT_COLUMNS} FROM memories m WHERE {clause} "
                    f"AND m.normalized_content = ${len(params) + 1} "
                    f"AND m.status = 'active' "
                    f"LIMIT 1",
                    *params,
                    normalized_content,
                )
            return _memory_from_row(row) if row is not None else None
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def search_vector(
        self, search: SearchVector
    ) -> builtins.list[SearchHit]:
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                return await self._search_vector_inner(connection, search)
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        finally:
            await self._release(connection)

    async def ping(self) -> PingResult:
        if not self.connected:
            return PingResult(ok=False, schema_state=SchemaState.MISSING)
        connection = await self._acquire()
        try:
            async with asyncio.timeout(self._operation_timeout):
                await connection.fetchval("SELECT 1")
                metadata = await read_schema_metadata(connection)
            if (
                metadata.get("schema_version") != self._schema_version
                or metadata.get("embedding_model_id") != self._model_id
                or metadata.get("embedding_dimension") != str(self._dimension)
            ):
                return PingResult(
                    ok=False,
                    schema_state=SchemaState.MISMATCH,
                    model_id=self._model_id,
                    dimension=self._dimension,
                    detail="configured schema/model/dimension mismatch",
                )
            return PingResult(
                ok=True,
                schema_state=SchemaState.READY,
                model_id=self._model_id,
                dimension=self._dimension,
                detail=self._schema_version,
            )
        except TimeoutError as exc:
            return PingResult(
                ok=False,
                schema_state=SchemaState.MISSING,
                detail=f"postgres operation timed out: {exc}",
            )
        except (OSError, asyncpg.PostgresError) as exc:
            return PingResult(
                ok=False,
                schema_state=SchemaState.MISSING,
                detail=f"postgres unreachable: {exc}",
            )
        finally:
            await self._release(connection)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pool = self._pool
        self._pool = None
        if pool is not None:
            await pool.close()

    def _validate_vector(self, vector: EmbeddingVector) -> None:
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

    async def _add_inner(
        self,
        connection: asyncpg.Connection,
        memory: MemoryRecord,
        vector: EmbeddingVector,
    ) -> MemoryRecord:
        row_data = _memory_to_row(memory)
        columns = list(row_data.keys())
        placeholders = [f"${index}" for index in range(1, len(columns) + 1)]
        returning = ", ".join(column for column in columns)
        insert_sql = (
            f"INSERT INTO memories ({', '.join(columns)}) "
            f"VALUES ({', '.join(placeholders)}) "
            f"ON CONFLICT (memory_id) DO NOTHING RETURNING {returning}"
        )
        row = await connection.fetchrow(insert_sql, *row_data.values())
        if row is None:
            raise KeyError(f"no memory with id {memory.memory_id}")
        await connection.execute(
            """INSERT INTO memory_embeddings (memory_id, model_id, dimension, embedding)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (memory_id) DO UPDATE SET
                    model_id = EXCLUDED.model_id,
                    dimension = EXCLUDED.dimension,
                    embedding = EXCLUDED.embedding""",
            memory.memory_id,
            vector.model_id,
            vector.dimension,
            list(vector.values),
        )
        return _memory_from_row(row)

    async def _update_inner(
        self,
        connection: asyncpg.Connection,
        memory: MemoryRecord,
        vector: EmbeddingVector,
    ) -> MemoryRecord:
        row_data = _memory_to_row(memory)
        columns = list(row_data.keys())
        set_clause = ", ".join(
            f"{column} = ${index}" for index, column in enumerate(columns, start=2)
        )
        update_sql = (
            f"UPDATE memories SET {set_clause} WHERE memory_id = $1 "
            f"RETURNING memory_id, schema_version, tenant_id, user_id, session_id, "
            f"memory_kind, content, normalized_content, entities, roles, relations, "
            f"evidence_refs, event_time, valid_from, valid_to, status, supersedes, "
            f"superseded_by, derived_from, derivation, synthetic, confidence, utility, "
            f"embedding_version, metadata, created_at, updated_at"
        )
        row = await connection.fetchrow(update_sql, memory.memory_id, *row_data.values())
        if row is None:
            raise KeyError(f"no memory with id {memory.memory_id}")
        await connection.execute(
            """INSERT INTO memory_embeddings (memory_id, model_id, dimension, embedding)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (memory_id) DO UPDATE SET
                    model_id = EXCLUDED.model_id,
                    dimension = EXCLUDED.dimension,
                    embedding = EXCLUDED.embedding""",
            memory.memory_id,
            vector.model_id,
            vector.dimension,
            list(vector.values),
        )
        return _memory_from_row(row)

    async def _search_vector_inner(
        self,
        connection: asyncpg.Connection,
        search: SearchVector,
    ) -> builtins.list[SearchHit]:
        self._validate_vector(search.query)
        scope = search.scope
        clause, params = _scope_clause(scope)
        next_param = len(params) + 1
        sql = (
            f"""SELECT {_SELECT_COLUMNS},
                       (1 - (me.embedding <=> ${next_param}::vector)) AS cosine_score
                FROM memories m
                JOIN memory_embeddings me ON me.memory_id = m.memory_id
                WHERE {clause}"""
        )
        params.append(list(search.query.values))
        next_param += 1
        if search.limit.state is MemoryQuery.ACTIVE_ONLY:
            sql += f" AND m.status = ${next_param}"
            params.append("active")
            next_param += 1
        sql += (
            f" ORDER BY cosine_score DESC, m.memory_id ASC LIMIT ${next_param}"
        )
        params.append(search.limit.max_results)
        rows = await connection.fetch(sql, *params)
        hits: builtins.list[SearchHit] = []
        for row in rows:
            cosine = float(row["cosine_score"])
            hits.append(
                SearchHit(
                    memory=_memory_from_row(row),
                    score=cosine,
                    reason="pgvector cosine",
                    source="pgvector",
                    fallback=False,
                    score_detail={"cosine_similarity": cosine},
                )
            )
        return hits


# ---------------------------------------------------------------------------
# Synchronous repository backing the research MemoryRepository contract.
# Kept unchanged for research/domain compatibility; not the production path.
# The production path is AsyncPostgresMemoryRepository (asyncpg pool, no thread).
# ---------------------------------------------------------------------------


class _SyncLoop:
    """Blocking adapter running coroutines on a dedicated background event loop.

    The dedicated loop lets the synchronous facade be called from inside an
    already-running asyncio loop (e.g. FastAPI's lifespan) and from the
    research contract tests. This facade is research/domain compatibility only;
    the production async path uses the pool directly and no thread.
    """

    def __init__(self, operation_timeout: float) -> None:
        self._operation_timeout = operation_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="evoeventmem-postgres-sync",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RepositoryUnavailableError("postgres sync loop did not start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        loop = self._loop
        if loop is None:
            raise RepositoryUnavailableError("postgres sync loop is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=self._operation_timeout)
        except TimeoutError as exc:
            raise RepositoryUnavailableError("postgres operation timed out") from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise RepositoryUnavailableError(f"postgres operation failed: {exc}") from exc

    def shutdown(self) -> None:
        loop = self._loop
        if loop is not None:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(loop.stop)
                self._thread.join(timeout=5.0)


class PostgresMemoryRepository:
    """Synchronous view over ``AsyncPostgresMemoryRepository`` for the research
    ``MemoryRepository`` contract. Not the production path.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout: float = 10.0,
        operation_timeout: float = 30.0,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        self._async = AsyncPostgresMemoryRepository(
            dsn,
            connect_timeout=connect_timeout,
            operation_timeout=operation_timeout,
            statement_timeout_ms=statement_timeout_ms,
        )
        self._loop = _SyncLoop(operation_timeout)

    @property
    def connected(self) -> bool:
        return self._async.connected

    def connect(self, *, apply_migrations: bool = True) -> None:
        self._loop.run(self._async.connect(run_migrations=apply_migrations))

    def run_migrations(self) -> list[str]:
        return self._loop.run(_run_migrations(self._async))

    def close(self) -> None:
        with suppress(Exception):
            self._loop.run(self._async.close())
        self._loop.shutdown()

    def ping(self) -> bool:
        result = self._loop.run(self._async.ping())
        return bool(result.ok)

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        scope = RequestScope(
            tenant_id=memory.tenant_id or "",
            user_id=memory.user_id,
            session_id=memory.session_id,
        )
        vector = EmbeddingVector(
            values=tuple(0.0 for _ in range(self._async._dimension)),
            model_id=self._async._model_id,
            dimension=self._async._dimension,
        )
        return self._loop.run(self._async.add(scope, memory, vector))

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        # The sync research contract has no scope; search across all rows.
        return self._loop.run(_sync_get(self._async, memory_id))

    def update(self, memory: MemoryRecord) -> MemoryRecord:
        scope = RequestScope(
            tenant_id=memory.tenant_id or "",
            user_id=memory.user_id,
            session_id=memory.session_id,
        )
        vector = EmbeddingVector(
            values=tuple(0.0 for _ in range(self._async._dimension)),
            model_id=self._async._model_id,
            dimension=self._async._dimension,
        )
        return self._loop.run(self._async.update(scope, memory, vector))

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        return self._loop.run(_sync_list(self._async, user_id))

    @contextmanager
    def transaction(self) -> Iterator[MemoryRepository]:
        raise RuntimeError("synchronous postgres transactions are not supported")


async def _run_migrations(repository: AsyncPostgresMemoryRepository) -> list[str]:
    pool = repository._require_pool()
    async with pool.acquire() as connection:
        return await apply_migrations(connection)


async def _sync_get(
    repository: AsyncPostgresMemoryRepository, memory_id: UUID
) -> MemoryRecord | None:
    pool = repository._require_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM memories m WHERE m.memory_id = $1", memory_id
        )
        return _memory_from_row(row) if row is not None else None


async def _sync_list(
    repository: AsyncPostgresMemoryRepository, user_id: str
) -> list[MemoryRecord]:
    pool = repository._require_pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM memories m WHERE m.user_id = $1 "
            "ORDER BY m.created_at ASC, m.memory_id ASC",
            user_id,
        )
        return [_memory_from_row(row) for row in rows]