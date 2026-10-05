-- Amendment requests for implemented estate plans
create table if not exists amendment_requests (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  description text not null,
  affected_template_types text[] not null default '{}',
  status text not null default 'pending' check (status in (
    'pending','approved','rejected','completed'
  )),
  created_by_user_id uuid not null references profiles(id),
  decided_by_user_id uuid references profiles(id),
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_amendment_requests_matter
  on amendment_requests (matter_id);
