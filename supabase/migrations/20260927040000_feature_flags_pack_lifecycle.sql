-- ============================================================================
-- LegacyFlow — Feature flags + legal-content lifecycle
-- Migration: 20260927040000_feature_flags_pack_lifecycle
--
-- 1. ``feature_flags``: scoped kill-switches for product capabilities
--    (``document_assembly``, ``ai_assistance``, ``funding_reminders``, ...).
--    Resolution precedence (app/domains/feature_flags/service.py):
--      explicit LF_FLAG_<KEY> env override
--        > firm scope (scope='firm', scope_ref=<firm id>)
--        > jurisdiction scope (scope='jurisdiction', scope_ref=<pack id>)
--        > environment scope (scope='environment', scope_ref='')
--        > built-in default (document_assembly fails closed).
-- 2. ``document_templates`` lifecycle columns: status
--    (draft/under_review/approved/retired) + approval attribution.
--
-- Conventions (§7.1, carried over from Slices 1–4):
--   * UUID primary keys, gen_random_uuid() (core since PG13).
--   * updated_at maintained by trigger (public.set_updated_at()).
--   * RLS enabled; the API service role bypasses RLS and enforces
--     authorization server-side (app/core/authz.py, app/core/audit.py).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. feature_flags table
-- ---------------------------------------------------------------------------

create table feature_flags (
  id uuid primary key default gen_random_uuid(),
  key text not null,
  scope text not null check (scope in ('environment','firm','jurisdiction')),
  scope_ref text not null default '',
  is_enabled boolean not null,
  created_by_user_id uuid references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (key, scope, scope_ref)
);

create index idx_feature_flags_lookup on feature_flags (key, scope, scope_ref);

create trigger trg_feature_flags_set_updated_at
before update on feature_flags
for each row execute function public.set_updated_at();

alter table feature_flags enable row level security;

-- ---------------------------------------------------------------------------
-- 1a. RLS — feature_flags
--
-- Reads: firm admins may read every flag that can affect their firm:
--   * environment scope ('' ref): global flags affect every firm;
--   * jurisdiction scope (pack id ref): packs are a global legal-content
--     catalog any firm may use, so the flag is visible to all firm admins;
--   * firm scope: only flags whose scope_ref is the caller's own firm.
-- Writes: firm admins may insert/update only firm-scope flags for their own
-- firm (the only scope the admin API exposes). Environment/jurisdiction
-- scope writes are service-role only (deny by default here). Deletes are
-- deny by default; disabling is done by flipping ``is_enabled``.
-- ---------------------------------------------------------------------------
create policy "feature_flags select for firm admins"
on feature_flags for select
using (
  exists (
    select 1
    from firm_memberships fm
    where fm.user_id = auth.uid()
      and fm.role = 'firm_admin'
      and fm.status = 'active'
      and (
        feature_flags.scope in ('environment', 'jurisdiction')
        or (
          feature_flags.scope = 'firm'
          and feature_flags.scope_ref = fm.firm_id::text
        )
      )
  )
);

create policy "feature_flags insert by firm admins for own firm"
on feature_flags for insert
with check (
  feature_flags.scope = 'firm'
  and exists (
    select 1
    from firm_memberships fm
    where fm.user_id = auth.uid()
      and fm.role = 'firm_admin'
      and fm.status = 'active'
      and fm.firm_id::text = feature_flags.scope_ref
  )
);

create policy "feature_flags update by firm admins for own firm"
on feature_flags for update
using (
  feature_flags.scope = 'firm'
  and exists (
    select 1
    from firm_memberships fm
    where fm.user_id = auth.uid()
      and fm.role = 'firm_admin'
      and fm.status = 'active'
      and fm.firm_id::text = feature_flags.scope_ref
  )
)
with check (
  feature_flags.scope = 'firm'
  and exists (
    select 1
    from firm_memberships fm
    where fm.user_id = auth.uid()
      and fm.role = 'firm_admin'
      and fm.status = 'active'
      and fm.firm_id::text = feature_flags.scope_ref
  )
);

-- ---------------------------------------------------------------------------
-- 2. document_templates — legal-content lifecycle columns
--
-- ``status`` moves draft -> under_review -> approved, and any of those may
-- move to retired; approval attribution is recorded in
-- approved_by_user_id / approved_at. Existing rows backfill to 'draft'.
-- The Slice 4 RLS policies already cover the new columns (firm members
-- SELECT; firm_admin writes; clients never see templates).
-- ---------------------------------------------------------------------------
alter table document_templates
  add column status text not null default 'draft'
    check (status in ('draft','under_review','approved','retired')),
  add column approved_by_user_id uuid references profiles(id),
  add column approved_at timestamptz;
