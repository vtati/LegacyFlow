# LegacyFlow Production Deployment Guide

**Status:** Planning — decisions recorded 2026-10-02. Implementation deferred until approaching production launch.
**Owner:** Venugopal Tati
**Last updated:** 2026-10-02

---

## 1. Decisions Log

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | Document storage → **Supabase Storage** (not local disk, not S3) | Already on Supabase; private buckets + RLS + signed URLs; negligible cost (~$10/mo at 10k matters) | 2026-10-02 |
| 2 | Vercel → **Pro plan** ($20/mo, single seat) | Commercial license requirement (paid product); reliability (no Hobby bandwidth shutoff); NOT for raw speed — edge CDN is identical on Hobby/Pro | 2026-10-02 |
| 3 | Vercel → **one project, two branch domains** | `main` → `legacyflow.genalphalabs.com`; `staging` branch → `staging.legacyflow.genalphalabs.com`. Zero extra cost, zero extra management | 2026-10-02 |
| 4 | Railway → **one project, two environments** (revised from two projects) | Solo founder — separate projects add dashboard/config overhead with no real benefit. Environments have fully separate variables, services, deployments | 2026-10-02 |
| 5 | Supabase → **two separate projects** (staging + production) | Real client data isolation. Production data must never flow to staging. Non-negotiable for PII | 2026-10-02 |
| 6 | Branching → `feature` → `staging` → `main` | `staging` branch auto-deploys to staging env; `main` auto-deploys to production. Human gate: explicit merge `staging` → `main` promotes to production | 2026-10-02 |
| 7 | Storage backend abstraction (`local` / `supabase`) | Dev/test keep working on local disk; production uses Supabase. Switched via `STORAGE_BACKEND` env var. No API contract changes | 2026-10-02 |

### Open questions (resolve before implementation)
- [ ] Staging API subdomain: `legacyflow-api-staging.genalphalabs.com`? (proposed, not confirmed)
- [ ] Production Supabase project name/plan: new Pro ($25/mo) or start on free tier?
- [ ] Timeline: when does production setup begin?

---

## 2. Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        PRODUCTION                            │
│                                                              │
│  Vercel (Pro)           Railway (production env)             │
│  ┌──────────────┐       ┌─────────────────────────┐         │
│  │ main branch  │       │ api (STORAGE_BACKEND=    │         │
│  │ legacyflow.  │──────▶│   supabase)              │         │
│  │ genalphalabs │       │ worker                   │         │
│  │ .com         │       │ redis                    │         │
│  └──────────────┘       │ reminder-dispatch        │         │
│                         └────────────┬────────────┘         │
│                                      │                       │
│                         ┌────────────▼────────────┐         │
│                         │ Supabase (NEW project)  │         │
│                         │ • Postgres (prod data)  │         │
│                         │ • Auth (prod users)     │         │
│                         │ • Storage: client-      │         │
│                         │   uploads, plan-        │         │
│                         │   documents (private)   │         │
│                         └─────────────────────────┘         │
│                                                              │
│  Stripe: LIVE mode keys                                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                         STAGING                              │
│                                                              │
│  Vercel (Pro, same plan) Railway (staging env)               │
│  ┌──────────────┐       ┌─────────────────────────┐         │
│  │ staging      │       │ api (STORAGE_BACKEND=    │         │
│  │ branch       │──────▶│   supabase)              │         │
│  │ staging.     │       │ worker / redis /        │         │
│  │ legacyflow.  │       │ reminder-dispatch        │         │
│  │ genalphalabs │       └────────────┬────────────┘         │
│  │ .com         │                    │                       │
│  └──────────────┘       ┌────────────▼────────────┐         │
│                         │ Supabase (EXISTING)     │         │
│                         │ nlrxlpjkrmvubvsfepjs    │         │
│                         │ • Synthetic test data   │         │
│                         │ • Storage buckets exist │         │
│                         └─────────────────────────┘         │
│                                                              │
│  Stripe: TEST mode keys                                      │
└─────────────────────────────────────────────────────────────┘
```

### Current state (2026-10-02)
- Railway project `legacyflow-staging` (050e92d2-...) has ONE environment (`production` env ID 3ad22e4f-...) serving as both test and live. Services: `api`, `worker`, `redis`, `reminder-dispatch`.
- Supabase project `nlrxlpjkrmvubvsfepjs` serves as the single database.
- Vercel: Pro plan active. Serves `legacyflow.genalphalabs.com`.
- `STORAGE_BACKEND=supabase` is set on the current Railway API service.
- Storage buckets `client-uploads` and `plan-documents` exist (private) on the current Supabase project.

---

## 3. Implementation Steps (in order)

### Phase A — Railway: add production environment
1. In the existing Railway project, create a new environment named `production`.
2. Duplicate the 4 services (`api`, `worker`, `redis`, `reminder-dispatch`) into the `production` environment.
3. Rename the current environment from `production` → `staging` (to avoid confusion).
4. Set production env vars (copy from staging, then override):
   - `STORAGE_BACKEND=supabase`
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY` → **production** Supabase project values
   - `DATABASE_URL` → **production** Supabase Postgres
   - `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` → **live** mode keys
   - `API_ENV=production`
   - All secrets (`DOWNLOAD_TOKEN_SECRET`, `INTERVIEW_TURN_ENCRYPTION_KEY`, etc.) → generate fresh values, do NOT reuse staging secrets
5. Attach custom domain `legacyflow-api.genalphalabs.com` to the production `api` service.
6. Attach custom domain `legacyflow-api-staging.genalphalabs.com` (or confirmed alternative) to the staging `api` service.

