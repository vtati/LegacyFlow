# LegacyFlow Production Launch Checklist

**Status:** Pre-launch hardening complete (Sprint 6). Items below require manual action.

## Database

- [ ] **Apply Supabase migration `20261002140000_attorney_applications.sql`**
  - Creates `attorney_applications` table (FKs, indexes, RLS enabled)
  - Run in Supabase Dashboard → SQL Editor
  - Blocks: attorney onboarding endpoints (`/attorney-applications`)

- [ ] **Apply Supabase migration `20261002150000_payouts.sql`**
  - Creates `payouts` and `case_requests` tables (FKs, unique constraints, indexes, RLS)
  - Adds `accepting_cases` column to `firm_memberships`
  - Run in Supabase Dashboard → SQL Editor
  - Blocks: payout system, case request endpoints

- [ ] **Verify RLS is enabled** on `attorney_applications`, `payouts`, `case_requests`
  - `SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN ('attorney_applications', 'payouts', 'case_requests');`

- [ ] **Seed VA document templates**
  - Run `services/api/seed_va_templates.py` for the production firm
  - Verifies: 4 templates (Will, Financial POA, Advance Medical Directive, Guardianship)

## Railway Environment Variables

- [ ] `LF_FLAG_DOCUMENT_ASSEMBLY=true`
  - Enables document package creation (highest-priority override)
  - Without this, `POST /v1/matters/{id}/document-packages` returns 409

- [ ] `LF_PAYOUT_STANDARD_CENTS=15000` (default $150)
- [ ] `LF_PAYOUT_COMPLEX_CENTS=30000` (default $300)
  - Configurable payout tiers; adjust per business decision

- [ ] `STRIPE_SECRET_KEY` (when ready for automated payouts)
  - v1 uses manual bank-transfer payouts; Stripe Connect interface is built but stubbed
  - See `services/api/app/domains/payouts/STRIPE_SETUP.md`

## Security (Sprint 6 audit — passed)

- [x] All API endpoints enforce role/scope checks (attorneys, payouts, documents verified)
- [x] No PII in logs (bar numbers, emails, addresses never logged)
- [x] File uploads: MIME allowlist (PDF/DOCX/PNG/JPEG), size limits, sanitized filenames
- [x] No CORS middleware (same-origin via Next.js rewrites; no wildcards)
- [x] Errors return RFC 7807 problem details with request IDs (no stack traces in production)
- [x] `/healthz` checks DB connectivity (returns 503 if Postgres unreachable)
- [x] Key dependencies current: Next.js 15.1.6 (includes CVE-2025-29927 fix), React 19, FastAPI 0.141, Pydantic 2.13
- [x] List endpoints paginated (limit/offset, max 200): attorney-applications, payouts, case-requests

## Performance (Sprint 6 — verified)

- [x] No N+1 queries in matter detail, review packet, or dashboard endpoints
- [x] Pagination on all new list endpoints
- [x] Interview bootstrap: uses default 120/min rate limit tier (fixed from 30/min)

## Data Integrity (Sprint 6 — verified)

- [x] All new tables have FKs with `ON DELETE CASCADE`, check constraints, indexes, RLS
- [x] Payout creation idempotent: `UNIQUE(matter_id)` + `ON CONFLICT DO NOTHING` (no double-payouts)
- [x] Case requests idempotent: `UNIQUE(attorney_user_id, matter_id)`
- [x] Audit events recorded for: attorney application submit/approve/reject, all payout state changes

## Monitoring

- [ ] **Configure uptime alert** on `https://legacyflow-api.genalphalabs.com/healthz`
  - Alert if: non-200 for > 2 minutes, or `"db": "unreachable"` in response
  - The health check now verifies Postgres connectivity (Railway IPv6 routing issue)

- [ ] **Watch these metrics** (first 30 days):
  - API 5xx rate (target: < 0.1%)
  - Interview bootstrap p95 latency (target: < 3s)
  - Payout creation failures (should be zero; idempotent)

- [ ] **Sentry DSN** (optional but recommended)
  - Set `SENTRY_DSN` env var; SDK auto-initializes
  - PII is never sent to Sentry (`send_default_pii` is off)

## Legal (requires attorney review before launch)

- [ ] **Attorney review of VA document templates**
  - Templates cite VA Code sections but are **attorney-authored drafts**
  - Recommend licensed VA attorney reviews all 4 templates before first client use
  - Templates: `services/api/content-packs/templates/us-va/v0/`

- [ ] **Multi-state pack review**
  - MD, DC, NC packs created with jurisdiction notes; question sets reference VA
  - Recommend attorney review per jurisdiction before enabling for clients

- [ ] **Terms of Service / Privacy Policy**
  - Required before public launch (not in scope for engineering sprints)

## Load Test

- [ ] **Basic load test**: 50 concurrent users on key flows
  - Family: signup → create matter → interview bootstrap
  - Attorney: login → review queue → packet view
  - Target: p95 < 3s, zero 5xx

## Backup & Recovery

- [ ] **Verify Supabase PITR** (Point-in-Time Recovery) is enabled
  - Supabase Dashboard → Database → Backups
  - Test restore to a staging project before launch

## Post-Launch

- [ ] Rotate the Supabase service key (was pasted in chat on 2026-10-02)
- [ ] Add `https://legacyflow.genalphalabs.com/**` to Supabase Auth redirect URLs
- [ ] Enable Stripe Connect when ready (see `STRIPE_SETUP.md`)
