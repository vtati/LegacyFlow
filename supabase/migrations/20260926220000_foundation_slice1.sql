-- ============================================================================
-- LegacyFlow — Foundation Slice 1
-- Migration: 20260926220000_foundation_slice1
--
-- Tenancy + identity + jurisdiction-pack foundation tables with Row Level
-- Security. Faithful to §7.2 DDL of the product build artifact.
--
-- Conventions (§7.1):
--   * UUID primary keys, gen_random_uuid() (built-in since PG13; the pgcrypto
--     extension below is kept for Supabase parity — digest()/crypt() helpers).
--   * updated_at maintained by trigger on tables that carry the column.
--   * firm_id on all firm-scoped resources.
--   * audit_events is append-only (no write policies for client roles).
--
-- Local test note: this migration references auth.users (Supabase Auth).
-- The pytest harness (supabase/tests/conftest.py) creates a stub auth schema
-- + auth.users table + auth.uid() shim before applying this file. On a real
-- Supabase project, auth.users / auth.uid() already exist.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0. Extensions
-- ---------------------------------------------------------------------------
-- pgcrypto ships with Supabase. The local pgserver bundle used for tests does
-- not include extension files, so guard the install: keep the required
-- statement, but don't fail where the files are absent.
do $pgcrypto_guard$
begin
  create extension if not exists pgcrypto;
exception when sqlstate '0A000' then
  raise notice 'pgcrypto extension files unavailable in this environment; skipping (gen_random_uuid() is core since PG13).';
end
$pgcrypto_guard$;

-- ---------------------------------------------------------------------------
-- 1. updated_at trigger helper (§7.1: use database triggers for updated_at)
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Core tables — §7.2 DDL, Slice 1 scope (10 tables)
-- ---------------------------------------------------------------------------

-- Identity and tenancy
create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  first_name text,
  last_name text,
  email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table firms (
  id uuid primary key default gen_random_uuid(),
  legal_name text not null,
  display_name text not null,
  status text not null check (status in ('active','suspended','trial')),
  created_at timestamptz not null default now()
);

create table firm_memberships (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  user_id uuid not null references profiles(id) on delete cascade,
  role text not null check (role in ('firm_admin','attorney','staff','auditor')),
  status text not null check (status in ('active','invited','disabled')),
  created_at timestamptz not null default now(),
  unique (firm_id, user_id)
);

-- Jurisdiction and content packs
create table jurisdictions (
  id uuid primary key default gen_random_uuid(),
  country_code text not null,
  subdivision_code text not null,
  display_name text not null,
  jurisdiction_type text not null check (jurisdiction_type in ('country','us_state','district','territory')),
  is_enabled boolean not null default false,
  created_at timestamptz not null default now(),
  unique (country_code, subdivision_code)
);

create table jurisdiction_packs (
  id uuid primary key default gen_random_uuid(),
  jurisdiction_id uuid not null references jurisdictions(id),
  version text not null,
  status text not null check (status in ('draft','internal_testing','attorney_review','approved','retired')),
  effective_from date not null,
  effective_to date,
  manifest jsonb not null,
  source_bundle_hash text not null,
  approved_by_user_id uuid references profiles(id),
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  unique(jurisdiction_id, version)
);

-- Households and matters
create table households (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id),
  display_name text not null,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now()
);

create table household_members (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade,
  user_id uuid references profiles(id),
  relationship_role text not null check (relationship_role in ('primary_client','collaborator','dependent','trusted_contact')),
  access_status text not null check (access_status in ('active','invited','revoked','none')),
  created_at timestamptz not null default now()
);

create table matters (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id),
  household_id uuid not null references households(id),
  primary_jurisdiction_id uuid not null references jurisdictions(id),
  jurisdiction_pack_id uuid not null references jurisdiction_packs(id),
  plan_type text not null,
  status text not null,
  status_version integer not null default 1,
  jurisdiction_confirmation_status text not null default 'pending',
  assigned_attorney_id uuid references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table matter_status_history (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id) on delete cascade,
  from_status text,
  to_status text not null,
  reason_code text,
  actor_user_id uuid references profiles(id),
  created_at timestamptz not null default now()
);

