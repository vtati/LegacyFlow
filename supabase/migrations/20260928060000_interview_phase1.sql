-- ============================================================================
-- LegacyFlow — AI Interviewer (Phase 1)
-- Migration: 20260928060000_interview_phase1
--
-- New domain: conversational interview sessions that collect estate-planning
-- facts through guided conversation, propose structured field values with
-- provenance, and verify each value with the user before it becomes a fact.
--
-- Tables:
--   1. ``interview_sessions`` — one active session per (matter, question-set);
--      server-side state machine (draft/in_progress/paused/completed/abandoned).
--   2. ``interview_turns`` — conversation turns; summaries + citations only.
--   3. ``interview_turn_bodies`` — encrypted full turn text (ADR-002). The
--      ciphertext is opaque to the database; the data key lives in the API
--      settings (``INTERVIEW_TURN_ENCRYPTION_KEY``), never here.
--   4. ``field_proposals`` — AI-proposed field values with provenance; the
--      model never writes facts directly. Status: proposed/confirmed/
--      corrected/rejected. One open proposal per (session, question key).
--   5. ``ai_traces`` — one row per model call: provider, model/prompt/pack
--      versions, token counts, latency, retrieved doc ids. Raw prompts and
--      responses are never stored (contract rule 9).
--
-- Conventions (carried over from Slices 1–4):
--   * UUID primary keys, gen_random_uuid().
--   * updated_at maintained by trigger (public.set_updated_at()).
--   * firm_id + matter_id denormalized onto every table for RLS.
--   * RLS enabled; the API service role bypasses RLS and enforces
--     authorization server-side (app/core/authz.py). Policies below are
--     defense-in-depth and mirror the ai_interactions pattern: firm members
--     full access; household members access rows tied to their own matters.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. interview_sessions
-- ---------------------------------------------------------------------------

