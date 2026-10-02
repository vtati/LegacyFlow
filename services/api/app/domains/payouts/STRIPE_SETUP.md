# Stripe Connect Setup (planned)

Attorney payouts run as **manual bank transfers** in v1: the admin approves a
payout, then marks it paid with a transfer reference. This document covers
moving to automated Stripe Connect transfers later. Nothing here is required
for launch.

## Keys needed

| Env var               | Where to get it                                          |
|-----------------------|----------------------------------------------------------|
| `STRIPE_SECRET_KEY`   | Stripe Dashboard → Developers → API keys (secret key)    |
| `STRIPE_WEBHOOK_SECRET` | Stripe Dashboard → Developers → Webhooks (signing secret) |

Set them on the API service (Railway → legacyflow-staging → Variables).
`stripe_connect.is_configured()` gates every Stripe call; without keys the
module raises `StripeNotConfiguredError` and the manual flow keeps working.

## Rollout plan

1. **Create a Stripe account** (if none) and enable **Connect** with the
   Express account type.
2. **Onboard attorneys**: call `create_connected_account(email, full_name)` when
   an application is approved; store the returned `account_id` on the
   attorney's profile. Send the attorney the `onboarding_url` to complete
   identity verification and add their bank account.
3. **Pay out**: when the admin approves a payout, call
   `create_payout(account_id, amount_cents, description)` instead of (or in
   addition to) marking it paid manually. Store the returned `payout_id` on
   the payout row.
4. **Webhooks**: expose `POST /v1/webhooks/stripe` verifying
   `STRIPE_WEBHOOK_SECRET`; on `payout.paid` / `payout.failed`, update the
   payout row and record an audit event.
5. **Dashboard**: show `get_account_status()` on the attorney dashboard
   (onboarding incomplete → prompt to finish).

## Fee model (for the product decision)

- Family pays: $499 standard / $899 complex (proposed).
- Attorney receives: $150 standard / $300 complex
  (`LF_PAYOUT_STANDARD_CENTS` / `LF_PAYOUT_COMPLEX_CENTS`).
- Platform keeps the remainder (~30%).

Adjust the env vars; no code change needed.
