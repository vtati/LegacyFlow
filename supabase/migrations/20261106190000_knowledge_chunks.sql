-- ============================================================================
-- Knowledge chunks for hybrid Ask retrieval (2026-11-06, Venugopal-approved
-- RAG program)
--
-- Stores chunk-level embeddings of pack knowledge documents so Ask can run
-- hybrid retrieval (lexical term-frequency UNION cosine similarity, RRF
-- fusion) instead of the doc-level lexical scan. One row per chunk:
-- (pack_id, pack_version, content_id, seq) is the identity; text_hash makes
-- re-indexing idempotent — unchanged chunks are not re-embedded.
--
-- Backend-only table: RLS enabled with NO policies (owner connections
-- bypass, API roles default-deny) per the standing RLS rule.
-- ============================================================================

create extension if not exists vector;

create table knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  pack_id text not null,
  pack_version text not null,
  content_id text not null,
  section text,
  seq integer not null,
  title text not null,
  chunk_text text not null,
  text_hash text not null,
  status text not null,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (pack_id, pack_version, content_id, seq)
);

create index knowledge_chunks_pack_idx
  on knowledge_chunks (pack_id, pack_version, status);

create index knowledge_chunks_embedding_idx
  on knowledge_chunks using hnsw (embedding vector_cosine_ops);

alter table knowledge_chunks enable row level security;
-- No policies: backend-only (see header).
