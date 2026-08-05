-- 0001_core.sql
-- Core durable memory table. Idempotent; safe to re-run.
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