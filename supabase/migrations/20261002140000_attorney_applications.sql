-- Attorney applications for manual onboarding (Sprint 3).
--
-- Flow: attorney signs up via Supabase Auth (gets public.profiles row via the
-- handle_new_user trigger), then submits an application. A firm admin reviews
-- the bar number / documents manually and approves or rejects. Approving
-- creates an active firm_membership with the 'attorney' role.

create table public.attorney_applications (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references public.firms (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  full_name text not null,
  email text not null,
  phone text,
  bar_number text not null,
  jurisdictions text[] not null default '{}',
  years_experience integer,
  practice_areas text,
  -- JSON object: { "bar_card": "<url>", "insurance": "<url>", "id_document": "<url>" }
  document_urls jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected')),
  rejection_reason text,
  reviewed_by_user_id uuid references public.profiles (id),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index attorney_applications_firm_status_idx
  on public.attorney_applications (firm_id, status);

create unique index attorney_applications_user_pending_uidx
  on public.attorney_applications (user_id)
  where status = 'pending';

alter table public.attorney_applications enable row level security;

-- Service role bypasses RLS; authenticated users go through the API which
-- enforces firm-staff checks. No direct client access grants.
