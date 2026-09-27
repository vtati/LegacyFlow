# Pilot Demo Script (synthetic data only)

A click-through walkthrough of the full LegacyFlow journey for a pilot
audience. Every step maps to a **real UI route** and the **real API endpoint**
behind it. All names, emails, and assets below are synthetic — never use real
client data.

**Setup:** start the stack locally (see `README.md`):
web at `http://localhost:3000`, API at `http://localhost:8000`.
With no Supabase keys configured the web runs in **mock-auth mode** — sign in
on `/login` creates a synthetic demo session (`lf_mock_session` cookie); no
real account is needed. Direct API calls need a JWT (`Authorization: Bearer …`):
in local dev set `TEST_JWT_ISSUER`/`TEST_JWT_SECRET` and mint an HS256 token,
or point the API at Supabase.

**Matter lifecycle reference** (`services/api/app/domains/matters/state_machine.py`):
`DRAFT → INTAKE_IN_PROGRESS → INTAKE_SUBMITTED → ATTORNEY_REVIEW →
CLARIFICATION_REQUESTED → ATTORNEY_REVIEW → APPROVED_FOR_ASSEMBLY →
DOCUMENTS_RELEASED → SIGNING_IN_PROGRESS → FUNDING_IN_PROGRESS →
PLAN_IMPLEMENTED` (archived / escalated branches omitted).

---

## 1. Firm admin imports a jurisdiction pack

There is no pack-import UI yet (admin pages are placeholders) — this is an API step.

```bash
curl -s -X POST http://localhost:8000/v1/admin/packs/import \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -F "pack_dir=$PWD/content-packs/us-va/v0" | jq .
# → {"pack_id":"us-va","jurisdiction_code":"US-VA","version":"0.1.0",
#    "status":"internal_testing","source_bundle_hash":"…","jurisdiction_id":"…"}
```

- Endpoint: `POST /v1/admin/packs/import` — `services/api/app/domains/review/router.py`
  (firm_admin only; accepts `pack_dir` or a `.zip` `bundle`, verifies the bundle hash).
- Verify: `GET /v1/packs` lists available packs and versions.
- Say: _"Legal content never ships as code — the Virginia pack is versioned,
  hashed, and auditable. A new statute means a new pack version, not a deploy."_

## 2. Firm admin invites a synthetic household

- UI: `/firm/team-settings` exists in the firm nav but is a placeholder page —
  use the API for the demo:
  `POST /v1/firms/invitations` `{"email":"client.a@example.test","role":"client","household_id":"…"}`
  (`services/api/app/domains/invitations/router.py`).
- In non-production the raw invite token is returned in the response
  (`ENVIRONMENT`/`config.py`); in production it is emailed instead.

## 3. Client accepts the invitation

- API: `POST /v1/invitations/accept` `{"token":"<raw-token>"}` (idempotent).
- The client now signs in on `/login` and lands on the family workspace.

## 4. Client completes intake (family → guardians → assets)

Firm staff first creates the matter + household:
`POST /v1/households` → `POST /v1/matters`
`{"household_id":"…","jurisdiction_code":"US-VA","title":"Tati Household Estate Plan","plan_type":"will"}`.

Then the client works through the wizard:

| UI route                    | API                                                                                | What to show                                                                                                             |
| --------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `/family/intake/[matterId]` | `GET /v1/matters/{id}/intake/question-sets`, `PUT /v1/matters/{id}/intake/answers` | Pack-driven question sets (household.yaml, guardians.yaml, assets.yaml from the imported pack — no hard-coded questions) |
| `/family/guardians`         | `POST /v1/matters/{id}/nominations`                                                | Nominate "Guardian One (synthetic)" for minor children                                                                   |
| `/family/assets`            | `POST /v1/households/{id}/assets`                                                  | Add a synthetic brokerage account + home; optional `POST …/evaluate-alignment` shows beneficiary-alignment flags         |
| `/family/liabilities`       | `POST /v1/households/{id}/liabilities`                                             | Add a synthetic mortgage                                                                                                 |

- Say: _"Every question, rule, and playbook comes from the jurisdiction pack —
  nothing about Virginia is hard-coded in the app."_

## 5. Client submits; attorney reviews

- Client: `POST /v1/matters/{id}/submit` → status `INTAKE_SUBMITTED`
  (`services/api/app/domains/matters/router.py`). A `matter.submitted`
  notification is queued for the firm.
