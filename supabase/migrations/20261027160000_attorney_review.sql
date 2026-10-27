-- ============================================================================
-- Attorney Reviewer Agent (plan 2026-10-27, design §9)
--
-- Frozen review artifacts + findings. All four tables are backend-only:
-- RLS enabled with NO policies (owner connections bypass, API roles
-- default-deny) per the standing RLS rule.
--
--   attorney_review_snapshots — one row per rendered package version:
--     the frozen body + template + context the reviewer analyses.
--   attorney_review_sources   — the matter-level frozen source payload
--     per source fingerprint (persons, nominations, answers, the
--     resolved draft set, pack question keys).
--   attorney_review_runs      — one row per review run over a fingerprint.
--   attorney_review_findings  — structured findings bound to a run,
--     idempotent by content hash within the run.
-- ============================================================================

create table attorney_review_snapshots (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id),
  firm_id uuid not null references firms(id),
  package_id uuid not null references document_packages(id),
  package_version integer not null,
  source_fingerprint text not null,
  template_type text not null,
  template_body text not null,
  body_text text not null,
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (package_id, package_version)
);

create index attorney_review_snapshots_matter_fp_idx
  on attorney_review_snapshots (matter_id, source_fingerprint);

alter table attorney_review_snapshots enable row level security;
-- No policies: backend-only (see header).

create table attorney_review_sources (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id),
  firm_id uuid not null references firms(id),
  source_fingerprint text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  unique (matter_id, source_fingerprint)
);

alter table attorney_review_sources enable row level security;
-- No policies: backend-only (see header).

create table attorney_review_runs (
  id uuid primary key default gen_random_uuid(),
  matter_id uuid not null references matters(id),
  firm_id uuid not null references firms(id),
  source_fingerprint text not null,
  status text not null default 'running'
    check (status in ('running', 'completed', 'failed')),
  model_id text,
  prompt_version text,
  error text,
  created_by_user_id uuid,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create index attorney_review_runs_matter_idx
  on attorney_review_runs (matter_id, created_at desc);

alter table attorney_review_runs enable row level security;
-- No policies: backend-only (see header).

create table attorney_review_findings (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references attorney_review_runs(id),
  matter_id uuid not null references matters(id),
  package_id uuid references document_packages(id),
  finding_hash text not null,
  layer text not null check (layer in ('deterministic', 'model')),
  check_name text not null,
  severity text not null check (severity in ('should_fix', 'question_for_attorney')),
  location jsonb not null default '{}'::jsonb,
  explanation text not null,
  suggested_fix text,
  disposition text not null default 'open'
    check (disposition in ('open', 'acknowledged', 'fixed_by_regeneration', 'stale')),
  acknowledged_by_user_id uuid,
  acknowledgment_note text,
  acknowledged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, finding_hash)
);

create index attorney_review_findings_matter_idx
  on attorney_review_findings (matter_id, created_at desc);

alter table attorney_review_findings enable row level security;
-- No policies: backend-only (see header).
