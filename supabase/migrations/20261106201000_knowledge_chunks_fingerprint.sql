-- ============================================================================
-- knowledge_chunks: sync fingerprint, embedding model, source spans
-- (2026-11-06, RAG program review fixes)
--
-- fingerprint: canonical hash of (title, section, effective status,
-- embedding model, text) — a status flip or model change re-indexes even
-- when the body text is unchanged. Backfilled from text_hash; the next
-- sync rewrites every row's fingerprint under the canonical formula.
-- embedding_model: rows are only ranked by the active model.
-- char_start/char_end: the chunk's exact span in its source document,
-- so citations can be traced to the matched passage.
-- ============================================================================

alter table knowledge_chunks add column embedding_model text;
alter table knowledge_chunks add column char_start integer;
alter table knowledge_chunks add column char_end integer;
alter table knowledge_chunks add column fingerprint text;

update knowledge_chunks set fingerprint = text_hash;
