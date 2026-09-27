-- ============================================================================
-- LegacyFlow — Household Intake Slice 2
-- Migration: 20260927010000_household_intake_slice2
--
-- Extends the Slice 1 foundation (20260926220000_foundation_slice1) with the
-- household-intake domain: new columns on households / household_members /
-- matters, plus new tables persons, relationships, invitations,
-- intake_answers, planning_decisions, nominations, escalations — all with
-- Row Level Security reusing the Slice 1 security-definer helpers
-- (public.is_firm_member, public.is_firm_admin, public.is_household_member,
-- public.household_firm_id) and the public.set_updated_at() trigger helper.
--
-- Conventions (§7.1, carried over from Slice 1):
--   * UUID primary keys, gen_random_uuid() (core since PG13).
--   * updated_at maintained by trigger on tables that carry the column.
--   * firm tenancy enforced via RLS; the API service role bypasses RLS and
--     enforces authorization server-side (app/core/authz.py, app/core/audit.py).
--
-- Local test note: like Slice 1, this migration is validated against a local
-- pgserver instance whose bundled Postgres lacks contrib extension files, so
-- the citext install below is guarded the same way pgcrypto was in Slice 1.
-- On real Postgres (CI postgres:16 image, Supabase) citext IS available and
-- invitations.email is promoted to citext.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0. citext extension (guarded: absent from the local pgserver test bundle)
-- ---------------------------------------------------------------------------
do $citext_guard$
begin
  create extension if not exists citext;
exception when sqlstate '0A000' then
  raise notice 'citext extension files unavailable in this environment; invitations.email stays text (promoted to citext where the extension exists).';
end
$citext_guard$;

-- ---------------------------------------------------------------------------
-- 1. Legacy matter-status normalization
-- Slice 1 seeds/fixtures used the free-text status 'intake'. The intake
-- state machine is now closed: normalize legacy rows, then lock the column
-- to the 12 canonical states.
-- ---------------------------------------------------------------------------
update matters set status = 'INTAKE_IN_PROGRESS' where status = 'intake';

alter table matters
  add constraint matters_status_check check (status in (
    'DRAFT','INTAKE_IN_PROGRESS','INTAKE_SUBMITTED','ATTORNEY_REVIEW',
    'CLARIFICATION_REQUESTED','ESCALATED','APPROVED_FOR_ASSEMBLY',
    'DOCUMENTS_RELEASED','SIGNING_IN_PROGRESS','FUNDING_IN_PROGRESS',
    'PLAN_IMPLEMENTED','ARCHIVED'
  ));

-- ---------------------------------------------------------------------------
-- 2. households.status
-- ---------------------------------------------------------------------------
alter table households
  add column status text not null default 'active'
    check (status in ('active','archived'));

-- ---------------------------------------------------------------------------
-- 3. matters intake columns (updated_at trigger already exists from Slice 1)
-- ---------------------------------------------------------------------------
alter table matters add column title text not null default '';
alter table matters add column jurisdiction_snapshot jsonb not null default '{}'::jsonb;
alter table matters add column completeness_pct integer not null default 0
  check (completeness_pct between 0 and 100);
alter table matters add column submitted_at timestamptz;
alter table matters add column current_step text;

-- ---------------------------------------------------------------------------
-- 4. New tables
-- persons is created before the household_members ALTER below, which adds
-- a person_id FK referencing it.
-- ---------------------------------------------------------------------------

