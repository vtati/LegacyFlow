-- ============================================================================
-- Worker dead letters (ERR-003, deep review 2026-10-29)
--
-- When a Dramatiq actor exhausts its retries, the Retries middleware
-- forwards the message to the worker's record_dead_letter actor, which
-- inserts one row here: actor name, message id, an args SUMMARY (ids
-- and scalars only — never payloads/PII), the error class, and the
-- retry count. Operators alert on new rows (runbook).
--
-- Backend-only table: RLS enabled with NO policies (owner connections
-- bypass, API roles default-deny) per the standing RLS rule.
-- ============================================================================

create table worker_dead_letters (
  id uuid primary key default gen_random_uuid(),
  actor_name text not null,
  message_id text,
  args_summary jsonb not null default '{}'::jsonb,
  error_class text,
  retries integer,
  created_at timestamptz not null default now()
);

create index worker_dead_letters_created_idx on worker_dead_letters (created_at desc);

alter table worker_dead_letters enable row level security;
-- No policies: backend-only (see header).
