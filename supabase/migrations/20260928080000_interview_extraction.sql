-- Interview document extraction runs (Phase 2).
--
-- One row per uploaded source document sent through AI extraction. The
-- extracted facts themselves live in ``field_proposals`` with
-- source='document_extracted' and source_document_id pointing here, so every
-- value keeps its document provenance through verification.

create table interview_extractions (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references interview_sessions(id) on delete cascade,
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  filename text not null,
  file_size int not null check (file_size >= 0),
  page_count int not null default 0 check (page_count >= 0),
  status text not null
    check (status in ('completed', 'no_readable_text', 'failed')),
  proposals_created int not null default 0 check (proposals_created >= 0),
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now()
);

create index idx_interview_extractions_session
  on interview_extractions (session_id, created_at desc);

-- Tenant isolation mirrors interview_sessions: firm members manage their
-- firm's rows; household members see rows for their matters.
alter table interview_extractions enable row level security;

create policy "interview_extractions all by firm members"
on interview_extractions for all
using (public.is_firm_member(interview_extractions.firm_id))
with check (public.is_firm_member(interview_extractions.firm_id));

create policy "interview_extractions by household members on own matters"
on interview_extractions for all
using (
  exists (
    select 1 from matters m
    where m.id = interview_extractions.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = interview_extractions.matter_id
      and public.is_household_member(m.household_id)
  )
);
