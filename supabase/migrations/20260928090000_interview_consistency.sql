-- Interview consistency review runs (Phase 3).
--
-- One row per AI consistency pass over a session's verified facts. The
-- findings themselves become escalations (attorney questions) with
-- content-hashed rule ids, so re-running a review never duplicates an
-- already-open finding.

create table interview_consistency_reviews (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references interview_sessions(id) on delete cascade,
  firm_id uuid not null references firms(id),
  matter_id uuid not null references matters(id) on delete cascade,
  findings_count int not null default 0 check (findings_count >= 0),
  escalations_created int not null default 0 check (escalations_created >= 0),
  facts_considered int not null default 0 check (facts_considered >= 0),
  model_id text not null,
  prompt_version text not null,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now()
);

create index idx_interview_consistency_reviews_session
  on interview_consistency_reviews (session_id, created_at desc);

-- Tenant isolation mirrors interview_sessions: firm members manage their
-- firm's rows; household members act on their own matters.
alter table interview_consistency_reviews enable row level security;

create policy "interview_consistency_reviews all by firm members"
on interview_consistency_reviews for all
using (public.is_firm_member(interview_consistency_reviews.firm_id))
with check (public.is_firm_member(interview_consistency_reviews.firm_id));

create policy "interview_consistency_reviews by household members on own matters"
on interview_consistency_reviews for all
using (
  exists (
    select 1 from matters m
    where m.id = interview_consistency_reviews.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = interview_consistency_reviews.matter_id
      and public.is_household_member(m.household_id)
  )
);