create table interview_sessions (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  question_set_id text not null,
  pack_version text not null,
  status text not null default 'draft'
    check (status in ('draft', 'in_progress', 'paused', 'completed', 'abandoned')),
  created_by_user_id uuid references profiles(id),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One active session per (matter, question-set): drafts and live sessions
-- collide; completed/abandoned history may accumulate.
create unique index uq_interview_sessions_active
  on interview_sessions (matter_id, question_set_id)
  where status in ('draft', 'in_progress', 'paused');

create index idx_interview_sessions_matter on interview_sessions (matter_id);

create trigger trg_interview_sessions_set_updated_at
before update on interview_sessions
for each row execute function public.set_updated_at();

alter table interview_sessions enable row level security;

create policy "interview_sessions all by firm members"
on interview_sessions for all
using (public.is_firm_member(interview_sessions.firm_id))
with check (public.is_firm_member(interview_sessions.firm_id));

create policy "interview_sessions by household members on own matters"
on interview_sessions for all
using (
  exists (
    select 1 from matters m
    where m.id = interview_sessions.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = interview_sessions.matter_id
      and public.is_household_member(m.household_id)
  )
);

-- ---------------------------------------------------------------------------
-- 2. interview_turns (summaries + citations only — never raw text)
-- ---------------------------------------------------------------------------

create table interview_turns (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references interview_sessions(id) on delete cascade,
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  summary text not null default '',
  citations jsonb not null default '[]'::jsonb,
  proposals_emitted int not null default 0,
  idempotency_key text,
  created_at timestamptz not null default now()
);

-- Retried turns replay instead of duplicating (one key per session).
create unique index uq_interview_turns_idempotency
  on interview_turns (session_id, idempotency_key)
  where idempotency_key is not null;

create index idx_interview_turns_session on interview_turns (session_id, created_at);

alter table interview_turns enable row level security;

create policy "interview_turns all by firm members"
on interview_turns for all
using (public.is_firm_member(interview_turns.firm_id))
with check (public.is_firm_member(interview_turns.firm_id));

create policy "interview_turns by household members on own matters"
on interview_turns for all
using (
  exists (
    select 1 from matters m
    where m.id = interview_turns.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = interview_turns.matter_id
      and public.is_household_member(m.household_id)
  )
);

-- ---------------------------------------------------------------------------
-- 3. interview_turn_bodies (ADR-002: encrypted raw turn text)
-- ---------------------------------------------------------------------------

create table interview_turn_bodies (
  id uuid primary key default gen_random_uuid(),
  turn_id uuid not null unique references interview_turns(id) on delete cascade,
  session_id uuid not null references interview_sessions(id) on delete cascade,
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  -- Fernet ciphertext (key-version prefixed by the API); opaque here.
  ciphertext text not null,
  key_version smallint not null default 1,
  created_at timestamptz not null default now()
);

-- No updated_at: bodies are immutable. Retention purge deletes rows outright.

alter table interview_turn_bodies enable row level security;

create policy "interview_turn_bodies all by firm members"
on interview_turn_bodies for all
using (public.is_firm_member(interview_turn_bodies.firm_id))
with check (public.is_firm_member(interview_turn_bodies.firm_id));

create policy "interview_turn_bodies by household members on own matters"
on interview_turn_bodies for all
using (
  exists (
    select 1 from matters m
    where m.id = interview_turn_bodies.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = interview_turn_bodies.matter_id
      and public.is_household_member(m.household_id)
  )
);

-- ---------------------------------------------------------------------------
-- 4. field_proposals (provenance-carrying value proposals)
-- ---------------------------------------------------------------------------

create table field_proposals (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references interview_sessions(id) on delete cascade,
  turn_id uuid references interview_turns(id) on delete set null,
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  question_key text not null,
  proposed_value jsonb not null,
  corrected_value jsonb,
  source text not null
    check (source in ('user_stated', 'document_extracted', 'ai_inferred')),
  confidence numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  source_document_id uuid,
  source_page int,
  source_snippet text,
  model_id text not null,
  prompt_version text not null,
  pack_version text not null,
  status text not null default 'proposed'
    check (status in ('proposed', 'confirmed', 'corrected', 'rejected')),
  confirmed_by_user_id uuid references profiles(id),
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One open proposal per (session, question key): a new proposal for the same
-- key supersedes only after the old one is decided.
create unique index uq_field_proposals_open
  on field_proposals (session_id, question_key)
  where status = 'proposed';

create index idx_field_proposals_session on field_proposals (session_id, status);

create trigger trg_field_proposals_set_updated_at
before update on field_proposals
for each row execute function public.set_updated_at();

alter table field_proposals enable row level security;

create policy "field_proposals all by firm members"
on field_proposals for all
using (public.is_firm_member(field_proposals.firm_id))
with check (public.is_firm_member(field_proposals.firm_id));

create policy "field_proposals by household members on own matters"
on field_proposals for all
using (
  exists (
    select 1 from matters m
    where m.id = field_proposals.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = field_proposals.matter_id
      and public.is_household_member(m.household_id)
  )
);

-- ---------------------------------------------------------------------------
-- 5. ai_traces (one row per model call; summaries only)
-- ---------------------------------------------------------------------------

create table ai_traces (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  turn_id uuid references interview_turns(id) on delete set null,
  provider text not null,
  model_id text not null,
  model_version text,
  prompt_template text not null,
  prompt_version text not null,
  pack_version text not null,
  tokens_in int,
  tokens_out int,
  latency_ms int,
  retrieved_doc_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_ai_traces_matter on ai_traces (matter_id, created_at);
create index idx_ai_traces_turn on ai_traces (turn_id) where turn_id is not null;

alter table ai_traces enable row level security;

create policy "ai_traces select for firm or household members"
on ai_traces for select
using (
  public.is_firm_member(ai_traces.firm_id)
  or exists (
    select 1 from matters m
    where m.id = ai_traces.matter_id
      and public.is_household_member(m.household_id)
  )
);

-- Traces are written by the service role only (deny direct writes here).
