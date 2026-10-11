# Sunday Public Test — Readiness Checklist

**Event:** LegacyFlow staging link goes public Sunday evening (Oct 11) for viewers to
click through and "try to break it." Promised in the Oct 9 LinkedIn carousel
(caption closes with a follow CTA tied to the staging-link drop + post-mortem).
Full public release follows once everything is tied up.

**Status as of Sat Oct 10 ~9:10 PM ET:** BLOCKED — no public staging URL exists.

## Verified blocker (2026-10-11 ~01:10 UTC, via Google DNS-over-HTTPS)

- `staging.legacyflow.genalphalabs.com` → **NXDOMAIN** (does not exist)
- `legacyflow-api-staging.genalphalabs.com` → **NXDOMAIN** (does not exist)
- `legacyflow.genalphalabs.com` → alive (Vercel, 307) — production only

Repo state: no `staging` branch exists (only `main`). Last noted staging deploy was
Railway project `legacyflow-staging` on 2026-09-28 — freshness unverified.

## Unblock steps (in order)

1. **Web staging deploy** — create `staging` branch from `main` (or deploy current
   `main` to the staging target), add custom domain `staging.legacyflow.genalphalabs.com`
   in the Vercel project.
2. **Web DNS** — IONOS: CNAME `staging.legacyflow` → Vercel-assigned target
   (Vercel shows it on the domain-add screen; standard is `cname.vercel-dns.com`).
3. **API staging domain** — Railway `legacyflow-staging` service → add custom domain
   `legacyflow-api-staging.genalphalabs.com`; use the per-domain CNAME target from
   Railway's "Show DNS records" (NOT the service public domain).
4. **API DNS** — IONOS: CNAME `legacyflow-api-staging` → Railway target, plus the
   `_railway-verify` TXT record Railway shows (DealProof lesson: without the TXT,
   Railway never verifies).
5. **Health check** — `GET https://legacyflow-api-staging.genalphalabs.com/healthz`
   must return 200; web staging must load and reach the API (watch CORS origins —
   StudioOS lesson: fixed origin lists break on new deploy URLs; prefer a regex or
   explicit staging origin).
6. **Deploy freshness** — confirm the staging deploy includes the loop fixes, the
   nomination-sync fix, and the Family & Guardians crash fix (the fixes the
   re-recorded demo will show).
7. **Demo data** — staging DB seeded with synthetic packs (us-va + us-xz already
   seeded per docs); decide the tester path: open signup on staging, or a shared
   demo login. Staging signups create rows in the staging DB only — acceptable, but
   rate limiting should stay on.
8. **Re-recorded demo video** — the side chat is re-recording tonight (voice was
   -34 to -39 dB, script didn't match the screen). The final cut must be on the
   public surface before the link goes out.

## Open decisions (Venugopal)

- **Exact release time** Sunday evening (needed before anything is announced).
- **Sunday 9 AM ship post topic** — still unresolved: Will & Trust weekend-module
  ship post vs. LegacyFlow public-testing announcement. The LegacyFlow option
  needs the URL + release time locked first.
- **Feedback intake** — where do "try to break it" reports go? (Options: a simple
  form, a dedicated email, or GitHub issues on the private repo.)
- **Post-mortem** — the Oct 9 carousel promised a post-mortem after the drop;
  plan the follow-up post once the test window closes.

## Coordination notes

- The LinkedIn weekly calendar build runs **Sunday 6:39 PM ET** — it should account
  for the Sunday-evening test announcement (or the resolved ship-post topic).
- Per product standards, never expose raw `*.vercel.app` / `*.up.railway.app`
  URLs to viewers — the subdomains above are the public surface.
- Demo documents carry the "DEMO ONLY — NOT LEGALLY VALID" watermark (P3) and
  production document release is closed (409) — safe for public testers.