### Phase B — Supabase: create production project
1. Create new Supabase project (suggested name: `legacyflow-production`).
2. Apply ALL migrations from `supabase/migrations/` in order via SQL Editor or CLI.
3. Create storage buckets (same SQL as staging):
   ```sql
   INSERT INTO storage.buckets (id, name, public) VALUES
     ('client-uploads', 'client-uploads', false),
     ('plan-documents', 'plan-documents', false)
   ON CONFLICT (id) DO NOTHING;
   ```
4. Verify RLS policies are active on all tables.
5. Enable daily backups (included on Pro).
6. Record the new `SUPABASE_URL`, anon key, and service_role key in the Secure Vault (do NOT paste in chat).
7. **Production starts with zero data.** No test users, no synthetic matters.

### Phase C — Vercel: branch domains
1. In the existing Vercel project, add domain `staging.legacyflow.genalphalabs.com` assigned to the `staging` branch.
2. Confirm `legacyflow.genalphalabs.com` remains assigned to `main` (production).
3. Set branch-scoped environment variables:
   - `staging` branch → `NEXT_PUBLIC_API_URL=https://legacyflow-api-staging.genalphalabs.com`
   - `main` branch → `NEXT_PUBLIC_API_URL=https://legacyflow-api.genalphalabs.com`

### Phase D — GitHub: branching and CI
1. Create `staging` branch from current `main`.
2. Update Railway + Vercel deploy triggers:
   - `staging` branch → staging environment
   - `main` branch → production environment
3. CI (`.github/workflows/ci.yml`) already runs on PRs and pushes — no change needed for gating.
4. Add post-deploy E2E smoke test job (runs against staging after staging deploys).
5. Add production health-check job (runs after production deploys; alerts on failure).

### Phase E — Promotion process (staging → production)
1. Feature work merges to `staging` via PR (CI must pass).
2. Staging auto-deploys. Automated smoke test runs.
3. Manual verification on `staging.legacyflow.genalphalabs.com`.
4. **Human gate:** create PR `staging` → `main`, review the diff, merge.
5. Production auto-deploys. Health checks run. Monitor error rates for 30 min.
6. **Rollback:** redeploy previous commit in Railway/Vercel (one click each). Migrations are backward-compatible so schema rollback is safe.

### Phase F — Cutover
1. Point production DNS to the new production environment.
2. Verify: fresh signup → household → matter → intake → attorney review → payout, all on production.
3. Verify document upload → Supabase production bucket; download → signed URL.
4. Decommission the old single-environment setup (after a soak period).

---

## 4. Database Migration Safety Rules

These rules are standing — they apply to every migration going forward:

1. **Staging first.** Every migration is applied to staging and verified before production.
2. **Backward-compatible only.** Add columns/tables; never rename or drop in the same deploy. Destructive changes use expand → migrate → contract across two releases.
3. **Idempotent.** Migrations must be safe to re-run (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
4. **No data loss.** Any migration that deletes or transforms data requires explicit Venugopal approval.

---

## 5. Data & Security Boundaries (standing)

| Rule | Detail |
|------|--------|
| Production data never flows to staging | No dumps, no copies, no exceptions. Staging uses synthetic data only. |
| Separate Auth | Staging and production Supabase Auth are completely separate user pools. |
| Separate Stripe keys | Test mode on staging, live mode on production. Never mix. |
| Separate secrets | Every secret (token keys, encryption keys) is generated fresh per environment. |
| Storage isolation | Buckets are per-Supabase-project; staging files and production files never share a bucket. |
| Signed URLs | 15-minute expiry on all document downloads, both environments. |

---

## 6. Cost Summary (new monthly spend for production)

| Service | Cost |
|---------|------|
| Railway (production env, usage-based) | ~$10–20/mo |
| Supabase (new Pro project) | $25/mo |
| Vercel Pro | $0 extra (already on Pro, covers both) |
| **Total new** | **~$35–45/mo** |

---

## 7. Rollback Plan

| Layer | How to roll back | Time |
|-------|-----------------|------|
| Railway API/worker | Redeploy previous commit (Railway dashboard → Deployments → Redeploy) | ~2 min |
| Vercel frontend | Instant rollback (Vercel dashboard → Deployments → Promote to Production) | ~30 sec |
| Database migration | Backward-compatible migrations mean old code works on new schema — no DB rollback needed in the normal case | N/A |
| DNS | Revert CNAME if cutover fails | ~5 min propagation |

**If production is broken and the cause is unclear:** roll back Railway + Vercel first (fastest), investigate second.

---

## 8. Appendix: Key Identifiers

| Resource | Identifier |
|----------|------------|
| GitHub repo | `vtati/LegacyFlow` |
| Railway project (staging+prod) | `legacyflow-staging` (050e92d2-5850-479b-b877-c2509f3a756d) — rename to `legacyflow` when production env is added |
| Railway API service | 5493dfc7-4d20-4d3d-9766-941312563cd1 |
| Staging Supabase project | `nlrxlpjkrmvubvsfepjs` |
| Production Supabase project | _(to be created)_ |
| Production web | `legacyflow.genalphalabs.com` |
| Production API | `legacyflow-api.genalphalabs.com` |
| Staging web | `staging.legacyflow.genalphalabs.com` (proposed) |
| Staging API | `legacyflow-api-staging.genalphalabs.com` (proposed) |
| Storage buckets (both envs) | `client-uploads`, `plan-documents` (private) |
| Commit identity | `vtati <vtati@users.noreply.github.com>` |
