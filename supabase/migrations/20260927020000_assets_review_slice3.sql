-- ============================================================================
-- LegacyFlow — Assets & Review Slice 3
-- Migration: 20260927020000_assets_review_slice3
--
-- Extends the Slice 2 household-intake domain
-- (20260927010000_household_intake_slice2) with the assets-and-review
-- domain: new tables assets, liabilities, asset_flags, approval_gates,
-- review_packets, comments, clarification_requests, notification_outbox —
-- all with Row Level Security reusing the Slice 1 security-definer helpers
-- (public.is_firm_member, public.is_firm_admin, public.is_household_member,
-- public.household_firm_id) and the public.set_updated_at() trigger helper.
--
-- Conventions (§7.1, carried over from Slice 1/2):
--   * UUID primary keys, gen_random_uuid() (core since PG13).
--   * updated_at maintained by trigger on tables that carry the column.
--   * firm tenancy enforced via RLS; the API service role bypasses RLS and
--     enforces authorization server-side (app/core/authz.py, app/core/audit.py).
--
-- Local test note: like Slice 1/2, this migration is validated against a
-- local pgserver instance whose bundled Postgres lacks contrib extension
-- files, so the citext install below is guarded the same way. Slice 3
-- tables do not use citext themselves; the guard only keeps the migration
-- self-contained when applied on a fresh database without Slices 1/2.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0. citext extension (guarded: absent from the local pgserver test bundle)
-- ---------------------------------------------------------------------------
do $citext_guard$
begin
  create extension if not exists citext;
exception when sqlstate '0A000' then
  raise notice 'citext extension files unavailable in this environment; skipping (Slice 3 tables do not depend on citext).';
end
$citext_guard$;

-- ---------------------------------------------------------------------------
-- 1. New tables
-- ---------------------------------------------------------------------------

