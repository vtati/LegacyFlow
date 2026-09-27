-- ============================================================================
-- LegacyFlow — Reminder delivery wiring & privacy surface
-- Migration: 20260927050000_reminders_privacy
--
-- Adds the two tables the Slice 4 reminder audit identified as missing:
--   * notification_preferences — per-user email/reminder opt-out state
--     consumed by the worker's send_due_reminders actor.
--   * data_deletion_requests — user-initiated data deletion requests
--     (privacy skeleton: intake + firm-admin triage; no auto-deletion).
--
-- Row Level Security reuses the Slice 1 security-definer helper pattern
-- (public.is_firm_member, public.is_firm_admin) and adds two narrowly
-- scoped helpers below (§7.3: helpers exist only to avoid RLS policy
-- recursion on firm_memberships; they only ever answer questions about
-- the *caller's own* membership via auth.uid()).
--
-- Conventions (§7.1, carried over from Slice 1–4):
--   * UUID primary keys, gen_random_uuid() (core since PG13).
--   * updated_at maintained by the public.set_updated_at() trigger.
--   * Firm tenancy enforced via RLS; the API service role bypasses RLS and
--     enforces authorization server-side (app/core/authz.py, app/core/audit.py).
--
-- Local test note: pgcrypto/gen_random_uuid() is core since PG13, so no
-- extension guard is needed (unlike the citext guard in Slice 4).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. New security-definer helpers (§7.3 pattern)
-- ---------------------------------------------------------------------------

-- The caller's active role in a firm, or NULL when the caller is not an
-- active member. Definer lookup avoids RLS recursion on firm_memberships.
create or replace function public.caller_firm_role(p_firm_id uuid)
returns text
language sql
security definer
set search_path = public
stable
as $$
  select fm.role
  from public.firm_memberships fm
  where fm.firm_id = p_firm_id
    and fm.user_id = auth.uid()
    and fm.status = 'active'
  limit 1;
$$;

-- Whether the caller is an active firm_admin of a firm that p_user_id
-- actively belongs to — either via a direct firm membership or as an active
-- member of one of the firm's households (household-only clients have no
-- firm_memberships row). Lets firm admins read notification preferences of
-- their own firm's users (never household clients of another firm).
create or replace function public.is_firm_admin_of_user(p_user_id uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1
    from public.firm_memberships fm_admin
    join public.firm_memberships fm_user
      on fm_user.firm_id = fm_admin.firm_id
    where fm_admin.user_id = auth.uid()
      and fm_admin.role = 'firm_admin'
      and fm_admin.status = 'active'
      and fm_user.user_id = p_user_id
      and fm_user.status = 'active'
  ) or exists (
    select 1
    from public.firm_memberships fm_admin
    join public.households h
      on h.firm_id = fm_admin.firm_id
    join public.household_members hm
      on hm.household_id = h.id
     and hm.user_id = p_user_id
     and hm.access_status = 'active'
    where fm_admin.user_id = auth.uid()
      and fm_admin.role = 'firm_admin'
      and fm_admin.status = 'active'
  );
$$;

-- ---------------------------------------------------------------------------
-- 2. notification_preferences
-- ---------------------------------------------------------------------------

create table notification_preferences (
  user_id uuid primary key references profiles(id) on delete cascade,
  email_enabled boolean not null default true,
  reminder_enabled boolean not null default true,
  unsubscribed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_notification_preferences_set_updated_at
before update on notification_preferences
for each row execute function public.set_updated_at();

alter table notification_preferences enable row level security;

-- Users read/update their own preferences row.
create policy "notification_preferences select own"
on notification_preferences for select
using (user_id = auth.uid());

create policy "notification_preferences insert own"
on notification_preferences for insert
with check (user_id = auth.uid());

create policy "notification_preferences update own"
on notification_preferences for update
using (user_id = auth.uid())
with check (user_id = auth.uid());

-- Firm admins may read (never write) the preferences of their firm's users,
-- e.g. to confirm an opt-out during a support interaction.
create policy "notification_preferences select for firm admins"
on notification_preferences for select
using (public.is_firm_admin_of_user(user_id));

-- ---------------------------------------------------------------------------
-- 3. data_deletion_requests
--
-- Skeleton only: intake + triage statuses. No automatic deletion is wired;
-- completing a request is a manual, audited firm-admin action.
-- ---------------------------------------------------------------------------

create table data_deletion_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id),
  firm_id uuid references firms(id),
  status text not null default 'open'
    check (status in ('open', 'in_review', 'completed', 'rejected')),
  requested_at timestamptz not null default now(),
  processed_at timestamptz,
  notes text,
  created_at timestamptz not null default now()
);

create index idx_deletion_requests_user_id   on data_deletion_requests (user_id);
create index idx_deletion_requests_firm_id   on data_deletion_requests (firm_id);
create index idx_deletion_requests_status    on data_deletion_requests (status);

alter table data_deletion_requests enable row level security;

-- Users see and file their own requests.
create policy "deletion_requests select own"
on data_deletion_requests for select
using (user_id = auth.uid());

create policy "deletion_requests insert own"
on data_deletion_requests for insert
with check (user_id = auth.uid());

-- Firm admins and attorneys read their firm's request queue.
create policy "deletion_requests select for firm legal staff"
on data_deletion_requests for select
using (public.caller_firm_role(firm_id) in ('firm_admin', 'attorney'));

-- Only firm admins move requests through triage (narrowed by the guard
-- trigger below to status/processed_at/notes).
create policy "deletion_requests update by firm admins"
on data_deletion_requests for update
using (public.caller_firm_role(firm_id) = 'firm_admin')
with check (public.caller_firm_role(firm_id) = 'firm_admin');

-- ---------------------------------------------------------------------------
-- 4. Guard: firm-admin updates may only touch triage fields.
--
-- Mirrors the Slice 4 household-update guards: the service role / owner
-- (auth.uid() null) is unrestricted; firm admins may only change status,
-- processed_at, and notes — never reassign user_id/firm_id or rewrite
-- requested_at/created_at history.
-- ---------------------------------------------------------------------------
create or replace function public.guard_deletion_request_update()
returns trigger
language plpgsql
as $$
begin
  -- service role / owner path: unrestricted
  if auth.uid() is null then
    return NEW;
  end if;
  if (NEW.user_id, NEW.firm_id, NEW.requested_at, NEW.created_at)
     is distinct from
     (OLD.user_id, OLD.firm_id, OLD.requested_at, OLD.created_at) then
    raise exception 'deletion requests: only status, processed_at and notes may be updated';
  end if;
  return NEW;
end;
$$;

create trigger trg_deletion_requests_update_guard
before update on data_deletion_requests
for each row execute function public.guard_deletion_request_update();
