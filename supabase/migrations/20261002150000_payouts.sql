-- Attorney payouts and case requests (Sprint 5).
--
-- Flow: when a matter reaches PLAN_IMPLEMENTED, a payout record is created
-- for the assigned attorney (pending). A firm admin reviews and approves,
-- then marks paid (manual bank transfer in v1; Stripe Connect later).
-- Attorneys can also request unassigned cases via case_requests; the admin
-- still assigns manually.

create table public.payouts (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references public.firms (id) on delete cascade,
  attorney_user_id uuid not null references public.profiles (id) on delete cascade,
  matter_id uuid not null references public.matters (id) on delete cascade,
  amount_cents integer not null check (amount_cents > 0),
  -- 'standard' ($150) or 'complex' ($300); resolved from env at creation time.
  tier text not null default 'standard' check (tier in ('standard', 'complex')),
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'paid', 'disputed')),
  stripe_payout_id text,
  dispute_reason text,
  created_at timestamptz not null default now(),
  approved_at timestamptz,
  approved_by_user_id uuid references public.profiles (id),
  paid_at timestamptz,
  -- One payout per matter: completion is a single event.
  constraint payouts_matter_unique unique (matter_id)
);

create index payouts_firm_status_idx
  on public.payouts (firm_id, status);
create index payouts_attorney_idx
  on public.payouts (attorney_user_id);

create table public.case_requests (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references public.firms (id) on delete cascade,
  attorney_user_id uuid not null references public.profiles (id) on delete cascade,
  matter_id uuid not null references public.matters (id) on delete cascade,
  status text not null default 'pending'
    check (status in ('pending', 'accepted', 'declined')),
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  decided_by_user_id uuid references public.profiles (id),
  -- One open request per attorney per matter.
  constraint case_requests_attorney_matter_unique unique (attorney_user_id, matter_id)
);

create index case_requests_firm_status_idx
  on public.case_requests (firm_id, status);

-- Attorney availability: paused attorneys don't appear in assignment lists.
-- Stored on the membership row to avoid a new table.
alter table public.firm_memberships
  add column if not exists accepting_cases boolean not null default true;

alter table public.payouts enable row level security;
alter table public.case_requests enable row level security;

-- Service role bypasses RLS; authenticated users go through the API which
-- enforces firm-role checks. No direct client access grants.