create table assets (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  household_id uuid not null references households(id) on delete cascade,
  matter_id uuid references matters(id) on delete set null,
  asset_type text not null check (asset_type in (
    'bank_account','investment','retirement','real_estate',
    'business','insurance','vehicle','other'
  )),
  label text not null default '',
  estimated_value_cents bigint check (estimated_value_cents >= 0),
  ownership text,
  beneficiary_designation jsonb not null default '{}'::jsonb,
  notes text,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table liabilities (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  household_id uuid not null references households(id) on delete cascade,
  matter_id uuid references matters(id) on delete set null,
  liability_type text not null check (liability_type in (
    'mortgage','loan','credit_card','other'
  )),
  label text not null default '',
  balance_cents bigint check (balance_cents >= 0),
  monthly_payment_cents bigint check (monthly_payment_cents >= 0),
  notes text,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Deterministic flags written by the alignment engine.
create table asset_flags (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  asset_id uuid not null references assets(id) on delete cascade,
  flag_type text not null check (flag_type in (
    'unknown_beneficiary','non_household_beneficiary',
    'missing_contingent','info'
  )),
  severity text not null check (severity in ('info','review')),
  message text not null default '',
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table approval_gates (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  gate_type text not null check (gate_type in (
    'intake_approval','document_release'
  )),
  status text not null default 'pending' check (status in (
    'pending','approved','rejected'
  )),
  decided_by_user_id uuid references profiles(id),
  decided_at timestamptz,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Exactly one pending gate per (matter_id, gate_type).
create unique index uq_approval_gates_pending_gate
  on approval_gates (matter_id, gate_type)
  where status = 'pending';

-- Immutable once created: no UPDATE/DELETE client policies below; rows are
-- only ever appended (versioned snapshots).
create table review_packets (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  version integer not null check (version > 0),
  snapshot jsonb not null default '{}'::jsonb,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now(),
  unique (matter_id, version)
);

-- Append-only discussion: SELECT + INSERT only, never UPDATE or DELETE.
create table comments (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  author_user_id uuid not null references profiles(id),
  body text not null check (char_length(body) between 1 and 5000),
  created_at timestamptz not null default now()
);

create table clarification_requests (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  question text not null,
  status text not null default 'open' check (status in ('open','answered')),
  created_by_user_id uuid not null references profiles(id),
  answered_by_user_id uuid references profiles(id),
  answered_at timestamptz,
  response text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table notification_outbox (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid references matters(id) on delete set null,
  recipient_user_id uuid references profiles(id),
  recipient_email text,
  template_key text not null,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending' check (status in (
    'pending','sent','failed'
  )),
  attempts integer not null default 0 check (attempts >= 0),
  sent_at timestamptz,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 2. Guard: household clients may only touch response fields on their own
--    clarification requests.
--
-- The RLS policy below lets a household member UPDATE a clarification
-- request they created. This trigger narrows that path so only the answer
-- columns (status, response, answered_by_user_id, answered_at) can change —
-- question text, tenancy columns and authorship stay immutable for the
-- household path. Firm members (and the service role / owner, for whom
-- auth.uid() is null) are unrestricted.
-- ---------------------------------------------------------------------------
create or replace function public.guard_clarification_request_update()
returns trigger
language plpgsql
as $$
begin
  -- service role / owner path: unrestricted
  if auth.uid() is null then
    return NEW;
  end if;
  -- firm member path: unrestricted (RLS already limited the row to their firm)
  if public.is_firm_member(OLD.firm_id) then
    return NEW;
  end if;
  -- household path: only the response fields may change
  if (NEW.firm_id, NEW.matter_id, NEW.question, NEW.created_by_user_id)
     is distinct from
     (OLD.firm_id, OLD.matter_id, OLD.question, OLD.created_by_user_id) then
    raise exception 'household members may only update response fields on clarification requests';
  end if;
  -- the request may be answered but never reopened by the household path
  if OLD.status = 'answered' and NEW.status = 'open' then
    raise exception 'household members cannot reopen an answered clarification request';
  end if;
  return NEW;
end;
$$;

create trigger trg_clarification_requests_household_update_guard
before update on clarification_requests
for each row execute function public.guard_clarification_request_update();

-- ---------------------------------------------------------------------------
-- 3. Indexes (FK lookups + RLS/policy subquery paths)
-- ---------------------------------------------------------------------------
create index idx_assets_firm_id             on assets (firm_id);
create index idx_assets_household_id        on assets (household_id);
create index idx_assets_matter_id           on assets (matter_id);
create index idx_liabilities_firm_id        on liabilities (firm_id);
create index idx_liabilities_household_id   on liabilities (household_id);
create index idx_liabilities_matter_id      on liabilities (matter_id);
create index idx_asset_flags_firm_id        on asset_flags (firm_id);
create index idx_asset_flags_asset_id       on asset_flags (asset_id);
create index idx_approval_gates_firm_id     on approval_gates (firm_id);
create index idx_approval_gates_matter_id   on approval_gates (matter_id);
create index idx_review_packets_firm_id     on review_packets (firm_id);
create index idx_review_packets_matter_id   on review_packets (matter_id);
create index idx_comments_firm_id           on comments (firm_id);
create index idx_comments_matter_id         on comments (matter_id);
create index idx_clarification_requests_firm_id   on clarification_requests (firm_id);
create index idx_clarification_requests_matter_id on clarification_requests (matter_id);
create index idx_notification_outbox_firm_id      on notification_outbox (firm_id);
create index idx_notification_outbox_status       on notification_outbox (firm_id, status);

-- ---------------------------------------------------------------------------
-- 4. Row Level Security
-- Defense in depth: the API connects with a privileged role that bypasses
-- RLS and enforces app/core/authz.py server-side. Policies below reuse the
-- Slice 1 security-definer helpers so household <-> household_members joins
-- never recurse.
-- ---------------------------------------------------------------------------
alter table assets                enable row level security;
alter table liabilities           enable row level security;
alter table asset_flags           enable row level security;
alter table approval_gates        enable row level security;
alter table review_packets        enable row level security;
alter table comments              enable row level security;
alter table clarification_requests enable row level security;
alter table notification_outbox   enable row level security;

-- ---------------------------------------------------------------------------
-- 4a. assets / liabilities — firm full access; household members SELECT
--     only (their household's rows).
-- ---------------------------------------------------------------------------
create policy "assets select for firm or household members"
on assets for select
using (
  public.is_firm_member(assets.firm_id)
  or public.is_household_member(assets.household_id)
);

create policy "assets insert by firm members"
on assets for insert
with check (public.is_firm_member(assets.firm_id));

create policy "assets update by firm members"
on assets for update
using (public.is_firm_member(assets.firm_id))
with check (public.is_firm_member(assets.firm_id));

create policy "assets delete by firm members"
on assets for delete
using (public.is_firm_member(assets.firm_id));

create policy "liabilities select for firm or household members"
on liabilities for select
using (
  public.is_firm_member(liabilities.firm_id)
  or public.is_household_member(liabilities.household_id)
);

create policy "liabilities insert by firm members"
on liabilities for insert
with check (public.is_firm_member(liabilities.firm_id));

create policy "liabilities update by firm members"
on liabilities for update
using (public.is_firm_member(liabilities.firm_id))
with check (public.is_firm_member(liabilities.firm_id));

create policy "liabilities delete by firm members"
on liabilities for delete
using (public.is_firm_member(liabilities.firm_id));

-- ---------------------------------------------------------------------------
-- 4b. asset_flags — firm full access; household members SELECT only.
-- The household path is resolved through the parent asset.
-- ---------------------------------------------------------------------------
create policy "asset_flags select for firm or household members"
on asset_flags for select
using (
  public.is_firm_member(asset_flags.firm_id)
  or exists (
    select 1 from assets a
    where a.id = asset_flags.asset_id
      and public.is_household_member(a.household_id)
  )
);

create policy "asset_flags insert by firm members"
on asset_flags for insert
with check (public.is_firm_member(asset_flags.firm_id));

create policy "asset_flags update by firm members"
on asset_flags for update
using (public.is_firm_member(asset_flags.firm_id))
with check (public.is_firm_member(asset_flags.firm_id));

create policy "asset_flags delete by firm members"
on asset_flags for delete
using (public.is_firm_member(asset_flags.firm_id));

-- ---------------------------------------------------------------------------
-- 4c. approval_gates / review_packets — firm full access; household members
--     SELECT only. The household path is resolved through the parent matter.
-- review_packets has no UPDATE/DELETE policies: immutable once created.
-- ---------------------------------------------------------------------------
create policy "approval_gates select for firm or household members"
on approval_gates for select
using (
  public.is_firm_member(approval_gates.firm_id)
  or exists (
    select 1 from matters m
    where m.id = approval_gates.matter_id
      and public.is_household_member(m.household_id)
  )
);

create policy "approval_gates insert by firm members"
on approval_gates for insert
with check (public.is_firm_member(approval_gates.firm_id));

create policy "approval_gates update by firm members"
on approval_gates for update
using (public.is_firm_member(approval_gates.firm_id))
with check (public.is_firm_member(approval_gates.firm_id));

create policy "approval_gates delete by firm members"
on approval_gates for delete
using (public.is_firm_member(approval_gates.firm_id));

create policy "review_packets select for firm or household members"
on review_packets for select
using (
  public.is_firm_member(review_packets.firm_id)
  or exists (
    select 1 from matters m
    where m.id = review_packets.matter_id
      and public.is_household_member(m.household_id)
  )
);

create policy "review_packets insert by firm members"
on review_packets for insert
with check (public.is_firm_member(review_packets.firm_id));

-- ---------------------------------------------------------------------------
-- 4d. comments — append-only. Firm members and household members may INSERT
--     and SELECT on their matters; no UPDATE/DELETE policies (deny by
--     default).
-- ---------------------------------------------------------------------------
create policy "comments select for firm or household members"
on comments for select
using (
  exists (
    select 1 from matters m
    where m.id = comments.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "comments insert for firm or household members"
on comments for insert
with check (
  exists (
    select 1 from matters m
    where m.id = comments.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

-- ---------------------------------------------------------------------------
-- 4e. clarification_requests — firm full access; household members may
--     SELECT and UPDATE the response fields of requests they created
--     (the update guard trigger above limits the columns).
-- ---------------------------------------------------------------------------
create policy "clarification_requests select for firm or household members"
on clarification_requests for select
using (
  exists (
    select 1 from matters m
    where m.id = clarification_requests.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "clarification_requests insert by firm members"
on clarification_requests for insert
with check (public.is_firm_member(clarification_requests.firm_id));

create policy "clarification_requests update by firm members"
on clarification_requests for update
using (public.is_firm_member(clarification_requests.firm_id))
with check (public.is_firm_member(clarification_requests.firm_id));

create policy "clarification_requests delete by firm members"
on clarification_requests for delete
using (public.is_firm_member(clarification_requests.firm_id));

create policy "clarification_requests household members update own responses"
on clarification_requests for update
using (
  clarification_requests.created_by_user_id = auth.uid()
  and exists (
    select 1 from matters m
    where m.id = clarification_requests.matter_id
      and public.is_household_member(m.household_id)
  )
)
with check (
  clarification_requests.created_by_user_id = auth.uid()
  and exists (
    select 1 from matters m
    where m.id = clarification_requests.matter_id
      and public.is_household_member(m.household_id)
  )
);

-- ---------------------------------------------------------------------------
-- 4f. notification_outbox — firm members only (all operations). Clients
--     never see the outbox.
-- ---------------------------------------------------------------------------
create policy "notification_outbox all by firm members"
on notification_outbox for all
using (public.is_firm_member(notification_outbox.firm_id))
with check (public.is_firm_member(notification_outbox.firm_id));

-- ---------------------------------------------------------------------------
-- 5. updated_at triggers (tables that carry the column)
-- ---------------------------------------------------------------------------
create trigger trg_assets_set_updated_at
before update on assets
for each row execute function public.set_updated_at();

create trigger trg_liabilities_set_updated_at
before update on liabilities
for each row execute function public.set_updated_at();

create trigger trg_approval_gates_set_updated_at
before update on approval_gates
for each row execute function public.set_updated_at();

create trigger trg_clarification_requests_set_updated_at
before update on clarification_requests
for each row execute function public.set_updated_at();