create table persons (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade,
  full_name text not null,
  date_of_birth date,
  email text,
  kind text check (kind in ('adult','minor')),
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table relationships (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade,
  from_person_id uuid not null references persons(id),
  to_person_id uuid not null references persons(id),
  relationship_type text not null,
  check (from_person_id != to_person_id),
  created_at timestamptz not null default now()
);

-- email is created as text, then promoted to citext where the extension is
-- available (see §0). The promotion is a no-op locally.
create table invitations (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  household_id uuid references households(id) on delete set null,
  email text not null,
  role text not null check (role in (
    'client_primary','client_collaborator',
    'firm_staff','firm_attorney','firm_admin','firm_auditor'
  )),
  token_hash text not null unique,
  status text not null default 'pending'
    check (status in ('pending','accepted','revoked','expired')),
  expires_at timestamptz not null,
  invited_by uuid not null references profiles(id),
  accepted_at timestamptz,
  accepted_user_id uuid references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $citext_promote$
begin
  if exists (select 1 from pg_extension where extname = 'citext') then
    alter table invitations alter column email type citext;
  end if;
end
$citext_promote$;

-- Autosave answers: composite PK, no surrogate id. updated_at is bumped by
-- trigger on every answer write so clients can show "saved at".
create table intake_answers (
  matter_id uuid not null references matters(id) on delete cascade,
  question_key text not null,
  answer jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (matter_id, question_key)
);

create table planning_decisions (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id) on delete cascade,
  decision_key text not null,
  value jsonb not null,
  decided_by uuid references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (matter_id, decision_key)
);

create table nominations (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id) on delete cascade,
  nomination_type text not null check (nomination_type in (
    'guardian','trustee','executor','financial_agent','healthcare_agent'
  )),
  person_id uuid references persons(id),
  priority integer not null default 1,
  notes text,
  status text not null default 'proposed'
    check (status in ('proposed','confirmed','withdrawn')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table escalations (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id) on delete cascade,
  rule_id text not null,
  severity text not null check (severity in ('info','review','blocking')),
  outcome text,
  facts_snapshot jsonb,
  status text not null default 'open'
    check (status in ('open','resolved','dismissed')),
  resolved_by uuid references profiles(id),
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 5. household_members: person linkage + inviter + partial unique indexes
-- Verified: neither supabase/seeds/01_jurisdictions.sql nor
-- supabase/seeds/02_live_rls_fixtures.sql inserts household_members rows, and
-- the pytest fixtures insert a single (household, user) pair — no duplicates
-- can violate the new partial unique indexes.
-- ---------------------------------------------------------------------------
alter table household_members add column person_id uuid references persons(id);
alter table household_members add column invited_by uuid references profiles(id);

create unique index uq_household_members_household_user
  on household_members (household_id, user_id)
  where user_id is not null;

create unique index uq_household_members_household_person
  on household_members (household_id, person_id)
  where person_id is not null;

-- ---------------------------------------------------------------------------
-- 6. Jurisdiction pack manifests + real bundle hashes
-- manifest jsonb is converted from the YAML files at
--   content-packs/us-va/v0/manifest.yaml
--   content-packs/us-xz-synthetic/v1/manifest.yaml
-- source_bundle_hash is the sha256 hex digest over the concatenation of every
-- file's bytes under each pack directory, ordered by relative POSIX path,
-- with no separators:
--   us-va v0:         4667574458e5de9ac67e06ef0827afec7dc4c975f37d6eab73d5b3e830e37589
--   us-xz-synthetic v1: 6c2df4a1e614ed5b6dd874c5234c7ee0ff380f89771afc47e4f1f01ef4a53198
-- (On a fresh database the pack rows do not exist yet when migrations run —
-- the seeds insert them — so these UPDATEs are no-ops there and the seed
-- file carries the same manifest/hash. On an existing database they bring
-- the rows up to date.)
-- ---------------------------------------------------------------------------
update jurisdiction_packs
set manifest = '{
  "pack": {
    "id": "us-va",
    "country_code": "US",
    "subdivision_code": "VA",
    "display_name": "Virginia",
    "semantic_version": "0.1.0",
    "status": "internal_testing",
    "effective_from": "2026-09-26"
  },
  "governance": {
    "legal_content_owner_type": "attorney_or_firm",
    "review_status": "pending",
    "approved_by": null
  },
  "capabilities": {
    "intake": true,
    "education": true,
    "eligibility_triage": true,
    "attorney_review": true,
    "document_assembly": false,
    "execution_guidance": "attorney_pending",
    "funding_guidance": true
  },
  "requirements": {
    "minimum_platform_version": "1.0.0",
    "required_question_set_versions": {
      "household": "1.0.0",
      "guardians": "1.0.0",
      "assets": "1.0.0"
    }
  },
  "sources": [
    {
      "source_id": "va-code-will-execution",
      "authority_type": "statute",
      "citation": "Va. Code § 64.2-403",
      "source_url": "https://law.lis.virginia.gov/vacode/title64.2/chapter2/section64.2-403/",
      "retrieved_on": "2026-09-26"
    },
    {
      "source_id": "va-code-advance-directive",
      "authority_type": "statute",
      "citation": "Va. Code § 54.1-2983 and § 54.1-2984",
      "source_url": "https://law.lis.virginia.gov/vacode/title54.1/chapter29/section54.1-2984/",
      "retrieved_on": "2026-09-26"
    }
  ]
}'::jsonb,
    source_bundle_hash = '4667574458e5de9ac67e06ef0827afec7dc4c975f37d6eab73d5b3e830e37589'
where id = '22222222-2222-2222-2222-222222222222';

update jurisdiction_packs
set manifest = '{
  "pack": {
    "id": "us-xz-synthetic",
    "country_code": "US",
    "subdivision_code": "XZ",
    "display_name": "Xanadu (synthetic test jurisdiction)",
    "semantic_version": "1.0.0",
    "status": "internal_testing",
    "effective_from": "2026-09-26"
  },
  "governance": {
    "legal_content_owner_type": "attorney_or_firm",
    "review_status": "pending",
    "approved_by": null
  },
  "capabilities": {
    "intake": true,
    "education": true,
    "eligibility_triage": true,
    "attorney_review": true,
    "document_assembly": false,
    "execution_guidance": "attorney_pending",
    "funding_guidance": true
  },
  "requirements": {
    "minimum_platform_version": "1.0.0",
    "required_question_set_versions": {
      "household": "1.0.0"
    }
  },
  "sources": [
    {
      "source_id": "xz-synthetic-statute",
      "authority_type": "other",
      "citation": "XZ Synthetic Code § 000.000 (test fixture, not law)",
      "source_url": null,
      "retrieved_on": "2026-09-26"
    }
  ]
}'::jsonb,
    source_bundle_hash = '6c2df4a1e614ed5b6dd874c5234c7ee0ff380f89771afc47e4f1f01ef4a53198'
