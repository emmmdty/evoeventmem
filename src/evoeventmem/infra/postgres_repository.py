from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Coroutine, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import Any, TypeVar
from uuid import UUID

import asyncpg

from evoeventmem.core.ports import MemoryRepository
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
from evoeventmem.infra.migrations import apply_migrations

T = TypeVar("T")

_SELECT_COLUMNS = """
memory_id, schema_version, tenant_id, user_id, session_id, memory_kind,
content, normalized_content, entities, roles, relations, evidence_refs,
event_time, valid_from, valid_to, status, supersedes, superseded_by,
derived_from, derivation, synthetic, confidence, utility, embedding_version,
metadata, created_at, updated_at
"""


class RepositoryUnavailableError(RuntimeError):
    """Raised when the PostgreSQL store cannot be reached or times out."""


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


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


def _row_to_memory(row: asyncpg.Record) -> MemoryRecord:
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


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PostgresMemoryRepository:
    """Async PostgreSQL repository exposed through a synchronous protocol.

    All asyncpg work runs on a dedicated worker-thread event loop so the
    repository satisfies the synchronous ``MemoryRepository`` port used by the
    service layer. Operations are serialized on that loop, which mirrors the
    lock semantics of the in-memory implementation.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout: float = 10.0,
        operation_timeout: float = 30.0,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        self._dsn = coerce_dsn(dsn)
        self._connect_timeout = connect_timeout
        self._operation_timeout = operation_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._conn: asyncpg.Connection | None = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="evoeventmem-postgres",
            daemon=True,
        )
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RepositoryUnavailableError("postgres event loop did not start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        loop.run_forever()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def connect(self, *, apply_migrations: bool = True) -> None:
        connection = self._submit(self._connect_coro())
        if apply_migrations:
            self._submit(self._migrate_coro(connection))
        self._conn = connection

    def run_migrations(self) -> list[str]:
        """Apply pending migrations on the current connection; returns applied versions."""
        connection = self._require_connection()
        return self._submit(self._migrate_coro(connection))

    def close(self) -> None:
        conn = self._conn
        self._conn = None
        loop = self._loop
        if conn is not None and loop is not None:
            with suppress(RepositoryUnavailableError):
                self._submit(conn.close())
        if loop is not None:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(loop.stop)
                self._thread.join(timeout=5.0)

    def ping(self) -> bool:
        try:
            return bool(self._submit(self._conn.fetchval("SELECT 1")) if self._conn else False)
        except RepositoryUnavailableError:
            return False

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        connection = self._require_connection()
        return self._submit(self._add(connection, memory))

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        connection = self._require_connection()
        return self._submit(self._get(connection, memory_id))

    def update(self, memory: MemoryRecord) -> MemoryRecord:
        connection = self._require_connection()
        return self._submit(self._update(connection, memory))

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        connection = self._require_connection()
        return self._submit(self._list_for_user(connection, user_id))

    @contextmanager
    def transaction(self) -> Iterator[MemoryRepository]:
        self._require_connection()
        dedicated = self._submit(self._connect_coro())
        transaction = self._submit(self._begin_coro(dedicated))
        try:
            yield _PostgresTransactionView(self, dedicated)
        except BaseException:
            self._submit(self._rollback_coro(transaction))
            raise
        else:
            self._submit(self._commit_coro(transaction))
        finally:
            self._submit(dedicated.close())

    def _require_connection(self) -> asyncpg.Connection:
        if self._conn is None:
            raise RepositoryUnavailableError("repository is not connected")
        return self._conn

    def _submit(self, coroutine: Coroutine[Any, Any, T]) -> T:
        loop = self._loop
        if loop is None:
            raise RepositoryUnavailableError("postgres event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=self._operation_timeout)
        except TimeoutError as exc:
            raise RepositoryUnavailableError(f"postgres operation timed out: {exc}") from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise RepositoryUnavailableError(f"postgres operation failed: {exc}") from exc

    async def _connect_coro(self) -> asyncpg.Connection:
        connection = await asyncpg.connect(self._dsn, timeout=self._connect_timeout)
        await connection.execute(f"SET statement_timeout = {int(self._statement_timeout_ms)}")
        return connection

    async def _migrate_coro(self, connection: asyncpg.Connection) -> list[str]:
        return await apply_migrations(connection)

    async def _begin_coro(
        self, connection: asyncpg.Connection
    ) -> asyncpg.transaction.Transaction:
        transaction = connection.transaction()
        await transaction.start()
        return transaction

    async def _commit_coro(self, transaction: asyncpg.transaction.Transaction) -> None:
        await transaction.commit()

    async def _rollback_coro(self, transaction: asyncpg.transaction.Transaction) -> None:
        await transaction.rollback()

    async def _add(
        self, connection: asyncpg.Connection, memory: MemoryRecord
    ) -> MemoryRecord:
        row = await connection.fetchrow(
            f"""
            INSERT INTO memories ({_SELECT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24,
                    $25, $26, $27)
            ON CONFLICT (memory_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                user_id = EXCLUDED.user_id,
                session_id = EXCLUDED.session_id,
                schema_version = EXCLUDED.schema_version,
                memory_kind = EXCLUDED.memory_kind,
                content = EXCLUDED.content,
                normalized_content = EXCLUDED.normalized_content,
                entities = EXCLUDED.entities,
                roles = EXCLUDED.roles,
                relations = EXCLUDED.relations,
                evidence_refs = EXCLUDED.evidence_refs,
                event_time = EXCLUDED.event_time,
                valid_from = EXCLUDED.valid_from,
                valid_to = EXCLUDED.valid_to,
                status = EXCLUDED.status,
                supersedes = EXCLUDED.supersedes,
                superseded_by = EXCLUDED.superseded_by,
                derived_from = EXCLUDED.derived_from,
                derivation = EXCLUDED.derivation,
                synthetic = EXCLUDED.synthetic,
                confidence = EXCLUDED.confidence,
                utility = EXCLUDED.utility,
                embedding_version = EXCLUDED.embedding_version,
                metadata = EXCLUDED.metadata,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at
            RETURNING {_SELECT_COLUMNS}
            """,
            *_row_values(memory),
        )
        return _row_to_memory(row)

    async def _get(
        self, connection: asyncpg.Connection, memory_id: UUID
    ) -> MemoryRecord | None:
        row = await connection.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM memories WHERE memory_id = $1",
            memory_id,
        )
        return _row_to_memory(row) if row is not None else None

    async def _update(
        self, connection: asyncpg.Connection, memory: MemoryRecord
    ) -> MemoryRecord:
        row = await connection.fetchrow(
            f"""
            UPDATE memories SET
                schema_version = $2, tenant_id = $3, user_id = $4,
                session_id = $5, memory_kind = $6, content = $7,
                normalized_content = $8, entities = $9, roles = $10,
                relations = $11, evidence_refs = $12, event_time = $13,
                valid_from = $14, valid_to = $15, status = $16,
                supersedes = $17, superseded_by = $18, derived_from = $19,
                derivation = $20, synthetic = $21, confidence = $22,
                utility = $23, embedding_version = $24, metadata = $25,
                created_at = $26, updated_at = $27
            WHERE memory_id = $1
            RETURNING {_SELECT_COLUMNS}
            """,
            *_row_values(memory),
        )
        if row is None:
            raise KeyError(f"no memory with id {memory.memory_id}")
        return _row_to_memory(row)

    async def _list_for_user(
        self, connection: asyncpg.Connection, user_id: str
    ) -> list[MemoryRecord]:
        rows = await connection.fetch(
            f"""
            SELECT {_SELECT_COLUMNS} FROM memories
            WHERE user_id = $1
            ORDER BY created_at ASC, memory_id ASC
            """,
            user_id,
        )
        return [_row_to_memory(row) for row in rows]


def _row_values(memory: MemoryRecord) -> tuple[Any, ...]:
    row = _memory_to_row(memory)
    return tuple(row[column.strip()] for column in _SELECT_COLUMNS.split(","))


class _PostgresTransactionView:
    """Repository view bound to a dedicated transaction connection."""

    def __init__(
        self, repository: PostgresMemoryRepository, connection: asyncpg.Connection
    ) -> None:
        self._repository = repository
        self._connection = connection

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        return self._repository._submit(self._repository._add(self._connection, memory))

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        return self._repository._submit(self._repository._get(self._connection, memory_id))

    def update(self, memory: MemoryRecord) -> MemoryRecord:
        return self._repository._submit(self._repository._update(self._connection, memory))

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        return self._repository._submit(
            self._repository._list_for_user(self._connection, user_id)
        )

    def transaction(self) -> AbstractContextManager[MemoryRepository]:
        raise RuntimeError("nested transactions are not supported")
