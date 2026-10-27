# Self-Serve Provisioning + Jurisdiction-Default Routing — Implementation Plan (Phases 1–2)

**Approved model:** Venugopal, main chat 2026-10-27 — per state, LegacyFlow stands up at least one DEFAULT law firm; the public registers for that state, completes a plan self-serve with **no firm membership**, and at submit the matter routes to the default firm for that state. Later: multiple firms per state with routing choice (out of scope here; the registry below is the seam).
**Repo:** `~/workspace/legacyflow` · HEAD at design time `8d3d81f` · API: `services/api` · Web: `apps/web` · Migrations: `supabase/migrations/`
**Phase split:** Phase 1 = operational standup of the Virginia default firm on today's code. Phase 2 = the build (this doc's Decisions + Tasks), migration applied to production in the same run, then the production verification gate.

---

## Evidence (verified 2026-10-27, code + production DB)

- **F1 — The dead end is in `create_matter` itself.** `matters/service.create_matter` calls `authz.get_user_firm_id(actor)` first; a caller with no `firm_memberships` row gets 403 before any household check. `household/service.create_household` has the same gate. `POST /v1/matters` is the only matter-creation path. Stage D's "contact your firm" is the web modal (`apps/web/components/family/create-matter-modal.tsx`) mapping that 403.
- **F2 — Customers hold no firm role.** Production `firm_memberships` contains only `attorney` (5), `firm_admin` (5), `staff` (2). Family customers exist solely as `household_members` (`primary_client` etc.). Every prior "family" run used dual-hat accounts (firm member + household member). The cohort account `demo.cohort@legacyflow.app` (`ff883172-6b48-426d-bc8c-786eb5d927c2`) has zero firm memberships and zero household memberships.
- **F3 — The schema is firm-anchored.** `households.firm_id` and `matters.firm_id` are `NOT NULL` (migration `20260926220000`); 68 call sites read `matter["firm_id"]`; templates, packages, packets, payouts are all firm-scoped. A nullable-firm model is a rewrite of the document pipeline, not a routing change.
- **F4 — Jurisdictions.** DB `jurisdictions`/`jurisdiction_packs`: only `US-VA` (usable) and `US-XZ` (synthetic, usable) have rows. MD/DC/NC have file packs but no DB rows → `UnknownJurisdictionError` today. US-XZ is therefore the perfect negative case: pack resolves, no default firm registered.
- **F5 — Flags.** Precedence env > firm > jurisdiction (`pack_id`) > environment > default; `document_assembly` default is `False`. The Railway env override was deleted 2026-10-27; firm rows exist only for Demo + E2E. Pack id for VA matters is `us-va` (manifest v2, semantic_version `2.0.0`, snapshot jurisdiction_code `US-VA`).
- **F6 — Signup.** Profiles are created by the `handle_new_user` trigger (`20261001130000_auth_profile_trigger.sql`); self-serve signup demonstrably works (Stage D accounts exist and can log in). Nothing else is provisioned at signup.
- **F7 — Templates.** Canonical seed = `services/api/seed_va_templates.py`: 7 templates (will, poa, advance_directive, guardianship, trust, codicil, trust_amendment), `pack_id='us-va'`, content `{"body": <file text>}` from `content-packs/templates/us-va/v0/`. Demo's live rows are the 5 core types (codicil/amendment were never copied there); the fresh-plan draft set (D2 of the 2026-10-26 plan) never includes codicil/trust_amendment. The new firm gets all 7 from the canonical script source.
- **F8 — Attorney enrollment precedent** (2026-10-26 plan, D1): Management SQL writes byte-equivalent to `attorneys/service.approve_application` — one `attorney_applications` row (approved, reviewed_by = the firm's admin) + `firm_memberships` upsert (role `attorney`, active) — because the dummy attorney's login password is not on file. `find_routable_attorney` needs exactly that pair + the jurisdiction in the application's jurisdictions array.
- **F9 — Web household discovery.** Family home derives `householdId` from `matters[0].household`; a fresh user has none → modal tries `POST /households` → 403 → dead-end copy. The modal is the only web change needed.
- **F10 — Submit already routes.** `documents/service.submit_for_attorney_review` resolves `find_routable_attorney(matter.firm_id, jurisdiction)`, assigns, freezes the packet after assignment, transitions to `ATTORNEY_REVIEW`, and 409s with `NoAttorneyAvailableError` when the firm has no routable attorney. No submit-path change is needed under D1.

## Phase 1 — Virginia default firm standup (operational, today's code)

- **P1.1** Create a controlled admin account `va.admin@legacyflow.app` (Supabase signup; profile via trigger). This account's only firm will be the new firm, so `get_user_firm_id` resolves unambiguously for flag/admin calls (the multi-firm first-row ambiguity is why the recording account is NOT reused as this firm's admin).
- **P1.2** `POST /v1/firms` as that admin → legal name `Virginia Default Firm PLLC`, display name `Virginia Default Firm`. Creator becomes `firm_admin`.
- **P1.3** Enroll the dummy VA attorney (`5916a677-97cf-43a3-90ff-0a655692721a`) per F8: application row (full_name `E2E Attorney Test`, email `e2e.interview.20260928.b@example.com`, bar `VA-99999-E2E`, jurisdictions `["US-VA"]`, status `approved`, reviewed_by = P1.1 admin) + attorney membership upsert. Verify as admin via `GET /v1/attorney-applications` (approved row visible) and a SQL routability check mirroring `find_routable_attorney`'s query.
- **P1.4** Seed the 7 templates (F7): Management SQL inserts generated from the same v0 files the seed script reads, `created_by_user_id` = P1.1 admin. Verify: 7 rows, correct types, and `length(content::text)` equal to Demo's rows for the 5 shared types.
- **P1.5** `PUT /v1/admin/feature-flags/document_assembly {"is_enabled": true}` as the P1.1 admin → verify the effective read shows `source: "firm"`, enabled.
- **P1.6** Attach the cohort account: SQL rows mirroring `household/repository.insert_household` + `insert_household_member` — household in the new firm (display name `Cohort Demo Household`, created_by = admin) + member row (user `ff883172…`, `primary_client`, `active`, invited_by = admin). Verify as cohort: `GET /v1/matters` returns 200 via the household path. **The create-a-matter proof for the cohort account lands in Phase 2's verification** — under today's code a household-only user still hits F1; Phase 2 is precisely what removes that, and the cohort account (pre-existing, firmless, already attached) is a named Phase 2 verification subject.
- **P1.7** Plumbing proof as admin (API only): `POST /households` → `POST /matters` (US-VA) → `POST /matters/{id}/interview/sessions` → `GET /matters/{id}/draft-documents/state` reports `enabled: true`. Cleanup: scratch matter deleted FK-safe (matter_id tables enumerated from `information_schema`), scratch household rows deleted; counts verified.

