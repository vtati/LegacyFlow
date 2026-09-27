# LegacyFlow

Attorney-enabled estate-planning workflow platform. Multi-tenant, multi-jurisdiction,
modular monolith. See `CLAUDE.md` for the engineering contract and
`docs/adr/ADR-001-modular-monolith-jurisdiction-packs.md` for the architecture decision.

> **Slice 1 (foundation) status:** repo scaffolding, CI, migrations scaffold, auth shell,
> jurisdiction-pack seeds (`US-VA`, synthetic `US-XZ`). The worker is a stub; cloud
> Supabase wiring is a stub. Legal templates, AI, document generation, file upload, and
> payments are explicitly out of this slice.

## Prerequisites

- Node 24, pnpm 10 (`corepack` or `npm i -g pnpm@10.34.5`)
- Python 3.12
- Postgres 16 — via a Supabase project (managed) or a local instance

## Install

```bash
pnpm install
cp .env.example .env   # then fill in real values; never commit .env
```

## Environment variables

All variables are documented in `.env.example`. Summary:

| Variable                                                     | Used by               | Notes                                                   |
| ------------------------------------------------------------ | --------------------- | ------------------------------------------------------- |
| `SUPABASE_URL`                                               | api, web              | Supabase project URL                                    |
| `SUPABASE_ANON_KEY`                                          | web                   | Browser-safe anon key                                   |
| `SUPABASE_JWKS_URL`                                          | api                   | JWT verification                                        |
| `SUPABASE_SERVICE_ROLE_KEY`                                  | api, worker           | **Server-only.** Never in browser code, never committed |
| `DATABASE_URL`                                               | migrations, RLS tests | Direct Postgres connection string                       |
| `API_PORT`                                                   | api                   | Defaults to `8000`                                      |
| `REDIS_URL`                                                  | worker                | Job queue                                               |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web                   | Public Supabase values for the browser                  |

## Run

API (FastAPI):

```bash
cd services/api && uvicorn app.main:app --reload
```

Web (Next.js):

```bash
pnpm --filter @legacyflow/web dev
```

Worker: **slice-1 stub** — the `services/worker` skeleton exists but runs no jobs yet.
Start it the same way as the API once job handlers land.

Or run everything via Turborepo from the repo root:

```bash
pnpm dev
```

## Migrations

Migrations live in `supabase/migrations/` and apply in filename order.

Local / direct Postgres:

```bash
for f in $(ls supabase/migrations/*.sql | sort); do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

Supabase CLI (later): `supabase db push`. Cloud Supabase wiring is a slice-1 stub.

## Tests

```bash
pnpm test            # JS workspaces via Turborepo
pnpm lint            # turbo run lint
pnpm typecheck       # turbo run typecheck
pnpm format:check    # prettier check
```

Python per service:

```bash
cd services/api && ruff format --check . && ruff check . && mypy . && pytest -q
```

Supabase RLS tests (allow/deny per table) need a Postgres database:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/legacyflow_test pytest supabase/tests -q
```

## Repo layout

```text
apps/web            Next.js family + firm + admin workspaces
services/api        FastAPI modular monolith (domain services per bounded context)
services/worker     Background jobs (slice-1 stub)
packages/ui         Shared React components
packages/design-tokens  Design tokens (colors, radius, typography)
packages/contracts  Shared TS/Python contracts (OpenAPI-derived, Zod/Pydantic)
supabase/           Migrations, seeds, RLS tests
docs/adr            Architecture decision records
```

## Safety

Synthetic data only in development and tests. No real client facts, no PII in logs or
fixtures. See `CLAUDE.md` §"Non-negotiable safety rules".
