# LegacyFlow — Technical Design

**Status:** Current as of 2026-10-29 (main @ `9725fa2`). This document describes the
system as it exists in code today. Where a behavior is ambiguous or known-defective,
the document says so rather than smoothing it over.
**Companion document:** [AI Interview — Technical Design](ai-interview-technical-design.md)
covers the interview subsystem in depth; this document treats it at overview level.
**Supersedes:** the 2026-10-26 HTML technical overview (kept outside the repo), which
predates self-serve provisioning, the Attorney Reviewer, and the 2026-10-29 deep-review
remediation program.

## Table of contents

1. [Product overview](#1-product-overview)
2. [End-to-end user journey](#2-end-to-end-user-journey)
3. [System architecture](#3-system-architecture)
4. [Codebase map](#4-codebase-map)
5. [Data model](#5-data-model)
6. [Authorization model](#6-authorization-model)
7. [AI agents — overview](#7-ai-agents--overview)
8. [Document pipeline](#8-document-pipeline)
9. [Cross-cutting concerns](#9-cross-cutting-concerns)
10. [Testing and verification](#10-testing-and-verification)
11. [Known limitations and deferred programs](#11-known-limitations-and-deferred-programs)

---

## 1. Product overview

LegacyFlow is an estate-planning platform that connects the public directly to law
firms. A household completes a guided, AI-assisted intake for its state; the platform
turns those answers into a draft estate-plan document package (will, powers of
attorney, advance directive, guardianship nominations, revocable living trust as
applicable); a licensed attorney at the responsible firm reviews, approves, and
releases clean final documents; the family then executes them against a generated
signing checklist.

Two rules shape the whole design:

- **The platform never practices law.** Interview copy, AI agents, and generated
  drafts all present themselves as information-gathering and pre-review. Drafts are
  watermarked `DRAFT — NOT LEGALLY VALID` until an attorney releases production
  documents through an explicit gate. AI findings are advisory and can never block
  or approve a gate.
- **Legal content is data, not code.** Everything jurisdiction-specific — questions,
  escalation rules, execution requirements, document templates, funding playbooks —
  lives in versioned *content packs* (one per jurisdiction), so a new state is a
  content exercise, not an engineering one.

The go-to-market model is **per-state default firms**: each served state has at
least one designated default law firm. A member of the public registers, states
their jurisdiction, and their work is anchored to that state's default firm from
the moment they create a plan. Virginia (`US-VA`) is the live state; the
"Virginia Default Firm" is its default firm.

## 2. End-to-end user journey

| # | Step | What happens | Implemented by |
|---|------|--------------|----------------|
| 1 | Public signup | Visitor creates a Supabase Auth account (email/password) on the web app. No firm affiliation is required or granted. | `apps/web` auth flows; Supabase Auth; `services/api/app/domains/identity` (profile read) |
| 2 | Create a plan (self-serve) | The user names a plan and picks a jurisdiction. The API resolves the jurisdiction pack (unknown jurisdiction → `422 unknown_jurisdiction`), looks up the state's default firm in `jurisdiction_default_firms` (none → `422 no_default_firm_for_jurisdiction`), find-or-creates the user's self-serve household under that firm, and creates the matter anchored to the firm with a snapshot of the pack it bound to. The user holds *no* firm membership — only a household membership (`primary_client`). | `services/api/app/domains/matters/service.py` (`create_matter`), `domains/household/repository.py` (`find_or_create_self_serve_household`), migration `20261027150000` |
| 3 | Intake — structured wizard | The family answers the pack's questions section by section (21 sections in the current VA pack). Answers save to `intake_answers`; saving reconciles people and relationships (testator/spouse/children/nominees) into `persons`/`relationships`. Questions can be parked and resumed. Progress is computed from the pack's `applies_if` gates, so only *applicable* required questions count. | `domains/intake` (`question_sets.py`, `service.py`), web intake wizard (`apps/web`) |
| 4 | Intake — AI interview (optional, parallel) | A conversational interviewer asks only for genuine gaps, extracts answers from free text and uploaded documents, and writes the same `intake_answers` through proposals. See the companion document. | `domains/interview`, `domains/ai` |
| 5 | Escalation screening | Pack escalation rules evaluate over the answers; findings write to `escalations`. A `blocking` finding moves the matter to `ESCALATED` for attorney disposition and blocks submission until dispositioned. | `domains/escalation`, pack `escalation-rules.yaml` (validated at load/import against the single vocabulary owner, `legal_content/pack_loader.py`) |
| 6 | Review & draft generation | On the family review screen, "Create draft documents" resolves which documents apply (from pack gates over the answers), renders each from the firm's seeded templates, and stores watermarked DOCX/PDF versions as a document package stamped with a SHA-256 `source_fingerprint` of the answers it was rendered from. Generating also auto-runs the Attorney Reviewer's deterministic layer on the frozen package. | `domains/documents` (`service.py`, `draft_set.py`), `domains/attorney_review` |
| 7 | Submit | Submission requires: all applicable required questions answered, no open `blocking` escalations, and — if drafts exist — a package whose fingerprint still matches the current answers (stale drafts → `409` until regenerated). The frozen review packet is built at submission; the matter moves `INTAKE_IN_PROGRESS → INTAKE_SUBMITTED → ATTORNEY_REVIEW`, an attorney at the anchor firm is assigned, and the full Attorney Reviewer run (deterministic + model layer) fires. | `domains/matters/submit.py`, `domains/review` (packet), `domains/attorneys` (`find_routable_attorney`) |
| 8 | Attorney review | Firm staff work the matter queue: review the packet, read Attorney Reviewer findings (advisory), request clarifications (matter → `CLARIFICATION_REQUESTED`, family responds, back to `ATTORNEY_REVIEW`), and decide gates. The `intake_approval` gate is required for `ATTORNEY_REVIEW → APPROVED_FOR_ASSEMBLY`. | `domains/review` (gates, clarifications, comments), `domains/matters/state_machine.py` |
| 9 | Release | With a `document_release` gate approved, `release_production` re-renders the package *without* the draft watermark, re-checking the fingerprint first. Matter → `DOCUMENTS_RELEASED`. | `domains/documents/service.py` (`release_production`) |
| 10 | Signing & funding | The system generates a signing checklist from the pack's execution profile (witnesses/notary per document); the family attests each requirement (the API stamps `attested_at` server-side). Funding tasks (retitling assets into the trust) are generated from the pack's funding playbooks. Matter progresses `SIGNING_IN_PROGRESS → FUNDING_IN_PROGRESS → PLAN_IMPLEMENTED`. | `domains/documents` (`generate_signing_checklist`, `record_attestation`, funding tasks) |

Matter lifecycle states (`domains/matters/state_machine.py`):
`DRAFT → INTAKE_IN_PROGRESS → INTAKE_SUBMITTED → ATTORNEY_REVIEW ⇄ CLARIFICATION_REQUESTED`,
with `ESCALATED` reachable from intake/submitted/review states, then
`APPROVED_FOR_ASSEMBLY → DOCUMENTS_RELEASED → SIGNING_IN_PROGRESS → FUNDING_IN_PROGRESS → PLAN_IMPLEMENTED`,
and `ARCHIVED` reachable from nearly every state. Transitions are validated against
an explicit table; two transitions additionally require gate records
(`intake_approval`, `document_release`).

Alongside the self-serve path, firms can also onboard households directly
(firm-created households, invitations, attorney applications and approvals in
`domains/attorneys` / `domains/invitations`); both paths converge on the same
matter model.

## 3. System architecture

```
                        ┌────────────────────────────┐
   Public / families ──▶│  Next.js web app            │  Vercel
   Firm staff ─────────▶│  legacyflow.genalphalabs.com│
                        └─────────────┬──────────────┘
                                      │ HTTPS, Supabase JWT (Bearer)
                                      ▼
                        ┌────────────────────────────┐
                        │  FastAPI API                │  Railway (service "api")
                        │  legacyflow-api             │  /v1/* routes, /healthz
                        │  .genalphalabs.com          │
                        └──────┬──────────────┬──────┘
                               │              │
              psycopg pool     │              │  HTTPS APIs
              (Supabase        ▼              ▼
               pooler)   ┌────────────┐  ┌──────────────────────┐
                        │ Supabase   │  │ OpenAI (chat,        │
                        │ Postgres + │  │ transcription, JSON) │
                        │ Auth +     │  │ Resend (email)       │
                        │ Storage    │  └──────────────────────┘
                        └──────▲─────┘
                               │ psycopg (public-schema reminder/
                               │ outbox tables)
                        ┌──────┴──────────────┐
                        │ Worker (Dramatiq/   │  Railway
                        │ Redis broker)       │
                        └─────────────────────┘
```

- **Web** — Next.js app in `apps/web`, deployed on Vercel at
  `legacyflow.genalphalabs.com`. All data access goes through the API; the web app
  holds no database credentials.
- **API** — FastAPI application in `services/api` (`app/main.py` is a thin
  composition root: middleware, RFC 7807 handlers, router wiring; business logic
  lives in `app/domains/*`). Deployed on Railway as the `api` service at
  `legacyflow-api.genalphalabs.com`. Routes are mounted under `/v1`; liveness is
  `GET /healthz`, which performs a real `SELECT 1` (Railway cannot route IPv6, so a
  bare "ok" would prove nothing — the DB check *is* the health signal; failure
  returns `503 {"status":"degraded"}` with detail kept in server logs).
- **Database access** — psycopg 3 with a process-wide pool
  (`app/core/db.py`: min 4 / max 20, `dict_row`, `prepare_threshold=None` because
  the Supabase pooler collides on auto-prepared statements). Repositories own SQL;
  services work through `db.transaction()` blocks. The API connects as a single
  pooled role — see §6 for what that means for tenant isolation.
- **Worker** — `services/worker`, a separate Railway service
  (`dramatiq worker.actors --processes 2 --threads 4`, Redis broker). Actors:
  the `notification_outbox` drain (`send_notifications`), due-reminder
  dispatch (`send_due_reminders`, `dispatch_due_reminders`), and uploaded-file
  storage scanning (`scan_file`), plus the dead-letter sink. It connects with
  `search_path=worker,public`. (The interview turn-body purge,
  `purge_expired_turn_bodies`, is implemented in the API but no worker actor
  invokes it yet — see the companion document §13.) Retries are bounded
  (3 attempts, exponential backoff, 1h max age) and exhausted work dead-letters
  to `worker_dead_letters` instead of stalling its queue.
- **Supabase** — Postgres (all application data), Auth (issues the JWTs the API
  verifies against the project JWKS), and Storage (file backend in production;
  `STORAGE_BACKEND=supabase`, vs `local` disk in dev/test).
- **External APIs** — OpenAI (chat completions, transcription, structured JSON —
  see §7 and the companion document) and Resend (transactional email;
  `noreply@mail.getshelf.link`-style per-product convention — for LegacyFlow the
  sending domain is `genalphalabs.com`).

**Deployment model.** `main` is the only long-lived branch. Every push to `main`
auto-deploys: Vercel builds the web app, Railway builds/deploys the API (and
worker). There is no separate staging environment for LegacyFlow production work;
test firms and test accounts inside production are the isolation mechanism, plus
the API/web test suites pre-push. A hard-won corollary, now a standing rule: **a
push is not a deploy** — web changes count as live only when the Vercel deployment
for the commit reads `READY` (on 2026-10-29 a lint failure silently ERRORed every
web deployment for ~5 hours while pushes kept "succeeding").

**Environments** are distinguished by configuration, not code: `API_ENV` /
`ENVIRONMENT` (`Settings.is_production` in `app/core/config.py` is the single
source of truth, true when either equals `production`, case-insensitive).
Production disables the interactive OpenAPI docs (`/docs`, `/openapi.json`,
`/redoc` are `None` in production — SEC-004) and minimizes the `/healthz` body
(SEC-006).

## 4. Codebase map

### 4.1 API domains (`services/api/app/domains/`)

22 domain packages, each following the same shape — `router.py` (HTTP +
orchestration only), `service.py` (business logic), `repository.py` (SQL),
`schemas.py` (pydantic models). All routers are aggregated in
`app/api/v1/router.py` under `/v1`.

| Domain | Responsibility |
|---|---|
| `ai` | Provider registry + grounded "Ask" Q&A over pack knowledge (`provider.py`, `service.py`) |
| `assets` | Household asset records feeding intake/funding |
| `attorney_review` | Attorney Reviewer agent: deterministic checks + model layer over frozen packages |
| `attorneys` | Attorney applications, approvals, bar/jurisdiction records, routable-attorney lookup |
| `audit` | Read access to the audit trail |
| `decisions` | Household planning decisions keyed by stable decision keys (values never audit-logged) |
| `documents` | Templates, packages/versions, rendering, release, signing checklists, attestations, funding tasks, vault shares |
| `escalation` | Pack-rule evaluation over answers; escalation lifecycle and disposition |
| `feature_flags` | Flag storage + resolution (`service.py` — see §6.4) |
| `files` | Uploads, storage backends, OCR/text extraction, vault document analysis |
| `firms` | Firm records and membership administration |
| `household` | Households, members, persons, relationships |
| `identity` | Caller profile (`/me`) from verified JWT claims |
| `intake` | Structured questionnaire: question sets, answers, progress, parked items, answer→person syncs |
| `interview` | AI interview: sessions, turns, proposals, extraction, dictation, consistency review |
| `invitations` | Firm/household invitation flows |
| `legal_content` | Content-pack loading and validation (`pack_loader.py`) — the single owner of pack vocabularies |
| `matters` | Matter CRUD, lifecycle state machine, submission, jurisdiction/default-firm resolution |
| `nominations` | Nomination records (executors, guardians, agents) captured during intake |
| `payouts` | Firm payout/billing records |
| `privacy` | Privacy/data-request surface |
| `review` | Attorney review workflow: packets, comments, clarifications, gates, pack import/lifecycle |

Shared infrastructure lives in `app/core/`: `config.py` (settings +
`MODEL_CAPABILITIES`), `db.py` (pool), `authz.py` (authorization helpers),
`errors.py` (RFC 7807 error types), `audit.py` (append-only audit writer),
`logging.py` (JSON logs + secret redaction), `idempotency.py`, `rate_limit.py`,
`security.py` (JWT verification), `uploads.py` (capped streaming reads),
`crypto.py` (Fernet turn-body encryption).

### 4.2 Web app (`apps/web`)

Next.js 15.5 (App Router, React 19, TypeScript, Tailwind v4). Auth is Supabase
Auth via `@supabase/ssr` browser/server clients; `middleware.ts` only checks
for the *presence* of the Supabase auth cookie (it does not validate the JWT —
the API does that on every call) and redirects unauthenticated app traffic to
`/login`. Route layout:

- **Public:** `/`, `/about`, `/attorney`, `/login`, `/signup`, `/vault/[token]`
  (tokenized vault-share access — the only token-authenticated surface).
- **Family app** (`app/(app)/matters/[matterId]/…`): dashboard, matters list,
  per-matter `intake` (structured wizard), `interview` (AI chat), `review`
  (review & drafts), `documents`, `signing`, `funding`, `vault`, `assets`,
  `household`, `attorneys`, plus `profile` and `settings`.
- **Firm side** (`app/(app)/firm/…`): work queue, intake-review, packets,
  escalations, templates, invitations, payouts, settings; **platform admin**
  (`app/(app)/admin/…`): firms, packs, users.

The intake wizard (`components/intake/intake-wizard.tsx`) is a two-pane layout —
section navigation (vertical sidebar on desktop; a sticky horizontal tab strip
on mobile since 2026-10-29) plus the active section's question card — with a
progress header. The interview chat
(`components/interview/interview-chat.tsx`) is a message thread with a
stick-to-bottom composer (see the companion document for its mobile contract).
Data access is plain `fetch` against the API through the typed module layer in
`lib/api/*.ts` (one module per domain: matters, intake, interview, documents,
review, …), attaching the Supabase access token. Tests: vitest + Testing
Library under `__tests__/` (212 tests / 48 files) and Playwright specs in `e2e/`
(auth setup, protected routes, family journey, interview v2).

### 4.3 Content packs (`services/api/content-packs/`)

One directory per jurisdiction (`us-va`, plus `us-md`, `us-dc`, `us-nc` in
preparation and a `us-xz-synthetic` test pack), with version directories
(`v0`, `v1`, `v2`) containing: `manifest.yaml` (pack id, semantic version,
`effective_from`, status), `question-sets/` (one YAML file per section — 21 in
us-va v2), `knowledge/` (grounding content for Ask/interview),
`escalation-rules.yaml`, `execution-profiles.yaml` (signing requirements per
document type), and `funding-playbooks.yaml`. Document template bodies live in
the sibling tree `content-packs/templates/<jurisdiction>/<version>/*.txt`
(13 templates for us-va) — see §8 for how repo files relate to per-firm seeded
template rows. `pack_loader.get_pack`
loads the highest version directory unless a version is pinned; matters snapshot
their pack (id, semantic version, question-set versions, rule ids) at creation
in `matters.jurisdiction_snapshot` and stay pinned to it.

---

## 5. Data model

Postgres (Supabase), single `public` schema, 55 application tables defined by the
34 files in `supabase/migrations/` (timestamp-named, applied in filename order).
There is **no `supabase_migrations` ledger in the project**, so the migration
*files* are the record and "was this applied?" is answered by fingerprinting
signature objects (tables/columns/indexes/constraints) against production —
see §9.5.

Conventions: `uuid` primary keys (`gen_random_uuid()`), `created_at`/`updated_at
timestamptz`, foreign keys to `profiles(id)` for user references, and soft-delete
columns on the aggregate roots where the domain supports deletion. Only the
load-bearing columns and constraints are listed below.

### 5.1 Tenancy and people

| Table | Purpose / key facts |
|---|---|
| `profiles` | One row per Supabase Auth user (id = auth user id); referenced by nearly every `*_user_id` FK. |
| `firms` | Law firms: `display_name`, `legal_name`. |
| `firm_memberships` | User↔firm: `role` ∈ `firm_admin`/`attorney`/`staff`/`auditor`, `status`; partial unique index keeps one live membership per (firm, user). |
| `attorney_applications` | Attorney onboarding: bar number, jurisdictions, target firm, `status` (`pending`/`approved`/`rejected`); approved rows drive `find_routable_attorney`. |
| `households` | Client households: `firm_id`, `display_name`, `created_by_user_id`, `origin` ∈ `firm`/`self_serve` (added by the self-serve migration). A household never confers firm membership. The partial unique index `households_self_serve_unique_idx` on `(created_by_user_id, firm_id) WHERE origin = 'self_serve'` makes find-or-create race-safe (FUNC-020). |
| `household_members` | User↔household: `relationship_role` ∈ `primary_client`/`collaborator`/`dependent`/`trusted_contact`, `access_status`, optional `person_id` link. Person-create through the family path accepts only `dependent`/`trusted_contact`; `primary_client`/`collaborator` are minted only by provisioning, invitation, or staff flows. |
| `persons` | People (testator, spouse, children, nominees): name parts, `date_of_birth`, contact/address fields. |
| `relationships` | Typed person↔person edges. The document renderer resolves the spouse **only** from this table (never from free-text answers). |
| `invitations` | Firm/household invitation records (token, expiry, status). |

### 5.2 Matters and intake

| Table | Purpose / key facts |
|---|---|
| `matters` | The unit of work: `firm_id`, `household_id`, `primary_jurisdiction_id`, `jurisdiction_pack_id`, `plan_type`, `status` (free text constrained by the application state machine, §2), `status_version` (optimistic concurrency), `title`, `jurisdiction_snapshot` (jsonb pin: pack id, jurisdiction code, semantic version, question-set versions, rule ids), `completeness_pct`, `assigned_attorney_id`, `submitted_at`, `current_step`. |
| `matter_status_history` | Status transitions with actor and reason. |
| `jurisdictions` / `jurisdiction_packs` | Jurisdiction registry and pack rows: `version`, `status` ∈ `draft`/`internal_testing`/`attorney_review`/`approved`/`retired`, `effective_from`/`effective_to`, `manifest` jsonb, `source_bundle_hash`, approver fields; unique (jurisdiction, version). Resolution orders intake-usable packs by `effective_from DESC`. |
| `jurisdiction_default_firms` | Self-serve registry (migration `20261027150000`): `jurisdiction_code` (PK, e.g. `US-VA`) → `firm_id`. Seeded `US-VA` → Virginia Default Firm. No admin UI yet; new states are added by SQL (known gap, §11). |
| `intake_answers` | One row per answered question: PK `(matter_id, question_key)`, `answer` jsonb. Deliberately minimal — provenance lives in `field_proposals` (§5.3), applicability in the pack definition. |
| `nominations` | Nominee records captured during intake (role, person link, priority). |
| `planning_decisions` | Household decisions by stable `decision_key` (unique per matter+key). Values are never written to audit logs. |
| `escalations` | Rule findings: `rule_id`, `severity` with CHECK `('info','review','attention','blocking')` (the `attention` value was added by migration `20261027170000` — see the case study in the companion document), `status` ∈ `open`/`resolved`/`dismissed`, `facts_snapshot`, disposition fields. |
| `assets` / `liabilities` / `asset_flags` | Household balance-sheet records feeding intake and funding. |

### 5.3 AI interview

| Table | Purpose / key facts |
|---|---|
| `interview_sessions` | `firm_id`, `matter_id`, `question_set_id`, `pack_version` (the pin), `status` ∈ `draft`/`in_progress`/`paused`/`completed`/`abandoned`, `completed_at`. Session creation fails closed if `INTERVIEW_TURN_ENCRYPTION_KEY` is unset — the interviewer refuses to start rather than store plaintext. |
| `interview_turns` | One row per turn: `role` ∈ `user`/`assistant`/`system`, `summary` (the redacted, permanent record), `citations` jsonb, `proposals_emitted`, `idempotency_key`, plus a sequence number (migration `20260928070000`). |
| `interview_turn_bodies` | The raw turn text, Fernet-encrypted (ADR-002), split from `interview_turns` so bodies can be purged (`INTERVIEW_TURN_RETENTION_DAYS`, default 90 days after a terminal matter state) while summaries/traces remain. |
| `field_proposals` | Model-proposed values with full provenance: `question_key`, `proposed_value`, `corrected_value`, `source` ∈ `user_stated`/`document_extracted`/`ai_inferred`, `confidence` (0–1), `source_document_id`/`source_page`/`source_snippet`, `model_id`, `prompt_version`, `pack_version`, `status` ∈ `proposed`/`confirmed`/`corrected`/`rejected`, decider + `decided_at`. How the current flows use these statuses is detailed in the companion document. |
| `interview_extractions` | Document-extraction runs against uploaded files (inputs/outputs for the extractor agent). |
| `interview_consistency_reviews` | Consistency Reviewer runs and their findings/escalation links. |
| `interview_parked_items` | Questions the family deferred ("parked") to return to later (migration `20261026140000`). |
| `ai_interactions` / `ai_traces` | Ask/AI interaction records and trace hashes (question + pack version + cited doc ids) for grounded-answer auditability. |

### 5.4 Documents and review

| Table | Purpose / key facts |
|---|---|
| `document_templates` | Per-firm template bodies (`status` draft/approved/active/…); seeded from the jurisdiction pack at firm provisioning. The seeded rows — not the repo files — are what production renders (will/advance-directive bodies verified byte-identical to the repo files in Oct 2026; the repo `us-va/v0` trust template is a known divergence, §11). |
| `document_packages` | A rendered set for a matter. The base table is per-template (`template_id`, `status` ∈ `draft`/`demo_released`); later migrations add the render/snapshot fields the pipeline uses, including the `source_fingerprint` staleness stamp (SHA-256 over the source answers). |
| `document_versions` | Individual rendered documents (DOCX/PDF) per package, with storage pointer and format. |
| `signing_checklists` | One row per matter: `checklist` jsonb (items live inside the blob — there is no separate items table), `completed_at`/`completed_by_user_id`. Generated from the pack's execution profile. |
| `attestations` | Signing-requirement attestations; `attested_at` is server-stamped (a client-supplied value validates but is never stored — see §8). |
| `funding_tasks` | Asset-retitling tasks generated from pack funding playbooks, with evidence attachments; a unique constraint (migration `20261029140000`) prevents duplicate task generation. |
| `review_packets` | The frozen attorney-review packet built at submission (answers + document snapshot). |
| `comments` / `clarification_requests` | Firm↔family review conversation; clarifications drive the `CLARIFICATION_REQUESTED` loop. Clarification *responses* require write scope (SEC-012); comments are deliberately read-scope. |
| `approval_gates` | Gate records (`gate_type`, `status`, decider). The state machine requires `intake_approval` and `document_release` gates for the two release-path transitions. |
| `attorney_review_runs` / `attorney_review_findings` / `attorney_review_snapshots` / `attorney_review_sources` | Attorney Reviewer persistence (migration `20261027160000`): runs over a frozen package fingerprint; findings (severities `should_fix`/`question_for_attorney`) whose acknowledgement is a *disposition on the finding itself* (`disposition` ∈ `open`/`acknowledged`/`fixed_by_regeneration`/`stale`, plus `acknowledged_by_user_id`, `acknowledgment_note`, `acknowledged_at`); render-time snapshots and source payloads the reviewer analyzes — it judges the frozen artifact, never live answers. Run-outcome columns were added by `20261029153000`. |
| `amendment_requests` / `case_requests` | Post-release amendment requests (gated on post-release status, deduped while pending — FUNC-011) and inbound case requests. |

### 5.5 Platform

| Table | Purpose / key facts |
|---|---|
| `feature_flags` | Scoped flag rows: `key`, `scope` ∈ `firm`/`jurisdiction`/`environment`, `scope_ref`, `is_enabled`. Resolution in §6.5. |
| `audit_events` | Append-only audit trail (the writer module only INSERTs; payloads are summaries — never raw plan content, documents, credentials, or prompts). |
| `file_uploads` | Upload records: storage pointer, MIME (sniffed from magic bytes server-side — SEC-011), scan status (`pending` on entry; funding evidence enters the scan pipeline the same way), content `sha256` (migration `20261029130000` — evidence downloads verify against the stored hash instead of re-downloading whole files, PERF-003). |
| `vault_shares` | Tokenized read shares of the family vault (hashed tokens). |
| `reminders` / `notification_outbox` / `notification_preferences` | Worker-consumed tables (public schema): scheduled reminders (partial unique index against duplicate active reminders, `20261029150000`), the email outbox drained with per-row savepoint isolation, and per-user notification preferences. |
| `worker_dead_letters` | Quarantined worker rows (migration `20261029143000`): a poison row dead-letters with its error instead of stalling its queue (ERR-002/003). |
| `data_deletion_requests` | Privacy/deletion request tracking (`domains/privacy`). |
| `payouts` | Firm payout/billing records. |

### 5.6 RLS posture

Row Level Security is **enabled on the application tables** (the foundation and
slice migrations enable it table-by-table, and the 2026-10-20 sweep verified
32/32 tables in the app schema at that time; every migration since follows the
standing rule that a new table enables RLS in its creating migration), with
policies only where PostgREST/API-role access is genuinely intended; most
backend tables have RLS on with zero policies (pooled owner connections bypass,
API roles default-deny). Important nuance: RLS is defense-in-depth for direct
database/API-role access; it does **not** isolate tenants for API traffic,
because the API connects as one pooled role. Tenant isolation for API requests
is application-level — see §6.3.

## 6. Authorization model

### 6.1 Authentication

Supabase Auth issues JWTs; the API verifies them against the project JWKS
(`SUPABASE_JWKS_URL`, defaulting to `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`,
audience `authenticated`) in `app/core/security.py`. The verified `sub` claim is
the caller's user id everywhere downstream. A `TEST_JWT_ISSUER`/`TEST_JWT_SECRET`
HS256 override exists for tests and **fails startup in production** (SEC-002,
enforced by a settings validator in `app/core/config.py`).

### 6.2 Roles and scope resolution

All authorization helpers live in `app/core/authz.py` and raise `AuthorizationError`
(403) or `NotFoundError` (404) — never return `None` on a denial path.

- **Firm roles** (`firm_memberships.role`): `firm_admin` (rank 4) > `attorney` (3)
  > `staff` (2) > `auditor` (1). `ATTORNEY_ROLES = {firm_admin, attorney}`;
  `STAFF_ROLES` adds `staff`.
- **Household roles** (`household_members.relationship_role`): `primary_client`,
  `collaborator` (write), `dependent`, `trusted_contact` (read-oriented).
- **Matter scope** — `get_matter_scope(conn, matter_id, user_id)` is the canonical
  resolution: access via an active firm membership in the matter's firm, or via an
  active household membership in the matter's household. Callers with neither get
  **404, not 403** (existence-oracle rule: a cross-tenant caller cannot distinguish
  "no such matter" from "not yours").
- **Firm resolution** — `get_user_firm_id` returns the caller's *oldest* active
  firm membership, deterministically (SEC-017: `ORDER BY created_at, id`); the MVP
  assumes one firm per user for firm-side surfaces.
- **Route coverage gate** — `services/api/tests/test_route_scope_coverage.py`
  enumerates the entire route table (147 routes) and fails if any handler does not
  transitively resolve a canonical `app.core.authz` scope. 13 routes are
  allowlisted with written justifications; the list is exact in both directions.
  This proves a scope is *resolved* on every route — not that it is resolved
  *correctly*; per-domain cross-tenant tests remain load-bearing.

### 6.3 Tenant isolation posture (SEC-003 — accepted risk, mitigated)

The API connects to Postgres with a **single pooled role**, so RLS never engages
for API traffic; tenant isolation rests entirely on the application-level scope
resolution above. This is a documented, accepted interim posture (deep review
SEC-003): the mitigations are the route-coverage gate, the existence-oracle rule,
composite FKs that make cross-matter references unrepresentable, and per-domain
cross-tenant tests. The full redesign — a least-privilege API role plus
per-request claim propagation so RLS engages — is deferred as its own program
with staging verification (§11). It must not be folded into unrelated fix batches:
a mistake in it fails either as a total outage or as a silent cross-tenant breach.

### 6.4 Platform administration

Platform-global actions — **firm creation** and **content-pack import/lifecycle**
— are authorized by `is_platform_admin` (`app/core/authz.py`), never by a firm
role. This split exists because of the deep review's top finding (SEC-008/009):
previously any authenticated user could create a firm, self-grant `firm_admin`,
and that role gated global pack administration — a registrant could have retired
or replaced the US-VA pack every new matter binds to.

- The allowlist is the `PLATFORM_ADMIN_USER_IDS` env var (comma-separated user
  UUIDs), set in production to the operator's own accounts.
- Empty allowlist + production ⇒ **nobody** is a platform admin (fail closed).
  Empty + non-production ⇒ any authenticated user (preserves dev/test flows).
- Imported packs are forced to `draft` regardless of the manifest's claimed
  status, and future `effective_from` values require platform approval — pack
  resolution orders by `effective_from DESC`, so an unvalidated future date
  would otherwise win resolution for every new matter.

### 6.5 Feature flags

`domains/feature_flags/service.py`. Resolution precedence (highest wins):
`LF_FLAG_<KEY>` env override > firm scope > jurisdiction scope > environment
scope > built-in default. Resolutions are cached in-process for 60s (flags are
read 2–3 times per interview turn). Known flags and defaults — note which fail
closed:

| Flag | Default | Meaning |
|---|---|---|
| `document_assembly` | **False** | Draft/final document generation. Must be explicitly enabled per firm. (A Railway env override once silently shadowed per-firm rows platform-wide; the override variable was deleted and per-firm rows are the source of truth.) |
| `ai_interviewer` | True | AI interview availability |
| `interview_v2` | **False** | Interview v2 path (see companion doc; now enabled globally via an environment-scope row) |
| `attorney_reviewer` | **False** | Attorney Reviewer agent (§7) |
| `ai_assistance` | True | Grounded Ask assistance |
| `funding_reminders` | True | Funding reminder emails |

Flag reads/writes via the admin API are `firm_admin`-only, and every write emits
a `feature_flags.updated` audit event. Unknown keys fail closed (`False`).

---

## 7. AI agents — overview

LegacyFlow has five AI agents. All model access goes through the provider layer
in `domains/ai/provider.py` (OpenAI-backed adapters in production;
deterministic dev providers in tests), and every call is shaped by the explicit
per-model payload contract table `MODEL_CAPABILITIES` in `app/core/config.py`
(§9.3). Document drafting is **not** AI: it is deterministic templating (§8).

| Agent | What it does | Model (config) | Where |
|---|---|---|---|
| **Interviewer** (v1 / v2) | Conversational intake: asks only genuine gaps, extracts answers from conversation into structured proposals/answers. v1: `gpt-4o`; v2: `gpt-5.5`. | `OPENAI_INTERVIEW_MODEL`, `OPENAI_INTERVIEW_MODEL_V2` | `domains/interview` — deep detail in the [companion document](ai-interview-technical-design.md) |
| **Document Extractor** | Pulls structured field values out of uploaded documents (and, in interview v2, out of conversational paragraphs) into `field_proposals` with provenance and confidence. | `gpt-5.5` on the v2 path; `gpt-4o` legacy | `domains/interview` extraction + `domains/files/extraction.py` |
| **Consistency Reviewer** | One fenced model call over a session's answers looking for contradictions; findings become escalations at severities `review`/`attention`. | `OPENAI_CONSISTENCY_MODEL` = `gpt-5.5` | `domains/interview/consistency.py` — companion document §9 |
| **Vault Document Analyst** | Classifies and summarizes documents a family uploads to its vault (type, key facts) so attorneys see an organized vault. | `OPENAI_CHAT_MODEL` (`gpt-4o-mini`) via `domains/files/analysis.py`, with a deterministic rules fallback when no model is configured | `domains/files/analysis.py` |
| **Attorney Reviewer** | Pre-review of the *frozen* draft package before an attorney opens it. Layer 1: seven deterministic checks (name consistency, role consistency, date/age arithmetic, pack completeness, unfilled placeholders — applicable questions only — VA execution requirements pinned to the matter's pack version, reference integrity). Layer 2: one fenced `gpt-5.5` call for free-text contradiction, implausible value, missing context, ambiguity. Severities are only `should_fix` / `question_for_attorney`; any model-suggested fix must be phrased as a question; and every model finding must quote the exact rendered span it relies on (verbatim-excerpt-or-discard). A model-layer failure or unparseable output degrades the run to deterministic findings with an explicit degraded marker — never a fake clean review. | `OPENAI_INTERVIEW_MODEL_V2` (`gpt-5.5`, layer 2 only, and only on full runs) | `domains/attorney_review` (`checks.py`, `model_review.py`, `service.py`) |

Attorney Reviewer invariants (by design, and proven in verification): it analyzes
**only** the frozen render-time snapshots, never live answers (one live read is
allowed — the current fingerprint — solely to avoid reviewing an orphaned
package); it auto-fires at draft generation (deterministic layer only) and at
submission (full run); per-matter run locks serialize overlapping runs; findings
surface on the family review screen and the firm packet tab; an attorney
**acknowledgement is a recorded override** that carries across regenerations by
finding content hash. It is **advisory only** — nothing in the module can block
generation, submission, or release (verified: a release completed with a finding
still open). UI copy calls it "Automated pre-review," never legal advice. The
flag `attorney_reviewer` is fail-closed and enabled per firm.

A sixth AI surface, **Ask** (`domains/ai/service.py`), answers family questions
grounded in pack knowledge via `GroundedTemplateProvider`: responses are
composed only from approved excerpts, and *sample* (attorney-pending) pack
content may ground an answer only when the deployment explicitly opts in
(`AI_ALLOW_SAMPLE_CONTENT`, default **False** — §9.3). Retrieval is mode-gated
by `AI_RETRIEVAL_MODE`: `lexical` (default; doc-level term frequency over
approved pack documents) or `hybrid` (chunk-level lexical ∪ pgvector embedding
candidates fused with RRF over the `knowledge_chunks` table, indexed from pack
files at startup by hash-diffed sync; any hybrid failure or empty index falls
back to lexical, so Ask availability never depends on the vector index).

## 8. Document pipeline

1. **Pack resolution.** Creating a matter resolves the jurisdiction's pack twice,
   deliberately: `matters/repository.py::resolve_jurisdiction_pack` picks the
   intake-usable DB pack row ordered by `effective_from DESC`, and
   `legal_content/pack_loader.py::get_pack` loads the pack content from disk
   (highest version directory, or a pinned version). The matter snapshots the
   result (`jurisdiction_snapshot`) and is pinned to it for life.
2. **Templates.** Rendering uses per-firm rows in `document_templates`, seeded
   from the pack at provisioning. `documents/service.py::_content_pack_files`
   reads pack-side YAML (execution profiles, funding playbooks) from disk with a
   manifest-semantic-version match (never directory-name matching) and falls back
   to built-in starter data with a logged warning when the pack is not on disk —
   it never crashes the API on a corrupt/missing pack.
3. **Draft set resolution.** `documents/draft_set.py` decides which documents a
   family gets from pack gates over their answers (e.g. trust documents only when
   the plan includes a trust). The Sharma reference case resolves to exactly five:
   will, financial POA, healthcare POA/advance directive, guardianship, trust
   (as applicable).
4. **Render context.** `build_template_context` (documents/service.py) assembles a
   flat string map: person ids resolve to names through an explicit slot table
   (`_ANSWER_SLOT_TABLE`); the spouse resolves **only** from the `relationships`
   row. Conditional legal prose is composed here, not in templates — the two
   composed slots added by the template-truthfulness fix (2026-10-27) are
   `family_identification` (married+spouse → "I am married to <name>."; married
   without a captured spouse → the attorney-visible `I am married to ___.`;
   single → "I am not married."; widowed → "I am widowed."; children block only
   when children are known) and `anatomical_gift_appointment` (emitted only when
   the free-text organ-donation answer is not a decline, per an interpreter with
   a 63-decline / 39-not-decline phrasing matrix).
5. **Rendering.** The renderer (`render_template_content`) is **pure string
   substitution** of `{{tokens}}` — no conditionals, no loops, no code execution.
   Unfilled tokens blank to `___`, and the render records which placeholders were
   left unfilled (`_flag_unfilled_placeholders`) so gaps are visible rather than
   silent. Output is DOCX and PDF; drafts carry the diagonal watermark
   `DRAFT — NOT LEGALLY VALID` and a draft header/footer.
6. **Fingerprinting.** Every package is stamped with `source_fingerprint`
   (SHA-256 over the source payload). Submission and release both re-compute and
   compare: a mismatch means the answers changed after rendering → `409` until
   the family regenerates. This is the staleness contract that keeps attorneys
   from ever reviewing documents rendered from superseded answers.
7. **Gates and release.** The state machine requires an `intake_approval` gate
   for `ATTORNEY_REVIEW → APPROVED_FOR_ASSEMBLY` and a `document_release` gate
   for `APPROVED_FOR_ASSEMBLY → DOCUMENTS_RELEASED`. `release_production`
   re-renders the package without watermark after the fingerprint check.
8. **Signing.** `generate_signing_checklist` builds the checklist from the pack's
   execution profile pinned to the matter's pack version. Attestation accepts the
   web shape `{requirement_id, acknowledgement}`; the API derives `statement_key`
   from the requirement and stamps `attested_at` server-side (a client-supplied
   `attested_at` validates but is never stored). The legacy
   `{statement_key, attested}` shape still works.

## 9. Cross-cutting concerns

### 9.1 Errors

All API errors are RFC 7807 problem details (`app/core/errors.py`):
`{type: "urn:legacyflow:error:<code>", title, status, detail, code, request_id}`.
A middleware assigns/propagates `X-Request-ID` and adds `X-Response-Time`.
Validation errors summarize **field locations only** — request values are never
echoed, because they may contain PII or plan content. Unhandled exceptions log
the full traceback server-side and return a generic 500 body. Domain errors are
typed subclasses with stable codes (e.g. `no_default_firm_for_jurisdiction`,
`intake_incomplete`, `blocking_escalations_open`, `attorney_review_disabled`).

### 9.2 Rate limiting, idempotency, uploads

- **Rate limits** (`app/core/rate_limit.py`): per-minute tiers — auth 10,
  upload/AI 30, default 120 — with `RATE_LIMIT_ENABLED` as an incident kill
  switch. The idempotency middleware sits *outside* the rate limiter so cheap
  replays bypass it.
- **Idempotency** (`app/core/idempotency.py`): `Idempotency-Key` on mutating
  requests; first definitive response (2xx/4xx) is recorded for 24h and replayed
  byte-for-byte (`Idempotent-Replayed: true`); a key reused with a different
  payload fingerprint → `422 idempotency_key_conflict`. Known limitation
  (ERR-013, deferred): the store is **per-process, in memory** — replays do not
  survive restarts or span instances. The web client sends no idempotency keys
  today, so the exposure is theoretical; the scale path is a shared store keyed
  the same way. Upload-class requests skip capture entirely and stream.
- **Uploads** (SEC-014): streaming capped reads with `413` at the files,
  documents, interview, and pack-bundle upload sites; `413` is refused from the
  `Content-Length` header alone when it exceeds the cap.

### 9.3 AI provider configuration

`AI_LLM_PROVIDER` / `AI_STT_PROVIDER` select adapters (`openai` in production,
`deterministic_dev` in tests — tests make no network calls). Model settings:
`OPENAI_CHAT_MODEL` (`gpt-4o-mini`), `OPENAI_INTERVIEW_MODEL` (`gpt-4o`),
`OPENAI_INTERVIEW_MODEL_V2` (`gpt-5.5`), `OPENAI_CONSISTENCY_MODEL` (`gpt-5.5`),
`OPENAI_WHISPER_MODEL` (`whisper-1`). Request shapes come from the explicit
`MODEL_CAPABILITIES` table in `app/core/config.py` — per-model contracts for
`temperature` (the gpt-5 family rejects it), the completion-token parameter name
(`max_completion_tokens`), and `reasoning_effort="none"` when function tools are
attached (a verified live 400 otherwise). The table replaced name-prefix routing:
an unlisted model resolves to a documented modern default, and dated snapshots
inherit their base model's contract.

### 9.4 Email — the outbox terminates at a sandbox (honest status)

Reminder and notification mail is queued in `notification_outbox` and drained
by the worker's `send_notifications` actor — but the drain's delivery target
today is a **file-based email sandbox**: each row is rendered to a JSON file in
a sandbox directory and marked `sent` (the worker settings state it plainly:
"Real email is never sent from this sandbox"). **No email provider is wired in
the codebase** — there is no Resend (or SMTP) client in the API, worker, or web
app, despite Resend being the portfolio's standard transactional provider and
the intended production target. Per-row failures retry up to
`NOTIFICATION_MAX_ATTEMPTS` and then mark the row `failed`; the drain isolates
rows with savepoints so one bad row cannot stall the batch. Wiring a real
provider into the drain is an open integration item (§11), and any document or
conversation that describes LegacyFlow email as "sent via Resend" is describing
the intention, not the code.

### 9.5 Persistence discipline

- **Savepoint isolation rule** (standing, generalized 2026-10-29 after the
  consistency-500 and ERR-016/019/021): any loop that persists model- or
  row-derived records inside a larger transaction isolates each row in a
  savepoint, so one bad row degrades to a skipped record + log line instead of
  rolling back the whole operation. It covers the escalation writers, both
  worker drains, and the Attorney Reviewer.
- **Vocabulary ownership:** persisted vocabularies (severities, statuses) are
  validated at pack load time and at import time against the single owner
  (`pack_loader.load_escalation_rules()`), not only by the database CHECK — the
  CHECK is the backstop, not the contract.
- **Audit:** `record_audit_event` (app/core/audit.py) writes append-only
  `audit_events` with before/after *summaries*. Payloads must never contain raw
  plan content, documents, credentials, or AI prompts (engineering contract
  rule 9).
- **Logging:** structured JSON logs with a secret-redaction filter
  (`app/core/logging.py`); the same rule 9 applies — no PII or plan content in
  logs (minors' names were removed from INFO logs in the deep-review program,
  ERR-017). Sentry is optional (`SENTRY_DSN`), initialized with
  `send_default_pii=False`, and can never break app startup.
- **Migrations:** every migration ships in the same change that needs it and is
  **applied to production via the Supabase Management SQL API in the same run** —
  a merged migration is *not* an applied migration (this exact gap bit twice
  before the rule existed). After any migration work, **all** migration files are
  fingerprinted against production (34 files / 185 signature objects verified
  2026-10-29). New tables enable RLS in their creating migration (§5.6).

## 10. Testing and verification

- **API suite** (`services/api/tests/`, pytest, ~110 test files): runs against a
  local Postgres (pgserver) with the real migration chain; provider calls are
  stubbed/deterministic. The sandbox harness has a known environmental failure
  class (pgserver/tmpfs pool pollution produces large setup-error counts in
  batch runs even on pristine main), so the comparison unit is the **failure
  set per file in isolation**, never raw counts. Post-deep-review state:
  1,716 passed with every remaining failure proven baseline-identical or
  adjudicated (mostly `test_interview*` files that exercise the live provider
  path and cannot go green in the sandbox).
- **Web suite** (`apps/web`, vitest): 212/212 after the tab-strip work, including
  component contract tests for the intake wizard (layout clamps that make the
  2026-10-29 mobile overflow unrepresentable, chip/strip behavior). The API
  side additionally carries the route-scope coverage gate (§6.2) as a test.
- **Worker suite:** 33/33.
- **Production E2E pattern:** after deploy (and after confirming the Vercel
  deployment is `READY` / the Railway deployment is active), a scratch-account
  run exercises the real flow — self-serve signup → jurisdiction-anchored matter
  → intake completion → five draft packages byte-verified (names in correct
  slots, watermark present) → submit → consistency review 200 → Attorney
  Reviewer completes → firm queue + signing checklist → mobile-viewport smoke —
  then FK-safe cleanup of all scratch rows and a protected-data integrity check
  (designated protected/proof matters are snapshotted before/after and must be
  byte-identical).
- **Review discipline:** every fix batch gets an independent diff review before
  it counts as done; completion claims require fresh verification evidence
  (re-run the proving command on the delivery tree and quote its output).

## 11. Known limitations and deferred programs

| Item | State |
|---|---|
| **RLS redesign (SEC-003)** | Deferred as its own program: least-privilege API role + per-request claim propagation so RLS engages for API traffic. Mitigated meanwhile by the route-coverage gate (§6.2/§6.3). |
| **Idempotency store (ERR-013)** | Per-process in-memory; shared-store migration deferred (web client sends no keys today). |
| **Pack cache invalidation (PERF-013/014)** | No general query cache; pack caches have no invalidation hook beyond the 60s flag TTL pattern. Deferred with rationale in the deep-review report. |
| **`whisper-1` removal** | OpenAI deprecated `whisper-1` on 2026-08-26 and removes it **2027-02-26**. Dictation must migrate to `gpt-transcribe` (or an equivalent) after a recorded-clip spot-check. Hard deadline. |
| **LLM challenger benchmark** | Deferred by the owner (2026-10-27): stay on OpenAI; challenger keys (Meta Model API, OpenRouter, Anthropic) to be provided later. Bar for any challenger: 0 wrong-source extractions, recall/precision ≥ the recorded-paragraph benchmark's .933. Note: `muse-spark-1.3-contributor` is disqualified for estate PII (trains on prompts/files/completions) — the `-contributor` id family is blocklisted. |
| **Jurisdiction registry admin UI** | Adding a state/default firm is SQL-only today (`jurisdiction_default_firms`). |
| **Email provider not wired** | The notification outbox drains to a file-based sandbox; no Resend/SMTP client exists in the codebase (§9.4). Reminder emails are rendered, not delivered. |
| **Repo vs production template divergence** | `content-packs/templates/us-va/v0/revocable-living-trust.txt` diverges from the seeded production bodies in all firms (hygiene ticket; production rows are authoritative). |
| **Household find-or-create race** | Mitigated by the partial unique index (`households_self_serve_creator_firm`, migration `20261028235900`); concurrent first-creates resolve via `ON CONFLICT … DO NOTHING` + re-read. |
| **Submit/assign serializer residue** | `submit`/`assign` responses still return `household_name=None` (the detail path is fixed); pre-existing, scoped out of the serializer fix. |
| **Progress-panel count quirk (S1)** | The interview panel can read 42 declared vs 41 applicable at true completion (display-only). Documented in the companion document, §7. |
