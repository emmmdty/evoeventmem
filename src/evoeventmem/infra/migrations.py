from __future__ import annotations

import asyncpg

_INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id uuid PRIMARY KEY,
    schema_version text NOT NULL,
    tenant_id text,
    user_id text NOT NULL,
    session_id text,
    memory_kind text NOT NULL,
    content text NOT NULL,
    normalized_content text NOT NULL,
    entities jsonb NOT NULL DEFAULT '[]'::jsonb,
    roles jsonb NOT NULL DEFAULT '{}'::jsonb,
    relations jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    event_time timestamptz,
    valid_from timestamptz,
    valid_to timestamptz,
    status text NOT NULL,
    supersedes uuid[] NOT NULL DEFAULT '{}'::uuid[],
    superseded_by uuid,
    derived_from uuid[] NOT NULL DEFAULT '{}'::uuid[],
    derivation text,
    synthetic boolean NOT NULL DEFAULT false,
    confidence double precision NOT NULL,
    utility double precision NOT NULL,
    embedding_version text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_user_id_idx ON memories (user_id);
CREATE INDEX IF NOT EXISTS memories_tenant_user_idx ON memories (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS memories_status_idx ON memories (status);
CREATE INDEX IF NOT EXISTS memories_created_at_idx ON memories (created_at);
"""

MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0001_initial_schema", _INITIAL_SCHEMA),
)

_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
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
