-- 0003_pgvector_hnsw.sql
-- One deployment supports one configured indexed embedding dimension; the
-- HNSW cosine index backs the scoped vector search path. Idempotent.
CREATE INDEX IF NOT EXISTS memory_embeddings_embedding_hnsw_idx
    ON memory_embeddings USING hnsw (embedding vector_cosine_ops);
