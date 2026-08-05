-- 0002_pgvector.sql
-- Enable the vector extension and store embeddings with model/dimension
-- metadata. One deployment supports one configured indexed dimension.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id uuid PRIMARY KEY REFERENCES memories (memory_id) ON DELETE CASCADE,
    model_id text NOT NULL,
    dimension integer NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memory_embeddings_model_idx
    ON memory_embeddings (model_id);