-- Append-only audit trail (§7.1: payload redaction happens at the API layer)
create table audit_events (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid references firms(id),
  matter_id uuid references matters(id),
  actor_user_id uuid references profiles(id),
  actor_type text not null,
  action text not null,
  resource_type text not null,
  resource_id uuid,
  correlation_id uuid,
  before_summary jsonb,
  after_summary jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 3. updated_at triggers (only tables that carry the column: profiles, matters)
-- ---------------------------------------------------------------------------
create trigger trg_profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create trigger trg_matters_set_updated_at
before update on public.matters
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 4. Helpful indexes (FK lookups + RLS policy subqueries)
-- ---------------------------------------------------------------------------
create index idx_firm_memberships_user_id      on firm_memberships (user_id);
create index idx_firm_memberships_firm_id      on firm_memberships (firm_id);
create index idx_matters_firm_id               on matters (firm_id);
create index idx_matters_household_id          on matters (household_id);
create index idx_household_members_household   on household_members (household_id);
create index idx_household_members_user_id     on household_members (user_id);
create index idx_matter_status_history_matter  on matter_status_history (matter_id);
create index idx_audit_events_firm_id          on audit_events (firm_id);
create index idx_audit_events_matter_id        on audit_events (matter_id);
create index idx_jurisdiction_packs_jurisdiction on jurisdiction_packs (jurisdiction_id);

-- ---------------------------------------------------------------------------
-- 5. Row Level Security
-- ---------------------------------------------------------------------------
alter table profiles             enable row level security;
alter table firms                enable row level security;
alter table firm_memberships     enable row level security;
alter table jurisdictions        enable row level security;
alter table jurisdiction_packs   enable row level security;
alter table households           enable row level security;
alter table household_members    enable row level security;
alter table matters              enable row level security;
alter table matter_status_history enable row level security;
alter table audit_events         enable row level security;

-- ---------------------------------------------------------------------------
-- 5a. Security-definer membership helpers
--
-- §7.3: "Use security-definer helper functions carefully, minimally, and
-- test them." These exist for one reason: policies on firm_memberships (and
-- policies that join households <-> household_members) cannot query those
-- tables with plain EXISTS subqueries without hitting RLS policy recursion.
-- The helpers run as the table owner, bypass RLS, and only ever answer
-- questions about the *caller's own* membership via auth.uid().
-- ---------------------------------------------------------------------------
create or replace function public.is_firm_member(p_firm_id uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1
    from public.firm_memberships fm
    where fm.firm_id = p_firm_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  );
$$;

create or replace function public.is_firm_admin(p_firm_id uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1
    from public.firm_memberships fm
    where fm.firm_id = p_firm_id
      and fm.user_id = auth.uid()
      and fm.role = 'firm_admin'
      and fm.status = 'active'
  );
$$;

-- Firm that owns a household (definer lookup; avoids households <->
-- household_members policy recursion).
create or replace function public.household_firm_id(p_household_id uuid)
returns uuid
language sql
security definer
set search_path = public
stable
as $$
  select h.firm_id from public.households h where h.id = p_household_id;
$$;

-- Active household-membership check for the caller.
create or replace function public.is_household_member(p_household_id uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1
    from public.household_members hm
    where hm.household_id = p_household_id
      and hm.user_id = auth.uid()
      and hm.access_status = 'active'
  );
$$;

-- ---------------------------------------------------------------------------
-- 5b. Policies — profiles
-- A user can read/update their own profile; firm members can read the
-- profiles of people in their firms (needed for attorney/staff directories).
-- No deletes for client roles.
-- ---------------------------------------------------------------------------
create policy "profiles select self or firm colleagues"
on profiles for select
using (
  auth.uid() = id
  or exists (
    select 1
    from firm_memberships mine
    join firm_memberships theirs on theirs.firm_id = mine.firm_id
    where mine.user_id = auth.uid()
      and mine.status = 'active'
      and theirs.user_id = profiles.id
      and theirs.status = 'active'
  )
);

create policy "profiles insert own"
on profiles for insert
with check (auth.uid() = id);

create policy "profiles update own"
on profiles for update
using (auth.uid() = id)
with check (auth.uid() = id);

-- ---------------------------------------------------------------------------
-- 5b. Policies — firms
-- Visible to active members of the firm. No client-role writes (firm
-- provisioning is a service-role operation).
-- ---------------------------------------------------------------------------
create policy "firms select for members"
on firms for select
using (public.is_firm_member(firms.id));

-- ---------------------------------------------------------------------------
-- 5b. Policies — firm_memberships
-- Members can list their own firm's roster; only firm_admins may
-- invite/modify/remove memberships.
-- ---------------------------------------------------------------------------
create policy "firm_memberships select for members"
on firm_memberships for select
using (public.is_firm_member(firm_memberships.firm_id));

create policy "firm_memberships insert by firm_admin"
on firm_memberships for insert
with check (public.is_firm_admin(firm_memberships.firm_id));

create policy "firm_memberships update by firm_admin"
on firm_memberships for update
using (public.is_firm_admin(firm_memberships.firm_id))
with check (public.is_firm_admin(firm_memberships.firm_id));