## Phase 2 — Decisions (D1–D8)

### D1 — Anchor at creation to the jurisdiction's default firm (recorded deviation)

A self-serve matter is anchored to the default firm **at creation**, not at submit: the household is provisioned under that firm and the matter is created in it, exactly like a firm-originated matter. The family never holds a firm membership — only a `household_members` row — so the user-visible contract is unchanged: register → pick Virginia → complete the plan → submit → the default firm's attorney is assigned.

Why not literal submit-time attachment (nullable `firm_id` until submit): F3. Templates, packages, the flag gate, the interview, and the render context are all firm-scoped from the first draft; a firm-less matter would need a parallel resolution rule in every one of them, plus a schema migration on the two hottest tables. The deviation's costs, accepted knowingly: (a) the default firm's staff can see the in-progress matter in their firm list before submit — the same visibility they have over every household in their firm today, and operationally normal for a default firm that will receive the matter; (b) if a jurisdiction's default firm changes mid-flight, in-flight matters stay with the original firm (they were provisioned under it; re-anchoring rendered packages across firms is explicitly out of scope and becomes a routing concern when a second firm per state exists).

The compensating benefit: no-default-firm jurisdictions fail **at plan start** (D6), before a family invests an hour in an interview — a strictly better failure point than Venugopal's submit-time sketch, and the reason this deviation is safe to take without re-asking.

### D2 — Registry: `jurisdiction_default_firms` table

