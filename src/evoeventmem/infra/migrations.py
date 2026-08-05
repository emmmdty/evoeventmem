from __future__ import annotations

from pathlib import Path

import asyncpg

_SQL_DIR = Path(__file__).parent / "sql"


def _load_sql(name: str) -> str:
    return (_SQL_DIR / name).read_text(encoding="utf-8")


MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0001_core_schema", _load_sql("0001_core.sql")),
    ("0002_pgvector", _load_sql("0002_pgvector.sql")),
)

_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""

_SCHEMA_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key text PRIMARY KEY,
    value text NOT NULL
)
"""


async def apply_migrations(connection: asyncpg.Connection) -> list[str]:
    """Apply pending migrations in order, each in its own transaction.

    Returns the list of versions applied during this call.
    """
    await connection.execute(_MIGRATION_TABLE)
    applied_rows = await connection.fetch("SELECT version FROM schema_migrations")
    applied = {row["version"] for row in applied_rows}
    applied_now: list[str] = []
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        async with connection.transaction():
            await connection.execute(sql)
            await connection.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", version
            )
        applied_now.append(version)
    return applied_now


async def ensure_schema_metadata(
    connection: asyncpg.Connection,
    *,
    schema_version: str,
    model_id: str,
    dimension: int,
) -> None:
    """Record the configured schema/model/dimension for readiness checks.

    Idempotent. If an existing value disagrees with the configured value, the
    deployment does not silently migrate existing data; it raises so readiness
    fails (mismatch) rather than serving with the wrong embedding shape.
    """
    await connection.execute(_SCHEMA_METADATA_TABLE)
    expected = {
        "schema_version": schema_version,
        "embedding_model_id": model_id,
        "embedding_dimension": str(dimension),
    }
    for key, value in expected.items():
        row = await connection.fetchrow(
            "SELECT value FROM schema_metadata WHERE key = $1", key
        )
        if row is None:
            await connection.execute(
                "INSERT INTO schema_metadata (key, value) VALUES ($1, $2)", key, value
            )
        elif row["value"] != value:
            raise SchemaMismatchError(
                f"schema_metadata[{key}] has {row['value']!r} but configured {value!r}"
            )


async def read_schema_metadata(
    connection: asyncpg.Connection,
) -> dict[str, str]:
    await connection.execute(_SCHEMA_METADATA_TABLE)
    rows = await connection.fetch("SELECT key, value FROM schema_metadata")
    return {row["key"]: row["value"] for row in rows}


class SchemaMismatchError(RuntimeError):
    """Raised when persisted schema metadata disagrees with the configuration."""