where id = '44444444-4444-4444-4444-444444444444';

-- ---------------------------------------------------------------------------
-- 7. Indexes (FK lookups + RLS/policy subquery paths)
-- ---------------------------------------------------------------------------
create index idx_persons_household_id          on persons (household_id);
create index idx_relationships_household_id   on relationships (household_id);
create index idx_relationships_from_person_id on relationships (from_person_id);
create index idx_relationships_to_person_id   on relationships (to_person_id);
create index idx_invitations_firm_id           on invitations (firm_id);
create index idx_invitations_household_id     on invitations (household_id);
create index idx_invitations_email             on invitations (email);
-- intake_answers PK (matter_id, question_key) already covers matter_id lookups
create index idx_planning_decisions_matter_id  on planning_decisions (matter_id);
create index idx_nominations_matter_id         on nominations (matter_id);
create index idx_nominations_person_id         on nominations (person_id);
create index idx_escalations_matter_id         on escalations (matter_id);
create index idx_escalations_matter_status     on escalations (matter_id, status);
create index idx_matters_firm_id_status        on matters (firm_id, status);
create index idx_household_members_person_id   on household_members (person_id);

-- ---------------------------------------------------------------------------
-- 8. Row Level Security
-- Defense in depth: the API connects with a privileged role that bypasses
-- RLS and enforces app/core/authz.py server-side. Policies below reuse the
-- Slice 1 security-definer helpers so household <-> household_members joins
-- never recurse.
-- ---------------------------------------------------------------------------
alter table persons             enable row level security;
alter table relationships       enable row level security;
alter table invitations         enable row level security;
alter table intake_answers      enable row level security;
alter table planning_decisions  enable row level security;
alter table nominations         enable row level security;
alter table escalations         enable row level security;

-- ---------------------------------------------------------------------------
-- 8a. persons / relationships — household-scoped.
-- Read: active members of the row's firm OR active members of the household.
-- Writes: firm members only.
-- ---------------------------------------------------------------------------
create policy "persons select for firm or household members"
on persons for select
using (
  public.is_firm_member(public.household_firm_id(persons.household_id))
  or public.is_household_member(persons.household_id)
);

create policy "persons insert by firm members"
on persons for insert
with check (public.is_firm_member(public.household_firm_id(persons.household_id)));

create policy "persons update by firm members"
on persons for update
using (public.is_firm_member(public.household_firm_id(persons.household_id)))
with check (public.is_firm_member(public.household_firm_id(persons.household_id)));

create policy "persons delete by firm members"
on persons for delete
using (public.is_firm_member(public.household_firm_id(persons.household_id)));

create policy "relationships select for firm or household members"
on relationships for select
using (
  public.is_firm_member(public.household_firm_id(relationships.household_id))
  or public.is_household_member(relationships.household_id)
);

create policy "relationships insert by firm members"
on relationships for insert
with check (public.is_firm_member(public.household_firm_id(relationships.household_id)));

create policy "relationships update by firm members"
on relationships for update
using (public.is_firm_member(public.household_firm_id(relationships.household_id)))
with check (public.is_firm_member(public.household_firm_id(relationships.household_id)));

create policy "relationships delete by firm members"
on relationships for delete
using (public.is_firm_member(public.household_firm_id(relationships.household_id)));

