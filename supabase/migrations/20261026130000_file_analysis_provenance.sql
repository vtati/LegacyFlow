-- Vault AI analysis provenance (F3, 2026-10-26).
--
-- Until now the model path and the rule-based fallback produced
-- indistinguishable rows: model-suggested tags merged into the tag
-- list with no record of where anything came from. Two additive
-- columns fix that:
--
-- * analysis_source — 'model' | 'rules', which path produced the
--   persisted analysis (NULL for rows analyzed before this migration).
-- * ai_suggested_tags — the tags the most recent analysis suggested,
--   kept separately from the merged `tags` list. A tag present in
--   `tags` but not in `ai_suggested_tags` was added by a user; a tag
--   in both came from the analysis named by analysis_source.
--
-- file_uploads already has row level security enabled (Slice 4
-- migration 20260927030000); this migration only adds columns, so no
-- RLS change is needed. Applied idempotently.

alter table file_uploads
  add column if not exists analysis_source text
    check (analysis_source in ('model', 'rules')),
  add column if not exists ai_suggested_tags jsonb not null default '[]'::jsonb;

comment on column file_uploads.analysis_source is
  'Which path produced the persisted analysis: model (LLM via the JSON gateway) or rules (deterministic fallback). NULL = analyzed before provenance was recorded.';
comment on column file_uploads.ai_suggested_tags is
  'Tags suggested by the most recent analysis run (provenance: analysis_source), preserved separately from the merged tags list.';
