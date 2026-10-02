-- Vault AI analysis: extraction + document intelligence on file_uploads.
--
-- Adds text extraction storage and AI analysis results (document type,
-- summary, key facts, tags) to the files domain. Applied idempotently.

alter table file_uploads
  add column if not exists extracted_text text,
  add column if not exists extraction_status text not null default 'pending'
    check (extraction_status in ('pending', 'done', 'unavailable', 'failed')),
  add column if not exists document_type text,
  add column if not exists ai_summary text,
  add column if not exists key_facts jsonb not null default '[]'::jsonb,
  add column if not exists tags jsonb not null default '[]'::jsonb,
  add column if not exists analysis_confidence double precision
    check (analysis_confidence is null or (analysis_confidence >= 0 and analysis_confidence <= 1)),
  add column if not exists analyzed_at timestamptz;

comment on column file_uploads.extracted_text is
  'Plain text extracted at upload completion (PDF/DOCX); truncated to ~20k chars. Images: NULL with extraction_status=unavailable.';
comment on column file_uploads.key_facts is
  'AI-extracted key facts: [{label, value}] — e.g. deed -> property address + owners.';
comment on column file_uploads.tags is
  'Combined tag list: AI-suggested tags plus user-added tags (deduplicated).';
