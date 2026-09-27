# LegacyFlow — Supabase (Slice 1: foundation)

Database layer for the LegacyFlow modular monolith. Slice 1 covers tenancy,
identity, jurisdiction packs, households, matters, and the append-only audit
trail — per §7.2 of the product build artifact.

```
supabase/
├── migrations/
│   └── 20260926220000_foundation_slice1.sql   # schema + triggers + RLS
├── seeds/
│   ├── 01_jurisdictions.sql                  # US-VA + synthetic US-XZ packs
│   └── 02_live_rls_fixtures.sql              # live-project RLS fixtures (dashboard)
├── tests/
│   ├── conftest.py                           # DB bootstrap + auth shim + fixtures
│   ├── test_rls.py                           # RLS allow/deny integration tests (local DB)
│   └── test_rls_live.py                      # RLS allow/deny tests vs live project (PostgREST)
└── README.md
```

## Apply migrations locally

Standard path (any Postgres you can reach, e.g. Supabase CLI-provisioned DB):

```bash
psql "$DATABASE_URL" -f supabase/migrations/20260926220000_foundation_slice1.sql
psql "$DATABASE_URL" -f supabase/seeds/01_jurisdictions.sql
```

On a real Supabase project, `auth.users` / `auth.uid()` already exist and the
migration's `pgcrypto` guard installs the extension. The RLS test harness
(see below) applies both files automatically against a throwaway database.

## How seeds work

`01_jurisdictions.sql` inserts reference data with **fixed UUIDs** and
`ON CONFLICT (id) DO NOTHING`, so it is safe to re-run (idempotency is
asserted by the test suite — the seed file is applied twice during setup
plus once more in an explicit test).

- **US-VA** (`11111111-…`): Virginia starter pack `us-va` v0.1.0
  (`22222222-…`), manifest per §9.3 of the product artifact.
- **US-XZ** (`33333333-…`): "Synthetic Xanadu (TEST ONLY)" pack
  `us-xz-synthetic` v1 (`44444444-…`) with a **visibly different config**
  (education/eligibility-triage/funding off, `execution_guidance: disabled`,
  question set `9.9.9-test`). It exists so tests can prove application code
  reads pack config instead of hard-coding VA behavior (§12.2 scenario 6).

Synthetic data only. `source_bundle_hash` is `'synthetic-dev-only'`.

## How RLS tests run

Requirements: Python 3.12+, `psycopg` (v3), `pytest`.

```bash
pip install --break-system-packages -q "psycopg[binary]" pytest
cd ~/workspace/legacyflow
python3 -m pytest supabase/tests -v
```

- **Without `DATABASE_URL`**: tests boot a user-space Postgres via
  `pgserver.get_server('/tmp/lf-pgdata')` — no Docker, no sudo needed.
- **With `DATABASE_URL` set** (CI path with a postgres service): tests
  connect to it instead. The database must be **throwaway** — the session
  fixture drops/recreates the `public` schema, creates an `auth.users` stub
  plus an `auth.uid()` shim (reads the `app.user_id` session GUC), applies
  the migration + seeds, then runs every test as the least-privilege role
  `lf_app` (`SET ROLE`) so RLS is genuinely enforced. The CI postgres user
  must be a superuser (or a member of `lf_app`) for `SET ROLE` to work.
- **If neither is available**: the suite skips with `no database available`.

Each test sets `app.user_id` to the synthetic user under test inside a
transaction that is always rolled back; an unauthenticated caller is
simulated by leaving the GUC unset.

## What the suite covers

| # | Test | §12.2 scenario |
|---|------|----------------|
| a | Firm A attorney sees mA, not mB | 1 |
| b | Firm B attorney sees mB, not mA | 1 |
| c | Household-member (no firm membership) sees own matter only | 2 |
| d | Unauthenticated sees zero matters / jurisdictions | 1 |
| e | Firm A cannot read firm B roster or households | 1 |
| f | Cross-firm matter INSERT denied (`42501`) | 1 |
| + | Own-firm matter INSERT allowed | — |
| + | Household member cannot INSERT matters | 2 |
| + | Audit events isolated per firm; append-only (no client write) | 1 |
| + | Status history follows parent-matter access | — |
| + | Profiles: self + firm colleagues only | — |
| + | Jurisdiction reference readable, not writable | — |
| + | VA vs XZ packs distinguishable by manifest config | 6 |
| + | `updated_at` trigger fires | §7.1 |
| + | Cross-firm matter UPDATE is a no-op | 1 |
| + | Seeds idempotent across repeated applies | — |

## CI expectation

The CI job for `supabase/` should:

1. Start a postgres service and export `DATABASE_URL`.
2. `pip install "psycopg[binary]" pytest` (or use a locked requirements file).
3. `python3 -m pytest supabase/tests` — must be green; skips fail the job
   (a skip means no database was available, which is a CI misconfiguration).
4. Run a secret scan over `supabase/` (no secrets may ever land here —
   fixtures use `@example.test` addresses and fixed synthetic UUIDs).

Migration hygiene: one forward-only migration per change, never edit a
shipped migration file; new tables get `ENABLE ROW LEVEL SECURITY` and an
explicit deny-path test before merge.

## Live-project targeting (RLS verification end to end)

`test_rls.py` proves the policies against a local database. `test_rls_live.py`
proves them against the real Supabase project through PostgREST, where
`auth.uid()` resolves from actual user JWTs. Sandbox/CI never applies SQL
to the live project — a human applies it in the dashboard, then the tests run.

**One-time provisioning (project owner, dashboard):**

1. SQL editor: run `supabase/migrations/20260926220000_foundation_slice1.sql`.
2. SQL editor: run `supabase/seeds/01_jurisdictions.sql`.
3. Authentication → Users: create two synthetic users (auto-confirm email,
   set passwords), e.g. `slice1-rls-a@example.test` and
   `slice1-rls-b@example.test`.
4. SQL editor: run `supabase/seeds/02_live_rls_fixtures.sql` (idempotent;
   creates two firms, profiles, memberships, households, matters, audit
   events with fixed synthetic UUIDs).

**Run the live tests (locally or CI):**

```bash
export SUPABASE_URL="https://<ref>.supabase.co"
export SUPABASE_ANON_KEY="<anon key>"
export LIVE_TEST_USER_A_EMAIL="slice1-rls-a@example.test"
export LIVE_TEST_USER_A_PASSWORD="<chosen password>"
export LIVE_TEST_USER_B_EMAIL="slice1-rls-b@example.test"
export LIVE_TEST_USER_B_PASSWORD="<chosen password>"
python3 -m pytest supabase/tests/test_rls_live.py -v
```

The suite authenticates each user via `/auth/v1/token?grant_type=password`
and asserts, through the anon key + user bearer tokens: each user sees only
their firm's firms/matters/roster/audit rows, explicit cross-firm filters
return nothing, unauthenticated access sees no protected rows, cross-firm
and firm inserts are denied, and authenticated reference-data reads succeed.
Write-denied attempts leave no residue (RLS rejects before insert).

Any missing prerequisite (env vars, users, migrations) produces a precise
skip message instead of a failure — the suite stays green in CI until the
live project is provisioned. Once provisioned, every test must pass.
