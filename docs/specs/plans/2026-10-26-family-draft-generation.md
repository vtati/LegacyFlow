# Family Self-Serve Draft Generation — Implementation Plan (Stage 1: design + decisions)

**Approved workflow:** Venugopal, main chat 2026-10-26 — at 100% of applicable required answers the family gets (1) a review screen (every answer grouped by section, shown as it will render, inline-editable incl. persons/nominations), (2) Create Draft Documents (set generated from the matter's jurisdiction pack; watermarked DOCX+PDF into the family Documents page), (3) Submit to Attorney (frozen review packet, matter → Attorney Review, routed to the enrolled dummy Virginia attorney), (4) attorney side on existing surfaces (Opportunities/assignment, Review Queue, `document_release` gate → release-production clean finals → signing checklist).
**Repo:** `~/workspace/legacyflow` · HEAD at design time `8edcd0a` · API: `services/api` · Web: `apps/web` · Migrations: `supabase/migrations/`
**Stage split:** Stage 2 = Parts 1–3 below (API → web → enrollment/flags). Stage 3 = Part 4 (E2E acceptance). Stretch S1 is separately gated and never blocks the feature.

---

## Decisions (D1–D9) with evidence

### D1 — Dummy Virginia attorney: EXISTS for the E2E firm; must be enrolled into the Demo Recording Firm in Stage 2

DB-verified 2026-10-26 (Supabase Management SQL, project `nlrxlpjkrmvubvsfepjs`):

- User `5916a677-97cf-43a3-90ff-0a655692721a` — profile "E2E Interview User B", email `e2e.interview.20260928.b@example.com`.
- Approved attorney applications on file (firm `c0010000-0000-0000-0000-000000000001`): id `99347cc6-7c3f-4d9d-8d0c-dc3f94df7eaa` ("E2E Attorney Test", bar `VA-99999-E2E`, jurisdictions `["US-VA"]`, status `approved`) and id `62a41773-858b-4563-9551-0d714bd2253d` ("E2E Attorney v2", bar `VA-88888-V2`, `["US-VA"]`, `approved`). Active `firm_memberships` row role `attorney` in firm `c0010000` (and in `c0010002`).
- **Gap:** the Demo Recording Firm `b7a9957e-aa67-4235-8ff9-2310468d92ff` has NO attorney member — its only membership is `test.recording@legacyflow.app` (`344a2959-98e7-4e03-89ce-de539c16a77f`, `firm_admin`). Assignment validates the assignee holds an active `attorney` membership in the matter's own firm (`matters/service.py` `assign_matter_attorney`: `get_firm_role(...) != "attorney"` → 422), so Demo-firm matters cannot be routed to the dummy until he is enrolled there.
- **Stage 2 seed (Task 3.1)** replicates exactly what `attorneys/service.py::approve_application` writes: (a) one `attorney_applications` row for firm `b7a9957e`, user `5916a677`, full_name `E2E Attorney Test`, email `e2e.interview.20260928.b@example.com`, jurisdictions `["US-VA"]`, bar_number `VA-99999-E2E`, status `approved`, reviewed_by `344a2959` (Demo firm_admin); (b) `firm_memberships` upsert (firm `b7a9957e`, user `5916a677`, role `attorney`, status `active`) — the same upsert `attorneys/repository.upsert_attorney_membership` performs. Seeded via the Management SQL runner because the real submit+approve flow needs the dummy's own JWT and his Supabase password is not on file (only user A's is, in the A/B harness); the SQL writes are byte-equivalent to the approval path's effects. The E2E firm needs nothing.
- Note: the dummy has no known login password, so Stage 3 attorney-side actions run as each firm's `firm_admin` test account — authz-equivalent by design (`ATTORNEY_ROLES = {firm_admin, attorney}` for release; gate decide allows attorney/firm_admin). Routing correctness is asserted on `assigned_attorney_id` + the packet snapshot, which is the substantive property.

### D2 — Generated set: 5 core types gated by pack answer signals; codicil + trust_amendment OUT for fresh plans

Deterministic set resolution over the matter's stored answers, mirroring the v2 pack's own `applies_if` conventions (`services/api/content-packs/us-va/v2/question-sets/`):