- Attorney opens `/firm/matters/[id]` (tabs: Overview, Files, Documents,
  Signing, …) and:
  1. Creates a review packet: `POST /v1/matters/{id}/review-packets`
  2. Requests clarification: `POST /v1/matters/{id}/clarifications`
     `{"body":"Please confirm the successor guardian's full legal name."}`
     → status `CLARIFICATION_REQUESTED`
  3. Client responds: `POST /v1/clarifications/{id}/respond` → back to `ATTORNEY_REVIEW`
  4. Attorney approves the intake gate: `POST /v1/matters/{id}/gates`
     `{"gate_type":"intake_approval",…}` then `POST /v1/gates/{gate_id}/decide`
     `{"decision":"approve"}` → status `APPROVED_FOR_ASSEMBLY`
- Say: _"Nothing moves without an explicit, recorded attorney decision. The
  gate decision is an audited event, not a button that hopes for the best."_

## 6. Demo document generated (watermarked)

Demo assembly is fail-closed: enable it first.

```bash
curl -s -X PUT http://localhost:8000/v1/admin/feature-flags/document_assembly \
  -H "Authorization: Bearer $ADMIN_JWT" -H "Content-Type: application/json" \
  -d '{"is_enabled":true}'
```

Then:

```bash
# 1. create a document package
curl -s -X POST http://localhost:8000/v1/matters/$MID/document-packages \
  -H "Authorization: Bearer $ATTY_JWT" -H "Content-Type: application/json" \
  -d '{"template_id":"<template-uuid>"}' | jq .id
# 2. generate the DEMO rendering
curl -s -X POST http://localhost:8000/v1/packages/$PID/generate-demo \
  -H "Authorization: Bearer $ATTY_JWT" | jq .
# 3. download (authenticated; client file uploads use a separate
#    token-gated flow: POST /v1/files/{upload_id}/download-token then
#    GET /v1/files/download?token=…)
curl -s http://localhost:8000/v1/document-versions/$VID/download \
  -H "Authorization: Bearer $ATTY_JWT" -o demo-will.pdf
```

- Endpoints: `POST /v1/matters/{id}/document-packages`,
  `POST /v1/packages/{package_id}/generate-demo`,
  `GET /v1/document-versions/{version_id}/download`
  (`services/api/app/domains/documents/router.py`).
- UI: `/family/documents`.
- Open `demo-will.pdf` on screen: diagonal **"DEMO ONLY — NOT LEGALLY VALID"**
  watermark on every page. Production release (`POST …/release-production`)
  always returns 409 — it cannot be triggered by accident.
- Say: _"This is a demo artifact, not a will. Real documents require the
  firm's controlled release process, which is intentionally not available
  in this build."_

## 7. Signing attestation

- UI: `/family/signing` — the client checks the **non-certifying
  acknowledgement** box ("bookkeeping step — not a legal certification of execution").
- API: `POST /v1/matters/{id}/signing-checklists` →
  `POST /v1/signing-checklists/{checklist_id}/attest`.

## 8. Funding tasks

- Generate: `POST /v1/matters/{id}/funding-tasks/generate` (idempotent —
  playbooks × household assets).
- UI: `/family/funding` — task cards move `pending → in_progress → complete`
  (`PATCH /v1/funding-tasks/{task_id}`), evidence attaches via
  `POST /v1/funding-tasks/{task_id}/evidence`.
- Due-date nudges flow through the reminder system (see runbook).

## 9. Vault share

- UI: `/family/vault` → "Create share":
  `POST /v1/matters/{id}/vault-shares` `{"label":"Successor trustee","expires_at":"…"}`
  returns an `access_token` (shown once).
- Open the public link `http://localhost:3000/vault/[token]` in an incognito
  window: **no login required**, shows only the redacted summary
  (`matter_id`, `matter_status`, `matter_title`, `plan_type`,
  `jurisdiction_code` — no PII) via `POST /v1/vault/access`.
- Revoke: `POST /v1/vault-shares/{share_id}/revoke` — the public link stops working.
- Say: _"Bearer-token access with expiry and revocation — and the vault never
  exposes PII to an unauthenticated viewer."_

## 10. Close

- Show `/admin/security-audit` (placeholder) and note the real audit trail:
  `audit_events` rows for the demo (pack import, invitation, submission, gate
  decision, reminder dispatch) all carry `correlation_id` from the
  `X-Request-ID` header.
- Remind the audience: every person, asset, and dollar in this demo is
  synthetic (`@example.test` addresses, `US-XZ` synthetic jurisdiction
  available for tests). The pilot runs on synthetic data only.
