-- Phase 4 (brief 2026-10-26, P4): "don't know yet" parked items.
-- A park marker is a first-class answer state at the MATTER level:
-- the interview excludes parked keys from its focus and follow-ups,
-- progress displays them distinctly, and document generation stays
-- gated because a parked item is still unanswered. An answer written
-- anywhere supersedes the marker (the service computes effective
-- parked = markers minus answered keys), so no resolution write is
-- needed when the family fills the item in later.
create table if not exists interview_parked_items (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id) on delete cascade,
  question_key text not null,
  parked_by_user_id uuid references profiles(id),
  created_at timestamptz not null default now(),
  unique (matter_id, question_key)
);

alter table interview_parked_items enable row level security;