create policy "firm_memberships delete by firm_admin"
on firm_memberships for delete
using (public.is_firm_admin(firm_memberships.firm_id));

-- ---------------------------------------------------------------------------
-- 5b. Policies — jurisdictions / jurisdiction_packs (reference data)
-- Readable by any authenticated user; packs are curated by the content
-- pipeline via service role — no client-role writes (deny by default).
-- ---------------------------------------------------------------------------
create policy "jurisdictions select for authenticated"
on jurisdictions for select
using (auth.uid() is not null);

create policy "jurisdiction_packs select for authenticated"
on jurisdiction_packs for select
using (auth.uid() is not null);

-- ---------------------------------------------------------------------------
-- 5b. Policies — households
-- Firm members of the household's firm OR active household members may
-- read/update. Creation is firm-side only (household-membership path is
-- vacuous on insert since the row does not exist yet).
-- ---------------------------------------------------------------------------
create policy "households select for firm or household members"
on households for select
using (
  public.is_firm_member(households.firm_id)
  or public.is_household_member(households.id)
);

create policy "households insert by firm members"
on households for insert
with check (public.is_firm_member(households.firm_id));

create policy "households update for firm or household members"
on households for update
using (
  public.is_firm_member(households.firm_id)
  or public.is_household_member(households.id)
)
with check (
  public.is_firm_member(households.firm_id)
  or public.is_household_member(households.id)
);

-- ---------------------------------------------------------------------------
-- 5b. Policies — household_members
-- Readable by firm members of the household's firm or active members of the
-- household itself; writes restricted to firm members.
-- ---------------------------------------------------------------------------
create policy "household_members select for firm or household members"
on household_members for select
using (
  public.is_firm_member(public.household_firm_id(household_members.household_id))
  or public.is_household_member(household_members.household_id)
);

create policy "household_members insert by firm members"
on household_members for insert
with check (public.is_firm_member(public.household_firm_id(household_members.household_id)));

create policy "household_members update by firm members"
on household_members for update
using (public.is_firm_member(public.household_firm_id(household_members.household_id)))
with check (public.is_firm_member(public.household_firm_id(household_members.household_id)));

create policy "household_members delete by firm members"
on household_members for delete
using (public.is_firm_member(public.household_firm_id(household_members.household_id)));

-- ---------------------------------------------------------------------------
-- 5b. Policies — matters (§7.3 example pattern, verbatim for select)
-- Firm members of the matter's firm OR active members of the matter's
-- household may read. Writes (insert/update) are firm-side; deletes are
-- service-role only (deny by default).
-- ---------------------------------------------------------------------------
create policy "firm members can view firm matters"
on matters for select
using (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = matters.firm_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
  or exists (
    select 1
    from household_members hm
    where hm.household_id = matters.household_id
      and hm.user_id = auth.uid()
      and hm.access_status = 'active'
  )
);

create policy "firm members can insert firm matters"
on matters for insert
with check (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = matters.firm_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

create policy "firm members can update firm matters"
on matters for update
using (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = matters.firm_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
)
with check (
  exists (
    select 1 from firm_memberships fm
    where fm.firm_id = matters.firm_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

-- ---------------------------------------------------------------------------
-- 5b. Policies — matter_status_history
-- Readable when the caller can read the parent matter (§7.3 access rule);
-- appends are firm-side only. Rows are immutable otherwise.
-- ---------------------------------------------------------------------------
create policy "matter_status_history select with parent matter access"
on matter_status_history for select
using (
  exists (
    select 1 from matters m
    where m.id = matter_status_history.matter_id
      and (
        exists (
          select 1 from firm_memberships fm
          where fm.firm_id = m.firm_id
            and fm.user_id = auth.uid()
            and fm.status = 'active'
        )
        or exists (
          select 1 from household_members hm
          where hm.household_id = m.household_id
            and hm.user_id = auth.uid()
            and hm.access_status = 'active'
        )
      )
  )
);

create policy "matter_status_history insert by firm members"
on matter_status_history for insert
with check (
  exists (
    select 1
    from matters m
    join firm_memberships fm on fm.firm_id = m.firm_id
    where m.id = matter_status_history.matter_id
      and fm.user_id = auth.uid()
      and fm.status = 'active'
  )
);

-- ---------------------------------------------------------------------------
-- 5b. Policies — audit_events (append-only)
-- Firm members may read their own firm's events. No client-role
-- insert/update/delete: events are written by the API service role only
-- (deny by default on enabled RLS).
-- ---------------------------------------------------------------------------
create policy "audit_events select for firm members"
on audit_events for select
using (
  audit_events.firm_id is not null
  and public.is_firm_member(audit_events.firm_id)
);