| template_type | Rule | Pack evidence |
|---|---|---|
| `will` | always included | core instrument; every v2 profile answers the will clusters |
| `poa` | included unless `wants_poa_document == false` | household.yaml declares `wants_poa_document`; poa.yaml dependents use `applies_if: wants_poa_document != false` |
| `advance_directive` | included unless `wants_healthcare_directive == false` | household.yaml declares `wants_healthcare_directive`; healthcare.yaml dependents use `!= false` |
| `guardianship` | included iff `has_minor_children == true` | household.yaml:42 gate; guardians.yaml questions all `applies_if: has_minor_children == true` |
| `trust` | included iff `wants_revocable_trust == true` | trust.yaml:11 gate question; all trust detail questions `applies_if: wants_revocable_trust == true` |
| `codicil` | NEVER for a fresh plan | amends an existing executed will; no executed will exists yet |
| `trust_amendment` | NEVER for a fresh plan | amends an existing executed trust; amendments already have their own flow (`POST /matters/{id}/amendment-requests`, `amendment_approval` gate) |

Full Sharma profile → exactly {will, poa, advance_directive, guardianship, trust} (matches the 5 packages generated in E2E #3). Fully pruned profile → {will}.

**Data defect found (must fix in Stage 2, Task 1.2):** in BOTH target firms the seeded rows carry wrong `template_type` values — "Advance Medical Directive (Virginia)" is typed `poa` and "Designation of Guardian for Minor Children (Virginia)" is typed `instruction_letter` (Demo firm rows `6f189dfb…`, `f94cd5da…`; same pattern in the E2E firm). The rows were firm-to-firm copies made while the API Literal allowed only 4 types; `seed_va_templates.py` itself has the correct types, and the DB CHECK (migration `20261006040000_allow_advance_directive_guardianship.sql`) allows all 8. Type-based selection is impossible until the rows are retyped. Companion code fix (Task 1.1): `DocumentTemplateType` in `documents/schemas.py` is still `Literal["will","trust","poa","instruction_letter"]`, and `_template_out` passes the raw DB value into it — retyping the rows without widening the Literal would make `GET /document-templates` 500 on validation. Widen the Literal to the DB set as part of the same change.

Template resolution per type: the firm's latest template of that type, preferring `pack_id` equal to the matter's snapshotted pack id (`us-va`), then `created_at DESC` (extends `documents/repository.latest_template_by_type`). If any selected type has no template in the firm → the whole generate fails closed with 422 `draft_template_missing` listing the types, before anything renders (codebase fail-closed posture; a silently skipped legal document is the worst outcome).

### D3 — Family API shape: three endpoints in the documents domain, reusing the package engine + packet core

New routes (documents router; all under the existing `/v1` prefix):

- `GET /matters/{matter_id}/draft-documents/state` → `DraftSetStateOut` — the review page's single read (shape below).
- `POST /matters/{matter_id}/draft-documents/generate` → 201 `DraftSetGenerateOut` — resolves the D2 set, find-or-creates one package per type (`repo.find_package_for_template` / `repo.insert_package`, the `generate_instruction_letter` pattern), renders watermarked DOCX+PDF per package through the shared render core extracted from `generate_demo` (Task 1.4), stamps each package's source fingerprint (D4).
- `POST /matters/{matter_id}/draft-documents/submit` → 200 `DraftSubmitOut` — the D6 orchestration.

**Authz decision (evidence-driven):** the endpoints authorize with `matters_service.require_matter_write_scope` semantics — firm staff+ OR household `primary_client`/`collaborator` — NOT household-only. Reason: `authz.get_matter_scope` resolves the firm path FIRST (`app/core/authz.py:93-127`), and the primary family test account (`test.recording@legacyflow.app`) is dual-hatted (Demo firm_admin + household primary_client), so a household-only check (the `generate_instruction_letter` precedent) would 403 the very account this flow serves. The codebase's own submit endpoint (`matters/submit.py`) already uses this dual-path write scope for the sibling action. Read-only household roles (dependent, trusted_contact) remain excluded; callers with no access get 404 (existence-oracle rule). Generate additionally enforces `_require_document_assembly` (flag + pack capability) and `_require_intake_complete`; state never raises the assembly error — it reports `enabled: false` so other firms' UX is untouched.

`DraftSetStateOut`:
```json
{
  "enabled": true,
  "matter_status": "INTAKE_IN_PROGRESS",
  "applicable": {"required_total": 41, "required_answered": 41, "complete": true, "missing_labels": []},
  "documents": [
    {"template_type": "will", "included": true, "signal": "always",
     "template_id": "…", "template_name": "Last Will and Testament (Virginia)",
     "package_id": "…", "package_status": "demo_released",
     "generated": true, "stale": false, "latest_version_at": "…"},
    {"template_type": "trust", "included": false, "signal": "wants_revocable_trust != true", "generated": false, "stale": false}
  ],
  "can_generate": true, "can_submit": false, "submit_blockers": ["drafts_missing"],
  "assigned_attorney": null,
  "latest_packet": null
}
```
Excluded types are listed with their signal so the family (and the E2E) can see WHY a document is out — determinism made visible. `submit_blockers` values: `not_complete`, `drafts_missing`, `drafts_stale`, `already_submitted`, `invalid_status`.

### D4 — Staleness: source-data fingerprint, one new column on `document_packages`, no new table

- `compute_source_fingerprint(conn, matter) -> str` (documents/service.py): sha256 hex of canonical JSON (`sort_keys`, compact separators, `default=str`) over the exact user-editable inputs the renderer consumes: `{matter_title, household_name, answers: {question_key: answer}, persons: sorted [id, full_name, kind, date_of_birth], nominations: sorted [nomination_type, person_id, priority, status]}`.
- Deliberately NOT the rendered context: `_render_context` injects a volatile `today` string (`documents/service.py:848`), so a context hash would flip daily with no edit. Source data changes only when the family (or staff) actually edits something that can change the output.
- Storage: `document_packages.source_fingerprint` (nullable text) — one `ALTER TABLE` migration (`supabase/migrations/20261026120000_document_packages_source_fingerprint.sql`), applied via the Management SQL runner in the same step and verified in `information_schema`. No new table, so the standing RLS-on-creation rule is not triggered (the table predates and is already RLS-on). Stamped by the shared render core on every demo render (staff path included — a staff re-render genuinely refreshes the drafts) and on release-production.
- Semantics: a package is `stale` when its stored fingerprint ≠ the current fingerprint. Set-level: submit requires every type in the CURRENT set to have a package with ≥1 demo version and `stale == false`; packages whose type fell out of the set after an edit are reported `included: false` (superseded drafts stay downloadable; they simply aren't part of the submission). State computes the current fingerprint once per call — three indexed reads, no caching needed at this scale.

### D5 — Review screen (web): `/family/review`, answers are the source of truth

- Route: `apps/web/app/(app)/family/review/page.tsx` — matter from `useMatters`/`MatterSelect` like My Plan and Documents (the family's mental model is one selected matter, not URL ids). Entry points: My Plan (next-step card when applicable-complete → "Review & create your drafts"), family home `nextStepFor` (same condition), Documents page header link. CTAs render only when `draft-documents/state.enabled` is true.
- Composition (top → bottom): matter header + StatusPill + completeness line (from `GET /intake/progress` overall — applicable numbers only, D8); **Answers by section** — iterate `GET /intake/question-sets` (sets → sections → questions with label/type/required) × `GET /intake/answers`, formatted as the documents will read them (person answers show the name string; booleans Yes/No; lists joined). Inline edit per question (text input / radio / select by question type), saved through the EXISTING `PUT /intake/answers` (api-client `saveAnswers`); every save re-fetches draft-set state so a stale banner appears the moment an edit lands after generation.
- **Persons & roles section:** persons from the household detail (existing client methods used by the guardians/person pages) with name-spelling edits via the EXISTING `PATCH /matters/{id}/persons/{person_id}`; nominations from `GET /matters/{id}/nominations` rendered resolved (type, priority, person name) and READ-ONLY — nominations materialize from person answers by design (the nomination sync), so the honest edit path is the answer/person edits above it; delete/re-add nomination endpoints exist for staff flows and are deliberately not surfaced here.
- **Draft panel:** the state endpoint's document list (included with per-type status; excluded with their signal), Create Draft Documents / Regenerate button (disabled unless `can_generate`), stale banner, Submit to Attorney button (disabled unless `can_submit`, blockers listed in plain language), and after submit a status panel (Attorney Review, packet version, assigned attorney name).

### D6 — Submit mechanics: one transaction — existing submit → assign dummy → packet → ATTORNEY_REVIEW

`POST .../draft-documents/submit` orchestrates inside the router's single `db.transaction()` (any failure rolls the whole thing back):

1. Family write scope (D3); status must be `INTAKE_IN_PROGRESS` or `INTAKE_SUBMITTED` (else 409 `already_submitted`/`invalid_status`).
2. Staleness gate (D4) → 409 `drafts_stale` / `drafts_missing`.
3. If `INTAKE_IN_PROGRESS`: call the EXISTING `matters/submit.py::submit_matter` (re-checks applicable completeness + no open blocking escalations; transitions to `INTAKE_SUBMITTED`; writes history + audits; enqueues the firm-staff `matter.submitted` notifications). If already `INTAKE_SUBMITTED`, skip — its preconditions were enforced when it ran.
4. **Assign the dummy VA attorney** (direct assignment, not the pool): resolve per the D1 rule generalized — the user with an APPROVED `attorney_applications` row for the matter's firm whose jurisdictions contain the matter's jurisdiction code (`US-VA`) AND an active `attorney` firm membership; earliest approval wins. Write via `matters/repository.set_assigned_attorney` + audit `matters.attorney_assigned` (mirrors `assign_matter_attorney`). No match → 409 `no_attorney_available`, whole submit rolls back. Why direct: the Opportunities page is a web-only request queue (unassigned matters in `INTAKE_SUBMITTED`/`ATTORNEY_REVIEW`/`CLARIFICATION_REQUESTED` that attorneys ask an admin to assign — two human steps, no claim API exists); direct assignment uses the existing `assigned_attorney_id` machinery, matches "routed to the dummy Virginia attorney" exactly, and the matter automatically leaves the Opportunities list (that page filters `!assigned_attorney_id`).
5. **Freeze the packet**: call the packet core extracted from `review/service.py::generate_packet` (Task 1.6) — `_build_snapshot` + `repo.insert_packet` at `repo.next_packet_version`, audit `review.packets.generated`, notes `"Family self-serve submission"`. Assignment happens BEFORE this step so the frozen snapshot's `assigned_attorney_id` shows the routed attorney. (The staff endpoint `POST /matters/{id}/review-packets` keeps its firm-staff authz and behavior; only the core is shared.)
6. **Transition** `INTAKE_SUBMITTED → ATTORNEY_REVIEW` (legal per `matters/state_machine.py` `_TRANSITIONS`; no gate required for this hop — gates guard the two LATER hops) via `repo.update_matter_status` + `repo.insert_status_history` (reason `family_submit_for_review`) + audit `matters.transitioned`, following `submit.py`'s internal pattern (the public transition endpoint is staff-only, so the orchestrator performs the same writes in-process, validated by `validate_transition`).

Attorney side thereafter is untouched existing machinery: Review Queue (`apps/web/app/(app)/firm/review-queue`) → create+decide `intake_approval` gate → transition to `APPROVED_FOR_ASSEMBLY` → create+decide `document_release` gate → `POST /packages/{id}/release-production` per package → transition to `DOCUMENTS_RELEASED` → `POST /matters/{id}/signing-checklists` → family reads `GET /matters/{id}/signing-checklist`.

### D7 — Flag enablement: per-firm via the audited admin API, two firms only

`PUT /v1/admin/feature-flags/document_assembly` with body `{"is_enabled": true}` (`feature_flags/router.py:36` → `service.set_firm_flag`, firm_admin of the CALLER's firm; writes a firm-scope row + `feature_flags.updated` audit). Run once per firm with that firm's admin credentials: Demo firm as `test.recording@legacyflow.app` (firm_admin of `b7a9957e`); E2E firm as `e2e.interview.20260928@example.com` (firm_admin of `c0010000`, password in the A/B harness file). Verify with `GET /v1/admin/feature-flags?pack_id=us-va` per firm: `document_assembly` must read `is_enabled: true, source: "firm"` — `source: "env"` would mean a Railway `LF_FLAG_DOCUMENT_ASSEMBLY` override exists and must be reported, not worked around. No jurisdiction-scope or environment-scope row is created; global enablement is out of scope. Caveat: resolutions cache in-process for 60s (`feature_flags/service.py`); `set_firm_flag` invalidates its own entry — allow up to a minute before E2E assertions if a stale read appears.

### D8 — Completeness gate: applicable-required everywhere; declared count never gates

Server side already complies and the family endpoints inherit it: `_require_intake_complete` and `submit_matter` both run `intake/question_sets.compute_progress` over the matter's snapshotted pack — pruned questions leave the denominator, pending ones count (fail-closed). Sharma unlocks at 41/41 applicable, never 42 declared. Web side: all numbers come from `GET /intake/progress` (`overall.required_answered` / `overall.required_total`) — the review page and CTAs must not count pack questions themselves.

**Stretch S1 (separately gated, in Part 5):** the known interview Progress-panel quirk — root cause pinned: `interview/service.py::_session_out` builds `SessionOut.checklist` from `qs.iter_questions(...)` with `required=bool(question.get("required"))` and never consults the applicability partition, so the panel (`apps/web/components/family/interview/interview-chat.tsx:527,758`) counts a pruned required item (e.g. `specific_bequests`) as "still needed" while `_progress` in the same payload says 100%. Fix: add `applicable: bool = True` to `ChecklistItemOut`, set it from the same partition used by `_progress` (pruned → `False`), and make the panel count `required && applicable && !answered`. API unit test (pruned gate profile) + web vitest for the panel string.

### D9 — E2E acceptance mapping: see Part 4 (each approved acceptance step ↔ its proof)

---

## Global constraints (every task inherits these)

- Commits authored `vtati <vtati@users.noreply.github.com>`; verify the author after each commit; every push to `main` auto-deploys (Vercel + Railway) — allow deploy time and verify live before probing.
- TDD per the repo discipline: failing test first, watch it fail, minimal implementation, green; an independent diff review per Part (fix Critical/Important) before the Part counts as done; no completion claim without fresh test output.
- API tests: `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY -u no_proxy -u NO_PROXY .venv/bin/python -m pytest <files> -q` from `services/api` (proxy vars unset or httpx breaks). DB-backed (pgserver) tests can run in this sandbox; the pre-existing failure baseline (byte-identical on pristine HEAD) is the comparison, not zero.
- Safety: never delete firm `b7a9957e-aa67-4235-8ff9-2310468d92ff` or matter `b9353a06-16b2-47e3-8967-8895d19ec471`; proof matter `2145ce67-bb55-4b23-9127-22654e2898a9` stays exactly as found. Supabase Management SQL: `python3 ~/workspace/custom-connectors/custom.supabase-management/bin/run_sql.py nlrxlpjkrmvubvsfepjs <file>` (returns only the LAST statement's rows — one statement per file when verification matters).
- Standing DB rule: every NEW table gets RLS enabled in its creating migration. This plan adds no new table (one column only).

---

## Part 1 — API (Stage 2)

### Task 1.1 — Widen `DocumentTemplateType` to the DB-allowed set
- Modify: `services/api/app/domains/documents/schemas.py` — `DocumentTemplateType = Literal["will", "trust", "poa", "advance_directive", "guardianship", "codicil", "trust_amendment", "instruction_letter"]` (exactly the `document_templates_template_type_check` set from migration `20261006040000`).
- Tests: unit — `_template_out` validates rows of each newly allowed type (parametrize at least `advance_directive`, `guardianship`, `codicil`); existing template tests in `tests/test_slice4_documents.py` stay green.

### Task 1.2 — Repair mistyped template rows (data, both target firms)
- Evidence: D2. For firms `b7a9957e…` and `c0010000…`: `UPDATE document_templates SET template_type='advance_directive' WHERE name='Advance Medical Directive (Virginia)' AND pack_id='us-va' AND template_type='poa'`; `UPDATE … SET template_type='guardianship' WHERE name='Designation of Guardian for Minor Children (Virginia)' AND pack_id='us-va' AND template_type='instruction_letter'`. Apply via Management SQL (idempotent — the type guard makes re-runs no-ops); verification query returns per-firm type counts with exactly one `will/poa/advance_directive/guardianship/trust` VA row for the Demo firm.
- Note: duplicate VA rows in the E2E firm (will ×2, AMD ×2, guardian ×2) are left as-is; resolution takes the latest (D2). No code change — `seed_va_templates.py` was already correct; the defect came from a historical firm-to-firm copy.

### Task 1.3 — `source_fingerprint` column migration
- Create: `supabase/migrations/20261026120000_document_packages_source_fingerprint.sql` — `ALTER TABLE document_packages ADD COLUMN IF NOT EXISTS source_fingerprint text;` (+ comment). Apply via Management SQL in the same step; verify via `information_schema.columns`. Spot-verify `document_packages` RLS is on (`pg_tables.rowsecurity`) for the record.

### Task 1.4 — Fingerprint + shared demo-render core (documents/service.py)
- Add: `compute_source_fingerprint(conn, matter) -> str` per D4 (uses `intake_repo.list_answers`, `household/repository.list_persons_for_household`, `nominations/repository.list_nominations` — the same reads `_build_snapshot` makes).
- Refactor: extract the render body of `generate_demo` (post-authz: completeness check → template load → body merge → `_flag_unfilled_placeholders` → `_render_package_versions` → status `demo_released` → fingerprint stamp → audit `document_demo.generated`) into `_render_demo_package(conn, *, package_row, matter, actor_id) -> tuple[DocumentPackageOut, list[DocumentVersionOut]]`; `generate_demo` (staff) and the family generate (Task 1.7) both call it. `release_production` also stamps the fingerprint after its render.
- Modify: `documents/repository.py` — `update_package_render(conn, package_id, status, source_fingerprint)` (or extend `update_package_status` with an optional fingerprint arg; keep the old signature working for other callers).
- Tests: fingerprint unit tests (stable across calls; flips on answer edit, person rename, nomination change; does NOT flip across a date change — freeze `datetime.now` via monkeypatch on `_render_context`'s module or assert two fingerprints minutes apart are equal with unchanged data); staff `generate_demo` regression (`tests/test_slice4_documents.py`, `tests/test_documents_intake_gate.py` green).

### Task 1.5 — Draft-set resolution (new `documents/draft_set.py`)
- Produces: `resolve_draft_set(answers: Mapping) -> list[TemplateType]` implementing the D2 table (pure function); `resolve_set_templates(conn, firm_id, pack_id, types) -> dict[type, template_row]` using a pack-preferring latest lookup — add `latest_template_by_type(conn, firm_id, template_type, pack_id=None)` optional param in `documents/repository.py` (ORDER BY `(pack_id IS NOT NULL AND pack_id = %s) DESC, created_at DESC` when pack given; existing callers unchanged); `DraftTemplateMissingError` (422, code `draft_template_missing`, carries `missing_types`) raised when any selected type resolves to nothing.
- Tests: pure unit tests for every signal combination — Sharma profile → the 5 types in canonical order [will, poa, advance_directive, guardianship, trust]; pruned (no minors, no trust, both declines) → [will]; minors-only → [will, poa, advance_directive, guardianship]; trust-only → [will, poa, advance_directive, trust]; unanswered gates (`!= false` semantics) → poa/advance_directive IN, trust/guardianship OUT.

### Task 1.6 — Packet core extraction (review/service.py)
- Refactor: `generate_packet` splits into `_create_packet(conn, *, matter, actor_id, notes) -> ReviewPacketDetailOut` (snapshot + version + insert + audit) and the existing staff-facing wrapper (authz unchanged). No behavior change: `tests/test_slice3_review.py` green, including packet version increments and snapshot contents (the snapshot already carries `assigned_attorney_id` from the matter row — the family submit assigns BEFORE calling the core so the frozen packet shows the routed attorney).

### Task 1.7 — Family draft endpoints (documents/router.py + service + schemas)
- Add schemas to `documents/schemas.py`: `DraftDocumentStateOut`, `DraftSetStateOut`, `DraftSetGenerateOut` (packages + versions + fingerprint), `DraftSubmitOut` (matter id/status, packet id/version, assigned attorney id/name/email). Errors in `documents/service.py` next to the existing domain errors: `DraftsStaleError` (409, `drafts_stale`), `NoAttorneyAvailableError` (409, `no_attorney_available`), reuse `ConflictError` for `already_submitted`/`invalid_status` with codes.
- Add service functions `get_draft_set_state`, `generate_draft_set`, `submit_for_attorney_review` implementing D3/D4/D6, with `_require_family_write_scope` = `matters_service.require_matter_write_scope` (import, don't reimplement).
- Add attorney resolution to `attorneys/repository.py` + thin service wrapper: `find_routable_attorney(conn, firm_id, jurisdiction_code) -> {user_id, full_name, email} | None` — join `attorney_applications` (status `approved`, firm match, `jurisdictions @> [code]`) with active `firm_memberships` role `attorney`; order by application `created_at` ASC, limit 1. full_name/email from the application row (the enrolled identity).
- Routes in `documents/router.py` next to the package routes, each in `db.transaction()`.
- Tests (`tests/test_family_draft_documents.py`, pgserver + service-level): authz matrix (household primary_client OK; collaborator OK; dependent 403; unrelated user 404; dual-hat firm_admin+household OK — pin the D3 decision); state with flag off → `enabled:false`, no 409; generate with flag off → 409 `document_assembly_disabled`; generate incomplete matter → 409 with missing labels; generate Sharma-complete matter → 5 packages `demo_released`, DOCX+PDF versions watermarked (`watermark_text` = `DRAFT — NOT LEGALLY VALID`, `is_demo` true), fingerprints stamped; edit answer → state `stale:true`; submit stale → 409 `drafts_stale`; regenerate → submit → 200, matter `ATTORNEY_REVIEW`, `assigned_attorney_id` = seeded dummy (fixture), packet v1 snapshot contains the attorney id + corrected answers; submit again → 409; firm without routable attorney → 409 `no_attorney_available` and matter still `INTAKE_IN_PROGRESS` (rollback proven); matter pre-submitted via the old endpoint (`INTAKE_SUBMITTED`) → family submit completes the chain.
- Part gate: full targeted run (documents + review + matters submit + intake gate files), independent diff review, commit, push, deploy-verify (`/healthz` + one state call as test.recording on a Demo-firm matter → `enabled:false` until Part 3, 200 shape correct).

## Part 2 — Web (Stage 2)

### Task 2.1 — API client additions (`apps/web/lib/api-client.ts`)
- Types `DraftSetState`, `DraftDocumentState`, `DraftSubmitResult`; methods `getDraftSetState(matterId)`, `generateDraftSet(matterId)`, `submitDraftSet(matterId)` following the existing `call<T>` pattern. Reuse existing `getQuestionSets`, `getAnswers`, `saveAnswers`, `getProgress`, household/persons and nominations methods already used by the intake/guardians pages.

### Task 2.2 — Review page (`apps/web/app/(app)/family/review/page.tsx` + `apps/web/components/family/review/`)
- Components: `ReviewAnswers` (grouped sections, per-question inline editors by type, save via `saveAnswers`, save-status via the existing `lib/save-status` pattern), `ReviewPersons` (persons list, name edit via the existing person PATCH client method; nominations read-only resolved list), `DraftPanel` (document list with included/excluded + signals, generate/regenerate, stale banner, submit + blockers, post-submit status panel).
- Behavior contracts: numbers only from `getProgress` overall; after any answer/person save, refetch `getDraftSetState`; generate/submit buttons disabled strictly per `can_generate`/`can_submit` with blocker text rendered from `submit_blockers`; when `enabled:false`, render a plain "not available for your firm yet" state (no dead buttons).
- Tests (vitest, fixture-driven like the Phase 3 page tests): grouped rendering from question-sets+answers fixtures; person-type answer shows the name; boolean renders Yes/No; stale banner appears iff `stale`; submit disabled with `drafts_stale` blocker text; post-submit panel shows attorney name + packet version.

### Task 2.3 — Entry points
- My Plan: when the selected matter is applicable-complete and status is `INTAKE_IN_PROGRESS`/`INTAKE_SUBMITTED`, the next-step area links to `/family/review` ("Review & create your drafts") — probe `getDraftSetState` and render only when `enabled`. Family home `nextStepFor` gains the same branch (status-first logic preserved — the Phase 3 review fix must not regress: a matter under attorney review must NOT be told to review/generate). Documents page: header link "Review answers & drafts" under the same probe.
- Tests: home next-step unit cases extended (complete+enabled → review step; in-review status → existing attorney-review copy wins); My Plan CTA presence/absence by fixture state.

### Task 2.4 — Web verification
- `npx tsc --noEmit`, eslint on changed files, web vitest targeted files green, `npx next build` compiles. Part gate: independent diff review, commit, push, deploy-verify (production `/family/review` loads for the recording account and shows the disabled state pre-Part-3).

## Part 3 — Enrollment + flags (Stage 2)

### Task 3.1 — Enroll the dummy VA attorney into the Demo Recording Firm
- Apply the D1 seed (application row + membership upsert) via Management SQL, one statement per file; verification: `find_routable_attorney` inputs exist — SQL join returns user `5916a677-97cf-43a3-90ff-0a655692721a` for firm `b7a9957e` + `US-VA`. Also verify the E2E firm already satisfies the same join (no writes needed).

### Task 3.2 — Enable `document_assembly` for the two firms
- Per D7: `PUT /v1/admin/feature-flags/document_assembly {"is_enabled": true}` as each firm's firm_admin; verify `GET /v1/admin/feature-flags?pack_id=us-va` → `document_assembly: {is_enabled: true, source: "firm"}` for both. If source reads `env`, stop and report (Railway override) — do not work around it.
- Post-check: state endpoint as test.recording on a Demo matter → `enabled: true`; a matter in a third firm (e.g. the Gen Alpha Labs firm) still → `enabled: false`.

## Part 4 — E2E acceptance (Stage 3; each step = the approved acceptance criterion ↔ its proof)

Driver: API via the production base URL with password-grant tokens (A/B harness pattern) + UI via Playwright Route C (localhost CONNECT-forwarder + `--ignore-certificate-errors`, working files in `~/workspace/goals/legacyflow-product-build/hidden_files/rec-scratch/`). Firm: Demo Recording (flag on, dummy enrolled). Persona: Sharma script from `interview_ab.py`, with ONE deliberate misspelling planted — backup guardian answered as "Neha Kapur" — plus one text typo planted in an address answer.

1. **Fresh matter → interview 100%.** Create matter as test.recording (snapshots pack 2.0.0); run the v2 interview via API. Assert `GET /intake/progress` overall = 41/41 applicable (declared 42 — the gap is the pruned `specific_bequests`; expected, not a failure).
2. **Review screen shows everything, grouped.** Route C: open `/family/review` — all sections present; the planted "Neha Kapur" visible in the guardians answers and in Persons & roles.
3. **Edit on the review screen.** Correct the guardian person-answer spelling to "Neha Kapoor" (answer edit → `PUT /intake/answers`) AND the person record if it carries the misspelling (`PATCH /persons/{id}` from the Persons section); fix the address typo via inline edit. Assert via API: answers + person row carry the corrections; nominations re-resolved to the corrected person.
4. **Generate.** Click Create Draft Documents (Route C). Assert via API state: exactly the 5 D2 types for this profile, each package `demo_released`, DOCX+PDF versions, watermark `DRAFT — NOT LEGALLY VALID`.
5. **Corrected spelling IN the rendered drafts.** Download the Guardianship + Will PDFs (and one DOCX) via the existing download endpoint; extract text (`pdftotext` / unzip+XML grep). Assert "Neha Kapoor" present, "Kapur" absent, corrected address present. This is the acceptance core — bytes, not UI claims.
6. **Set gating (negative case, API only).** Second fresh matter driven with the pruned profile (no minors, no trust, POA + healthcare declined, complete) → generate via API → exactly {will}. Delete after.
7. **Staleness.** Edit any answer via API → state `stale: true`, `can_submit: false` → `POST submit` → 409 `drafts_stale` → regenerate via API → fingerprints match.
8. **Submit.** Click Submit to Attorney (Route C). Assert: matter `ATTORNEY_REVIEW`; `assigned_attorney_id == 5916a677…`; review packet v1 exists and its snapshot contains the corrected name + the attorney id; family home/My Plan status pill reads Attorney Review.
9. **Attorney side (API, as Demo firm_admin — D1 note).** `POST /matters/{id}/gates {gate_type: "intake_approval"}` → `POST /gates/{id}/decide {decision: "approved"}` → `POST /matters/{id}/transition {to_status: "APPROVED_FOR_ASSEMBLY"}` → create + approve `document_release` gate → `POST /packages/{pkg}/release-production` for all 5 packages → assert clean versions (`is_demo: false`, `watermark_text: null`, package `released`) and that a released PDF's text has no DRAFT banner → transition to `DOCUMENTS_RELEASED` → `POST /matters/{id}/signing-checklists` (201).
10. **Signing checklist appears for the family.** `GET /matters/{id}/signing-checklist` as the family → 200 with items; Route C: family signing page shows the checklist.
11. **Cleanup.** Delete both E2E matters FK-safely — enumerate matter-scoped tables from `information_schema` at runtime (known set: vault_shares, interview_turn_bodies → interview_turns → field_proposals → interview_sessions, intake_answers, nominations, audit_events, review_packets, approval_gates, comments, clarification_requests, document_versions → file_uploads, document_packages, signing_checklists, status history → matters) — verify 0 remain; proof + protected matters intact. KEEP: flag rows, the dummy's Demo enrollment, retyped templates (they are the shipped feature state).

## Part 5 — Stretch S1 (separately gated; never blocks Parts 1–4)

### Task S1.1 — Interview checklist applicability (D8 root cause)
- Modify: `services/api/app/domains/interview/schemas.py` (`ChecklistItemOut.applicable: bool = True`), `services/api/app/domains/interview/service.py::_session_out` — compute the partition once (same helper `_progress` uses) and set `applicable=False` for pruned questions; `apps/web/components/family/interview/interview-chat.tsx` — the "still needed" count and list filter on `required && applicable && !answered`.
- Tests: API unit — Sharma profile with `has_specific_bequests=false`: `specific_bequests` checklist item `applicable:false`, `completeness_pct` 100 and panel count 0 agree; pending gate (unanswered) → item stays `applicable:true` (fail-closed). Web vitest: panel renders "All required items are collected." for a fixture session whose only unanswered required item is non-applicable.
- Ship as its own commit + live probe (fresh Sharma interview session payload shows the flag) whenever Venugopal green-lights it; it rides no other Part's gate.

---

## Out of scope / risks

- Global `document_assembly` enablement — Venugopal approves separately (D7). Other packs than us-va v2 — the manifest capability check already fails closed for them.
- Attorney self-serve claim flow (Opportunities requests) — untouched; direct assignment only.
- Risk: 60s flag cache — mitigated in D7 verification. Risk: storage-byte orphans if a multi-package render rolls back — mitigated by pre-validating all templates before rendering (Task 1.5/1.7) so the family path cannot fail mid-set on a missing template. Risk: duplicate VA template rows in the E2E firm — resolution is deterministic (latest wins); dedupe is a separate hygiene ticket, as is the `window.confirm` sweep on guardians/assets/liabilities and the stale `section-nav.test.tsx` expectation.
- No decision from Venugopal is required to start Stage 2: every open point found in design (authz dual-hat, attorney credential gap, template mistyping) is resolved above from codebase/DB evidence within the approved scope.