New table: `jurisdiction_code text PRIMARY KEY` (e.g. `US-VA`), `firm_id uuid NOT NULL REFERENCES firms(id)`, `created_at`, `updated_at`. RLS **enabled with no policies** (backend-only table; standing rule — owner connections bypass, API roles default-deny). One default per jurisdiction is enforced by the PK; a firm may default for several jurisdictions. Registration is an operational act (SQL, reviewed) in this build — no admin UI (D8). Resolution helper: `matters/repository.get_default_firm_id(conn, jurisdiction_code) -> UUID | None`.

### D3 — Household provenance + find-or-create rule

`households.origin text NOT NULL DEFAULT 'firm'` with CHECK `origin IN ('firm','self_serve')`. Self-serve find-or-create keys on `(created_by_user_id = caller, firm_id = default firm, origin = 'self_serve')` and reuses the household only when the caller's member row there is `primary_client` + `active`; otherwise it creates a fresh one (display name from the request's `household_name`, else `My Household` — the web modal's existing default). Per-(user, default firm) keying means a second state opening later gets its own household under its own default firm, and a default-firm change never strands a user's existing plans.

### D4 — `create_matter` contract

`MatterCreate.household_id` becomes optional; new optional `household_name` (self-serve path only).

- **household_id present** (existing clients unchanged in shape): the firm is resolved **from the household row**, not from the caller's memberships. Authorize when the caller is staff of the household's firm (STAFF_ROLES, as today) **or** an active household member with role `primary_client`/`collaborator`. Non-member, non-staff callers get 404 (existence-oracle, unchanged); read-only members (dependent/trusted_contact) get 403. This is a strict widening along the household axis the rest of the API already honors (`get_matter_scope`, `require_matter_write_scope`, `list_matters`' household path) — it fixes the latent inconsistency that a household-only user could read and write everything about a matter except create one. For every legitimate current caller the household's firm equals their own firm, so staff behavior is byte-identical.
- **household_id omitted** → self-serve path, uniform for all callers (including dual-hat accounts — the recording/demo accounts must not dead-end on their first run; staff on-behalf creation always names a household explicitly in every existing client). Sequence: split + resolve the jurisdiction pack (unknown → 422 `UnknownJurisdictionError`, unchanged) → resolve the default firm from the registry (none → D6 error) → find-or-create the household (D3) → insert the matter with the same snapshot, status history, and audit writes as today (audit `firm_id` = the default firm; audit metadata gains `origin: "self_serve"`).

### D5 — `document_assembly` gating for self-serve households: the anchor firm's firm-scope row

No new mechanism. Because the matter lives in the default firm from creation, the existing resolution (firm row → …) already applies; Phase 1 enables the VA default firm's row. **Deliberately NOT a jurisdiction-scope row:** jurisdiction scope is keyed by pack and would enable assembly for *every* firm's VA matters that lacks a firm row — silently reopening the global enablement Venugopal had removed from Railway the same day. Jurisdiction-scope enablement stays available as a future deliberate per-state rollout decision.

### D6 — Honest errors, both tested

- No default firm for a jurisdiction with a usable pack → **422** `NoDefaultFirmForJurisdictionError`, code `no_default_firm_for_jurisdiction`, detail naming the jurisdiction ("No law firm is currently accepting self-serve plans in US-XZ…"). Raised at matter creation (D1). Tested against US-XZ.
- Default firm exists but has no routable attorney → the existing submit-time **409** `NoAttorneyAvailableError` remains the guard. Tested by pointing a test matter at a default firm with no enrolled attorney.

### D7 — Web: the modal stops creating households for firmless users

`create-matter-modal.tsx`: when `householdId == null`, skip `POST /households` and call `POST /matters` without `household_id`, passing the entered name as `household_name`. `api-client`: `MatterCreateInput.household_id` optional + `household_name?: string`. The 403 "contact your firm" copy stays for genuine permission failures; the no-default-firm 422 surfaces the server's specific message via the existing non-403 branch. The pinned modal test is updated to the new contract; a 403 test is kept.

### D8 — Registry administration is operational in this build

Registering/changing a default firm = a reviewed SQL insert (Phase 2 registers `US-VA` → the Phase 1 firm as its final task). Surfacing the registry in the existing `admin/jurisdictions` web page is a named follow-up, not part of this build.

## Phase 2 — Tasks (TDD: failing tests first, per task)

- **T1 — Migration** `20261027150000_self_serve_provisioning.sql`: registry table (D2, RLS on) + `households.origin` (D3) + supporting index. Applied to production via Management SQL in the same run as the merge; objects verified (table, column, CHECK, RLS) and all migration files fingerprinted against production.
- **T2 — API provisioning**: repository helpers (registry lookup; household find-or-create) + `create_matter` rework (D4) + schema change + `NoDefaultFirmForJurisdictionError`. New tests `tests/test_self_serve_provisioning.py` (pgserver harness modeled on `test_family_draft_documents.py`): firmless create (201, firm = default, household origin `self_serve`, caller primary_client) · second create reuses household · US-XZ → 422 with code · household-path create by primary_client (201), dependent (403), outsider (404), cross-firm household id (404) · staff create unchanged · dual-hat omitted-household create succeeds via self-serve.
- **T3 — Routing proof tests**: self-serve matter → Sharma-complete answers → generate (5) → submit → `ATTORNEY_REVIEW` + assigned = default firm's attorney + packet exists; default firm without attorney → submit 409. (Extends the T2 file or the family-draft harness.)
- **T4 — Web**: modal + api-client (D7) + vitest updates.
- **T5 — Registry seed**: insert `US-VA` → Phase 1 firm; verify by API (fresh firmless token creates a VA matter; US-XZ still 422s).
- **T6 — Production verification gate** (below), then cleanup.

## Partner provisioning procedure (Phase 1 deliverable — for when partner emails arrive)

Post-Phase-2, a partner needs **nothing** provisioned in advance:

1. Partner signs up at `https://legacyflow.genalphalabs.com` with their own email (profile is automatic).
2. Partner clicks **Start estate plan**, picks **Virginia**, and completes the plan — their household is auto-provisioned under the Virginia Default Firm and their submission routes to its enrolled attorney. No admin step exists in this path.
3. *Optional* pre-attachment (only if we want their household to exist before they start): as the VA firm admin, run the two Management SQL inserts mirroring P1.6 (household in the VA firm + `primary_client` member row for the partner's user id, looked up from `profiles` by email). Verify with step 4.
4. Verify (2 minutes): partner calls `GET /v1/matters` (200, household path), creates a plan, and the VA firm's queue (as `va.admin@legacyflow.app`) shows the matter after they submit.

## Production verification gate (Phase 2 completion — fresh evidence required)

1. **Fresh signup E2E**: brand-new email account, public signup, **zero manual provisioning** → `POST /v1/matters` (no household_id, US-VA) 201 → API-driven Sharma interview to 41/41 → draft state `enabled: true` → generate → exactly 5 packages → submit → `ATTORNEY_REVIEW`, `assigned_attorney_id` = dummy attorney `5916a677…` → as VA firm admin, the matter + packet are visible in the firm's queue → as the family account, matter + documents still read 200 after routing.
2. **Cohort proof**: the pre-attached cohort account creates a matter (self-serve path) — the Stage D dead end is dead for existing accounts too.
3. **Negative**: self-serve create for US-XZ → 422 `no_default_firm_for_jurisdiction`.
4. **Regression**: Demo-firm family flow still routes to the Demo firm's attorney (live spot check on the existing dual-path endpoints) · API suite and web suite run before/after with counts recorded; no new failures vs the pristine baselines (known environmental: `test_api_smoke.py`, full-suite pgserver pool pollution — members pass in isolation; web: `section-nav` 18-vs-12 pre-existing).
5. **Cleanup**: all scratch matters deleted FK-safe (matter_id tables enumerated from `information_schema` at runtime); fresh test accounts' households removed; protected matter `b9353a06…` and proof matter `2145ce67…` verified intact (status + package counts). Kept as shipped state: the VA firm, its templates, flag row, attorney enrollment, registry row, cohort household.

## Risks / open points

- The 60s in-process flag cache: flag PUTs invalidate the local cache entry, but a just-enabled firm row can read stale on another worker for ≤60s — verification steps wait or re-read until consistent (same posture as Stage 2 D7).
- `get_user_firm_id` remains first-row for multi-firm users everywhere else it is used (flags admin, household create) — pre-existing MVP invariant, untouched by this build; the VA admin account is deliberately single-firm.
- US-XZ's production pack row is `internal_testing` — the negative test depends on it staying resolvable; if it is ever retired, the negative case moves to a fixture-only test.

---

## Execution record (2026-10-27, completed same day)

- **Phase 1 DONE** (detail: run folder `phase1-log.md`): admin `va.admin@legacyflow.app` (`71a30680…`, single-firm by design); **Virginia Default Firm `9cda36d1-2c6a-410f-8bed-698b2cd14763`** created via API; dummy attorney enrolled (application `f494d8cc…`, routability verified with the exact `find_routable_attorney` query shape + API list); 7 templates seeded — the 5 shared bodies are md5-identical to Demo's production rows (the repo v0 trust file diverges from the production body in ALL firms — pre-existing hygiene item, flagged, not fixed); `document_assembly` enabled via firm row (before-read `false/default`, after-read `true/firm`); cohort attached (household `15c20f39…`, member `e508fa6b…`) with API-verified reads; plumbing proof (household → matter → interview session → draft state `enabled: true`) executed and cleaned FK-safe.
- **Phase 2 DONE**: branch `feat/self-serve-provisioning` (4 commits, all `vtati`-authored) → independent review **SHIP AFTER FIXES** (no Criticals; two test pins added: pack-before-registry ordering, D3 reuse guard; stale router docstring fixed) → fast-forward merged to main at `027f7ce` and pushed. Migration `20261027150000` applied to production BEFORE the code deploy (the new `insert_household` writes `origin`); objects verified (registry table + RLS on + 0 policies, `households.origin` + CHECK + index; all 18 existing households backfilled `firm`). Registry seeded: `US-VA → 9cda36d1…`.
- **Production verification: 21/21 PASS** (`verify_prod.py` + `verify_continue.py`, summaries in the run folder). Fresh signup with zero manual provisioning → self-serve create anchored to the VA firm (`origin=self_serve`) → Sharma interview 41/41 → draft state enabled → exactly 5 packages → submit → `ATTORNEY_REVIEW` with the dummy attorney assigned and packet `50779c3e…` frozen → firm-queue + firm-side packet visibility → family reads/packages intact after routing. Negatives: US-XZ → 422 `no_default_firm_for_jurisdiction`; US-QQ → 422 `unknown_jurisdiction`. Cohort: household-path create 403 → **201**, self-serve create 201. Demo regression spot check: proof matter draft state still `enabled: true`. (The interview leg needed one driver-side catch-all message for the guardian-address cluster — a harness-persona gap, not an app defect; the app asked for exactly the one missing fact.)
- **Regression**: new tests 13/13 in isolation; family-draft 23/23; firms_create 3/3. Full API suite FAILED sets **identical** branch vs main (27 = 27, zero diff); the full-suite pgserver setup-error class (293 errors on pristine main) is environmental and also errors the new file's tests in full-suite mode — they pass in isolation, as do the sibling harnesses'. Web: branch 139/140 vs main 138/139 — the only failure is the pre-existing `section-nav` (§5.5) on both; net +1 passing test. `test_slice2_access.py` errors 36× in isolation on BOTH versions (fixture depends on suite-level state) — its reversed behavior is pinned by the new file's household-path tests instead.
- **Cleanup**: all scratch matters/households and the fresh QA auth account deleted FK-safe (verified 0 remaining); protected `b9353a06…` and proof `2145ce67…` verified intact. Kept as shipped state: VA firm, templates, flag row, registry row, attorney enrollment, cohort household.
- **Reviewer follow-ups recorded, not blocking**: (1) find-or-create is not race-safe (no uniqueness on the D3 key — two concurrent first creates could provision two households; later creates pin to the oldest). A partial unique index is the follow-up fix. (2) Firmless non-member create now returns 404 where it previously returned 403 — the D4-intended existence-oracle behavior; noted here so the change is described accurately. (3) Collaborator-role create on the household path is granted by D4 but pinned only via primary_client tests.
