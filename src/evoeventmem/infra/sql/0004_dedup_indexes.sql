-- 0004_dedup_indexes.sql
-- Add indexes for write deduplication at the database layer.
-- This eliminates O(n) full table scans on every write.

-- Unique index for write dedup: prevents duplicate normalized_content within a scope.
-- Partial index: only active memories (not deleted/forgotten) participate in dedup.
CREATE UNIQUE INDEX IF NOT EXISTS memories_scope_normalized_content_idx
    ON memories (tenant_id, user_id, normalized_content)
    WHERE status = 'active';

-- Index for idempotency key lookup (used in batch write pipeline).
-- Extracts the idempotency_key from the metadata JSONB path.
CREATE INDEX IF NOT EXISTS memories_idempotency_key_idx
    ON memories ((metadata->'write_pipeline'->>'idempotency_key'))
    WHERE (metadata->'write_pipeline'->>'idempotency_key') IS NOT NULL;
