-- ============================================================================
-- Self-serve provisioning + jurisdiction-default routing (Phase 2, 2026-10-27)
--
-- D2: jurisdiction_default_firms registry — one default firm per jurisdiction.
--     Backend-only table: RLS enabled with NO policies (owner connections
--     bypass, API roles default-deny) per the standing RLS rule.
-- D3: households.origin provenance ('firm' | 'self_serve') + an index
--     supporting the self-serve find-or-create lookup keyed on
--     (created_by_user_id, firm_id, origin).
-- ============================================================================

create table jurisdiction_default_firms (
  jurisdiction_code text primary key,
  firm_id uuid not null references firms(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table jurisdiction_default_firms enable row level security;
-- No policies: backend-only registry (see header).

alter table households
  add column origin text not null default 'firm'
  check (origin in ('firm', 'self_serve'));

create index households_self_serve_lookup_idx
  on households (created_by_user_id, firm_id, origin);