-- ---------------------------------------------------------------------------
-- 8b. intake_answers / planning_decisions — matter-scoped autosave tables.
-- Read: active members of the matter's firm OR active members of the
-- matter's household. Insert/update: same two paths (client autosave).
-- No DELETE policy: rows are retired via update-to-null semantics in the API.
-- ---------------------------------------------------------------------------
create policy "intake_answers select for firm or household members"
on intake_answers for select
using (
  exists (
    select 1 from matters m
    where m.id = intake_answers.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "intake_answers insert for firm or household members"
on intake_answers for insert
with check (
  exists (
    select 1 from matters m
    where m.id = intake_answers.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "intake_answers update for firm or household members"
on intake_answers for update
using (
  exists (
    select 1 from matters m
    where m.id = intake_answers.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = intake_answers.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "planning_decisions select for firm or household members"
on planning_decisions for select
using (
  exists (
    select 1 from matters m
    where m.id = planning_decisions.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "planning_decisions insert for firm or household members"
on planning_decisions for insert
with check (
  exists (
    select 1 from matters m
    where m.id = planning_decisions.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "planning_decisions update for firm or household members"
on planning_decisions for update
using (
  exists (
    select 1 from matters m
    where m.id = planning_decisions.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = planning_decisions.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

-- ---------------------------------------------------------------------------
-- 8c. nominations / escalations — matter-scoped, firm-write.
-- The household is resolved through the parent matter (these tables carry
-- matter_id, not household_id). Read: firm members OR household members.
-- Writes: firm members only.
-- ---------------------------------------------------------------------------
create policy "nominations select for firm or household members"
on nominations for select
using (
  exists (
    select 1 from matters m
    where m.id = nominations.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "nominations insert by firm members"
on nominations for insert
with check (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = nominations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

create policy "nominations update by firm members"
on nominations for update
using (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = nominations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
)
with check (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = nominations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

create policy "nominations delete by firm members"
on nominations for delete
using (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = nominations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

create policy "escalations select for firm or household members"
on escalations for select
using (
  exists (
    select 1 from matters m
    where m.id = escalations.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "escalations insert by firm members"
on escalations for insert
with check (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = escalations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

create policy "escalations update by firm members"
on escalations for update
using (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = escalations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
)
with check (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = escalations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

create policy "escalations delete by firm members"
on escalations for delete
using (
  exists (
    select 1 from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = escalations.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

-- ---------------------------------------------------------------------------
-- 8d. invitations
-- Firm path: active firm_admins and attorneys of the invitation's firm get
-- SELECT/INSERT/UPDATE. (The helpers only expose is_firm_member /
-- is_firm_admin, so the attorney role check is an explicit EXISTS on
-- firm_memberships — the same non-recursive pattern Slice 1 uses on matters.)
-- Client path: active members of the invitation's household may SELECT their
-- own household's invitations. No DELETE policy: invitations are revoked via
-- status. Anonymous: no access (deny by default).
-- ---------------------------------------------------------------------------
create policy "invitations select for firm admins attorneys or household clients"
on invitations for select
using (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = invitations.firm_id
      and fm.user_id = auth.uid()
      and fm.role in ('firm_admin','attorney')
      and fm.status = 'active'
  )
  or (
    invitations.household_id is not null
    and public.is_household_member(invitations.household_id)
  )
);

create policy "invitations insert by firm admins and attorneys"
on invitations for insert
with check (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = invitations.firm_id
      and fm.user_id = auth.uid()
      and fm.role in ('firm_admin','attorney')
      and fm.status = 'active'
  )
);

create policy "invitations update by firm admins and attorneys"
on invitations for update
using (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = invitations.firm_id
      and fm.user_id = auth.uid()
      and fm.role in ('firm_admin','attorney')
      and fm.status = 'active'
  )
)
with check (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = invitations.firm_id
      and fm.user_id = auth.uid()
      and fm.role in ('firm_admin','attorney')
      and fm.status = 'active'
  )
);

-- ---------------------------------------------------------------------------
-- 8e. matter_status_history — unchanged. The Slice 1 policies (select with
-- parent-matter access, firm-side insert, no update/delete) already cover the
-- intake states; Slice 2 adds no policies here.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 9. updated_at triggers (tables that carry the column)
-- ---------------------------------------------------------------------------
create trigger trg_persons_set_updated_at
before update on persons
for each row execute function public.set_updated_at();

create trigger trg_invitations_set_updated_at
before update on invitations
for each row execute function public.set_updated_at();

create trigger trg_intake_answers_set_updated_at
before update on intake_answers
for each row execute function public.set_updated_at();

create trigger trg_planning_decisions_set_updated_at
before update on planning_decisions
for each row execute function public.set_updated_at();

create trigger trg_nominations_set_updated_at
before update on nominations
for each row execute function public.set_updated_at();

create trigger trg_escalations_set_updated_at
before update on escalations
for each row execute function public.set_updated_at();
