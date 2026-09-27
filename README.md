# LegacyFlow

Attorney-enabled estate-planning workflow platform. Multi-tenant,
multi-jurisdiction, modular monolith: Next.js web, FastAPI API, Dramatiq
worker, Supabase Postgres. See `CLAUDE.md` for the engineering contract and
`docs/adr/ADR-001-modular-monolith-jurisdiction-packs.md` for the architecture
decision. Launch checklist: `docs/launch-readiness.md`. Pilot demo:
`docs/pilot-demo-script.md`. Operations: `docs/observability-runbook.md`.

**Synthetic data only** in development and tests. No real client facts, no PII
in logs or fixtures.

## Prerequisites

- Node 24, pnpm 10 (`corepack enable`, or `npm i -g pnpm@10.34.5`)
- Python 3.12
- Postgres 16 — via a Supabase project (managed) or a local instance
  (`psql` client needed for the manual migration loop below)

## Install

```bash
pnpm install --frozen-lockfile
cp .env.example .env   # then fill in real values; never commit .env
```

## Environment variables

All variables are documented in `.env.example` (verified present at repo
root). Summary:

| Variable                                                     | Used by                   | Notes                                                                                                                                                                                               |
| ------------------------------------------------------------ | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SUPABASE_URL`                                               | api, web                  | Supabase project URL                                                                                                                                                                                |
| `SUPABASE_ANON_KEY`                                          | web                       | Browser-safe anon key                                                                                                                                                                               |
| `SUPABASE_JWKS_URL`                                          | api                       | JWT verification (defaults to `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`)                                                                                                                       |
| `SUPABASE_SERVICE_ROLE_KEY`                                  | api, worker               | **Server-only.** Required by the API at startup. Never in browser code, never committed                                                                                                             |
| `DATABASE_URL`                                               | migrations, RLS tests     | Direct Postgres connection string                                                                                                                                                                   |
| `API_PORT`                                                   | api                       | Defaults to `8000`                                                                                                                                                                                  |
| `REDIS_URL`                                                  | worker, reminder dispatch | Job queue; when unset the worker uses an in-process `StubBroker` (dev only)                                                                                                                         |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web                       | Public Supabase values for the browser. **When unset, the web runs in mock-auth mode** (synthetic demo session via the `lf_mock_session` cookie) — handy for local UI work with no Supabase project |

Also supported (see `services/api/app/core/config.py`; all documented in
`.env.example`): `LOG_LEVEL`, `SENTRY_DSN`, `RATE_LIMIT_ENABLED`,
`RATE_LIMIT_DEFAULT_PER_MINUTE`, `RATE_LIMIT_UPLOAD_AI_PER_MINUTE`,
`RATE_LIMIT_AUTH_PER_MINUTE`, `IDEMPOTENCY_TTL_HOURS`, `AI_PROVIDER`,
`LF_FLAG_<KEY>` (feature-flag env overrides), `DOCUMENT_STORAGE_DIR`,
`STORAGE_DIR`, `DOWNLOAD_TOKEN_SECRET`, `TEST_JWT_ISSUER`/`TEST_JWT_SECRET`
(local HS256 auth override for tests/dev).

## Database: migrate

Migrations live in `supabase/migrations/` and apply in filename order
(6 files: foundation → household/intake → assets/review → files/documents →
feature flags → reminders/privacy). Seeds in `supabase/seeds/` are idempotent.

Local / direct Postgres:

```bash
for f in $(ls supabase/migrations/*.sql | sort); do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
psql "$DATABASE_URL" -f supabase/seeds/01_jurisdictions.sql
```

Live Supabase: apply the migration files **in filename order** via the
dashboard SQL editor (the CI migration job additionally creates the `auth`
schema shim before applying). `02_live_rls_fixtures.sql` is only for the live
RLS test suite (see Tests).

## Run

API (FastAPI, `http://localhost:8000`; `/healthz`, `/docs`, `/openapi.json`):

```bash
cd services/api
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
uvicorn app.main:app --reload --port "${API_PORT:-8000}"
```

Web (Next.js, `http://localhost:3000`):

```bash
pnpm --filter @legacyflow/web dev
```

Worker (Dramatiq actors: `send_notifications`, `send_due_reminders`,
`scan_file`, `example_ping`):

```bash
cd services/worker
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
.venv/bin/dramatiq worker.actors
```

With `REDIS_URL` set it uses Redis; otherwise a `StubBroker` with a logged
warning. Reminder cadence (every 15 min) is a deployment cron hitting
`POST /v1/admin/reminders/dispatch` — see `docs/observability-runbook.md`.

Or run web + API dev servers via Turborepo from the repo root:

```bash
pnpm dev
```

## Tests

```bash
pnpm test            # JS workspaces via Turborepo (web: vitest, 43 tests)
pnpm lint            # turbo run lint
pnpm typecheck       # turbo run typecheck
pnpm format:check    # prettier check
pnpm build           # turbo run build (CI also runs this)
```

API (FastAPI):

```bash
cd services/api && ruff check . && ruff format --check . && mypy app && pytest -q
```

Worker:

```bash
cd services/worker && ruff check . && ruff format --check . && mypy worker && pytest -q
```

Supabase RLS tests (allow/deny per table; need a Postgres database — uses
`DATABASE_URL` when set, otherwise boots a user-space Postgres via
`pgserver`):

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/legacyflow_test pytest supabase/tests -q
```

Live-Supabase RLS suite (needs project secrets, runs in CI):

```bash
cd supabase && pytest tests/test_rls_live.py -q
```

Playwright E2E (web shell against mock auth; API stubbed per test):

```bash
cd apps/web && pnpm e2e   # "playwright test"; Chromium only
```

## Repo layout

```text
apps/web               Next.js family + firm + admin workspaces (mock-auth dev mode)
apps/web/e2e           Playwright critical-path spec
services/api           FastAPI modular monolith (domains per bounded context)
services/api/app/core  Cross-cutting: auth, authz, audit, rate limiting, idempotency,
                       structured JSON logging, optional Sentry
services/api/app/domains  household, intake, matters, invitations, nominations,
                       assets, review (+gates, packs), documents (+signing,
                       funding, vault), files, ai, decisions, escalation,
                       firms, privacy (export/delete/reminders), feature_flags
services/worker        Dramatiq background jobs (notifications, reminders, file scans)
packages/ui            Shared React components
packages/design-tokens Design tokens (colors, radius, typography)
packages/contracts     Shared TS/Python contracts (OpenAPI-derived, Zod/Pydantic)
supabase/              Migrations, seeds, RLS tests
content-packs/         Jurisdiction packs (us-va, synthetic us-xz-synthetic)
docs/                  Launch readiness, pilot demo script, observability runbook, ADRs
```

## Safety

Synthetic data only in development and tests. No real client facts, no PII in
logs or fixtures. Production document release is fail-closed (always 409);
demo renderings carry a `DEMO ONLY — NOT LEGALLY VALID` watermark. See
`CLAUDE.md` §"Non-negotiable safety rules".
