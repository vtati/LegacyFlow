# Attorney Reviewer Agent — implementation plan (2026-10-27)

Design source: technical documentation §9 ("Design: the Attorney Reviewer Agent
(PROPOSED)", approved in principle by Venugopal 2026-10-26; commissioned for
build 2026-10-27 after the self-serve core flow completed). This plan
implements §9 with the reconciliations recorded in §R below. Standing
playbook applies: TDD (failing tests first), independent diff review before
"done", fresh production verification, migration applied to production in
the same run it merges.

## Goal

An automated pre-review of a family's draft package that runs only against
the FROZEN rendered artifact (never live answers), in two layers —
deterministic checks in code, and one fenced model-judgment call — producing
structured findings the family can fix through the existing
edit → regenerate loop and the attorney sees, with dispositions, on the
packet. Advisory-first: findings never block generation, submission, or the
attorney's `document_release` gate. The human attorney release remains the
only legal approval. The feature is labelled "automated pre-review"; it is
never called an attorney or "legal review", and no output is phrased as a
legal conclusion (§9.7).

## R. Reconciliations with §9 (deviations and readings, recorded deliberately)

- **R1 — Surfaces.** §9.6 puts the primary surface on the family review
  screen; the commissioning brief puts findings on the firm UI with an
  attorney override. Both are built: family review screen (fix loop) and the
  firm packet tab (findings + dispositions that traveled with the packet).
  Acknowledgment is available to BOTH actors — family pre-submit (§9.6) and
  attorney on the packet (brief) — each recorded with actor + note.
- **R2 — Run timing.** §9.1 runs the review at draft generation against the
  persisted package version; the brief describes the run as on the frozen
  packet snapshot after submit. The submitted packet carries exactly the
  generated versions (regeneration is impossible post-submit), so these are
  the same frozen bytes. Implementation: snapshots persist at generation;
  the review auto-runs (a) after a family generate commits and (b) after
  submit commits, if no completed run exists for the submitted fingerprint;
  plus an explicit re-run endpoint (either actor with write/staff scope).
  A run NEVER reads live answers — it reads only snapshot rows.
- **R3 — Severity `blocker`.** §9.5 reserves `blocker` for a future
  enforcement stage. This build is advisory-only: severities emitted are
  `should_fix` and `question_for_attorney`. The enum leaves room for
  `blocker` later; nothing in this build blocks anything.
- **R4 — Family-required vs attorney-fill tokens.** §9.2 says "the pack marks
  which tokens are family-required" — the us-va pack carries no such marking
  today. Classification is derived: a token is family-required iff it is an
  intake question key of the matter's pack, a target slot of the documents
  slot table, or an answer key present in the frozen source answers.
  Everything else (witness/notary names, signing dates, …) is attorney-fill
  and never flagged. If the pack later marks tokens, the classifier gains a
  pack source without a schema change.
- **R5 — Execution profiles.** The us-va pack (all versions) carries exactly
  one execution profile, for the will (VA-WILL-V0). The execution check is
  profile-driven: it verifies the will's structural requirements
  (testator signature block, two-witness signature block, statutory citation
  Va. Code § 64.2-403, self-proving affidavit — the affidavit is
  *recommended* in the profile, so its absence is `question_for_attorney`,
  the rest `should_fix`). No requirements are invented for other document
  types; the check extends automatically when the pack gains profiles.
- **R6 — Model slot.** §9.4 specifies the `OPENAI_INTERVIEW_MODEL_V2` slot
  (gpt-5.5) via the provider registry's JSON gateway — one call per run,
  temperature 0, prompt `attorney_review-v1`, traced in `ai_traces`. No new
  providers or keys (LLM comparison is deferred by Venugopal).
- **R7 — §9.8 shadow period** (two weeks hidden on real matters) is an
  operational rollout step, out of scope for this build: the flag opens only
  for the three test firms (Demo, E2E, Virginia Default) in visible
  advisory mode, which the verification gate exercises end to end.

## Data model — migration `20261027160000_attorney_review.sql`

All tables backend-only: RLS enabled in the same migration, no policies
(standing rule; owner connections bypass, API roles default-deny).

- `attorney_review_snapshots` — one row per rendered package version:
  `id`, `matter_id → matters`, `firm_id`, `package_id → document_packages`,
  `package_version int`, `source_fingerprint text`,
  `template_type text`, `template_body text`, `body_text text`,
  `context jsonb`, `created_at`. Unique `(package_id, package_version)`.
- `attorney_review_sources` — the matter-level frozen source payload per
  fingerprint (persons, nominations, answers, expected draft set — the same
  data `compute_source_fingerprint` hashes): `matter_id`, `firm_id`,
  `source_fingerprint`, `payload jsonb`, `created_at`.
  Unique `(matter_id, source_fingerprint)`.
- `attorney_review_runs` — `id`, `matter_id`, `firm_id`,
  `source_fingerprint`, `status` (`running|completed|failed`),
  `model_id`, `prompt_version`, `error`, `created_by_user_id`,
  `created_at`, `completed_at`.
- `attorney_review_findings` — `id`, `run_id → runs`, `matter_id`,
  `package_id` (nullable for set-level findings), `finding_hash text`,
  `layer` (`deterministic|model`), `check_name`, `severity`
  (`should_fix|question_for_attorney`), `location jsonb`
  (`{document, field, excerpt}`), `explanation`, `suggested_fix`,
  `disposition` (`open|acknowledged|fixed_by_regeneration|stale`),
  `acknowledged_by_user_id`, `acknowledgment_note`, `acknowledged_at`,
  `created_at`, `updated_at`. Unique `(run_id, finding_hash)`.

`finding_hash` = sha256 over layer + check + document + field + normalised
evidence (the §9.5 content-hash pattern, mirroring the consistency
reviewer's idempotent rule ids).

## Snapshot capture (documents domain)

`_render_demo_package` learns to keep the render context in a local
(`context = build_template_context(...)`) and, after
`update_package_render` stamps the fingerprint, calls the reviewer domain's
recorder (lazy import, same pattern as the existing cross-domain imports):

- snapshot row per package: template type/body from the template row,
  merged `body`, the context, `package_version` = max version created in
  this render pass, the just-computed fingerprint.
- source payload row per (matter, fingerprint) if absent: persons
  (id, full_name, kind, date_of_birth), nominations (type, person_id,
  priority, status), answers (question_key → answer), and the resolved
  expected draft set for those answers.

Because the recorder runs inside the shared render core, both the family
draft-set generate and the staff demo-generate produce frozen artifacts;
production (final) renders are not snapshotted (the reviewer reviews
drafts). Recording is NOT best-effort: a snapshot failure fails the render
loudly rather than silently losing the frozen artifact the reviewer and
the attorney packet rely on.

## Layer 1 — deterministic checks (`attorney_review/checks.py`, pure functions)

Inputs: the run's snapshots + source payload + the matter's pack execution
profile (loaded exactly as `documents/service._load_execution_profile`
does). Final list:

1. `name_consistency` — canonical names = frozen persons + context
   testator/spouse. Per document body: a capitalised two-token span within
   Levenshtein distance ≤ 2 of a canonical name but not byte-identical →
   `should_fix`, located in that document with both spellings.
2. `role_consistency` — per role family (executor, POA agent, healthcare
   agent, trustee, guardian) from context slots: primary == successor
   (case-insensitive) → `should_fix`; successor present with primary blank
   → `should_fix`; a frozen active nomination whose person differs from
   the rendered slot for that role → `should_fix`.
3. `date_age_arithmetic` — from frozen persons: DOB in the future →
   `should_fix`; a minor-kind person aged ≥ 18 at snapshot date →
   `should_fix` (guardianship designated for an adult); an adult-kind
   person aged < 18 → `should_fix`; the minor-children gate answer says
   minors exist but no minor person and no children list content exists
   (or vice versa) → `should_fix`.
4. `pack_completeness` — snapshot template types for the fingerprint vs
   the frozen expected set: missing → `should_fix`; extraneous →
   `should_fix` (set-level finding, `package_id` null).
5. `unfilled_placeholders` — per snapshot: tokens(template_body) −
   context keys, classified per R4; each family-required blank →
   `should_fix` located by document + token.
6. `va_execution_requirements` — will snapshot vs VA-WILL-V0 (R5):
   testator signature block, two-witness signature block, statutory
   citation → `should_fix` each; self-proving affidavit absent →
   `question_for_attorney`.
7. `reference_integrity` — capitalised name spans in the context
   residuary-beneficiaries text matching no frozen person →
   `should_fix`; a trust snapshot whose context initial property is blank
   → `should_fix`.

## Layer 2 — model judgment (`attorney_review/model_review.py`)

One call per run through `get_json_provider(model=OPENAI_INTERVIEW_MODEL_V2)`
(the consistency reviewer's gateway), system prompt `attorney_review-v1`:
flag-only fence, questions and inconsistencies never conclusions, every
finding must quote its span. Checks: `free_text_contradiction`,
`implausible_value`, `missing_context`, `ambiguity` (the last two always
`question_for_attorney`). Server-side validation, all discards logged:

- `check` must be in the allowed set; `severity` coerced to the allowed set.
- `excerpt` must be a verbatim substring of the named document's frozen
  body — unquotable findings are discarded (§9.5).
- explanation/suggested_fix containing legal-conclusion phrasing
  ("invalid", "unenforceable", "illegal", "void", "you must") → discarded.
- Caps: excerpt ≤ 300 chars, explanation ≤ 500, suggested_fix ≤ 300,
  ≤ 25 findings per run.

The call is traced via `insert_ai_trace` (provider openai, model id,
prompt_template `attorney_review`, prompt_version `attorney_review-v1`,
pack version in metadata position `pack_version`, tokens from usage).
If the provider is not OpenAI/configured, the run completes with the
deterministic layer only and `model_id = deterministic_only` recorded —
the model layer's absence is visible, never silent.

## Run service + dispositions (`attorney_review/service.py`)

`run_review(conn, *, matter_id, actor_id | None)`:
1. Resolve matter + governing firm (`matter.firm_id`; self-serve matters
   are anchored to their default firm from creation, so this is uniform).
2. Flag gate: `attorney_reviewer` firm flag (new key, default **False**,
   added to `FeatureFlagKey` + `KNOWN_FLAG_DEFAULTS`). Off → raise on the
   explicit endpoint; auto-run hooks skip silently.
3. Current fingerprint = the packages' stamped fingerprint (must be
   uniform across the set); snapshots + source payload for it must exist,
   else the run is recorded `failed` with a specific error.
4. Supersede the previous latest run's findings: a finding whose hash
   reappears in the new run keeps living (new row; an `acknowledged`
   disposition + note carries over by hash); a prior `open`/`acknowledged`
   finding absent from the new run → `fixed_by_regeneration`; prior rows
   for superseded versions otherwise → `stale`. (§9.5 dispositions.)
5. Insert the run, run Layer 1, run Layer 2 (when configured), insert
   findings idempotently by hash, mark the run completed, audit
   `attorney_review.completed` with counts.

Auto-run hooks (documents router): after the generate and submit handler
transactions commit, invoke the run in its own transaction when the flag
is on; any failure is caught, logged, and recorded as a failed run — an
advisory system never breaks the core flow it advises on.

## API (`attorney_review/router.py`)

- `POST /matters/{matter_id}/attorney-review/run` — write scope (family
  writer or firm staff). 201 with the run summary + findings.
- `GET /matters/{matter_id}/attorney-review/findings` — matter read scope.
  Returns the latest run for the matter's current fingerprint:
  `{run, findings[]}` with dispositions; empty shape when no run exists.
  This is the single read both UIs use (post-submit the fingerprint cannot
  change, so the firm packet view shows exactly what traveled with the
  packet).
- `POST /matters/{matter_id}/attorney-review/findings/{finding_id}/acknowledge`
  — body `{note}`; write scope (family) or STAFF_ROLES (firm). Sets
  `acknowledged` + note + actor; audit `attorney_review.acknowledged`.
  Acknowledging never touches any gate.

## Web

- `lib/api-client.ts`: `getAttorneyReviewFindings`, `runAttorneyReview`,
  `acknowledgeAttorneyReviewFinding` + types.
- Family: `components/family/review/attorney-review-panel.tsx`, rendered
  from the review page's draft panel area. Heading "Automated pre-review"
  with the §9.7 explainer ("checks your drafts for consistency before your
  attorney reviews them — it isn't a legal review"). Findings grouped by
  document, `should_fix` first; each shows explanation, suggested fix, and
  an acknowledge-with-note action. When the flag is off the endpoint
  reports `enabled: false` and the panel renders nothing.
- Firm: findings section inside `components/firm/review-packet-tab.tsx` —
  same component family, firm variant adds the acknowledging actor label
  and an "Override (acknowledge)" action for the attorney. Copy keeps the
  advisory framing: findings are advisory; the release gate is unchanged.

## Evaluation + production verification (the completion gate)

Seeded-error harness (hidden_files, API + SQL planting into the frozen
snapshot rows — §9.8's prescribed method: inject labelled defects into a
known-good package, then force a re-run and read findings):

| Class | Plant | Expected check |
|---|---|---|
| Cross-document name mismatch | misspell spouse surname in the POA snapshot body | `name_consistency` |
| Role mismatch | set successor executor = executor in the will snapshot context | `role_consistency` |
| Missing pack document | delete the trust snapshot row | `pack_completeness` |
| VA execution gap | strip the witness block from the will snapshot body | `va_execution_requirements` |
| Free-text contradiction | rewrite a bequest line in the will snapshot body against the structured residuary | `free_text_contradiction` (model) |
| Implausible value | bequest amount exceeding the stated estate in the will snapshot body | `implausible_value` (model) |

Targets (§9.8): deterministic classes 100% caught; model classes caught
(≥90% target on the seeded set — the seeded set is small, so report exact
caught/missed per class). Clean control: an unmodified Sharma packet →
zero `should_fix` findings (§9.8 false-positive budget: ≤1 nuisance
finding per package, zero false blockers — this build has no blockers).
Override proof: acknowledge a finding as firm_admin, then complete the
release gate end to end (finals produced) with findings still open —
advisory never blocks. UI proof: firm packet tab renders the findings for
the submitted matter (Playwright Route C), family review screen renders
the panel. Regression: API + web suites vs the pre-change baseline
FAILED sets. Cleanup: scratch matters deleted FK-safe; protected
`b9353a06…` and proof `2145ce67…` verified intact.

## Rollout

Flag `attorney_reviewer` enabled per-firm for Demo, E2E, and Virginia
Default only (PUT as each firm's admin, verify `source: "firm"`). All
other firms resolve off by default.

## As-built addendum (2026-10-27, after the production gate)

Shipped: commits 10e1458, 522f3e1, 77cc2ed + fixes 2c89276, 6a9cadb.
Full evidence: hidden_files/attorney-reviewer-2026-10-27/VERIFICATION.md.

Deviations/corrections the gate forced (each pinned by a test built
from the production shape):
- Slot names and payload derivations must come from the renderer's own
  tables (_ANSWER_SLOT_TABLE, compute_progress), never from fixture
  intuition: similar-name suppression for family members sharing a
  surname; unfilled = answered-or-applicable-required only;
  trustee_name is the grantor by renderer design (no priority-1
  cross-check); execution detection must read the v2 signature layout
  ("Witness N:" headers, rule-line + role-labeled name lines).
- The snapshot tables carry matter_id/firm_id columns — any manual
  snapshot restore must include them.
- Two TRUE template defects the reviewer caught on a clean packet are
  product findings, not reviewer bugs: a single testator's will prints
  "I am married to ___." (+ blank children line), and the advance
  directive pairs a no-donation wish with an anatomical-gift agent
  appointment. Venugopal decision owed on the templates.

## Addendum 2 — independent review verdict + second fix pass (2026-10-27)

The independent diff review of the shipped range returned NO Criticals;
authz, SQL safety, RLS, and gate-isolation verified clean. It raised
4 Highs and 7 Mediums. Disposition (fix pass 2):

- H1 (recorder could fail generation for every firm) — FIXED in the
  first fix pass (best-effort recorder, plan R3 restored).
- H2 (runs analysed ALL snapshot versions of a fingerprint, oldest
  first — stale findings regeneration could never clear; layers could
  read different artifacts) — FIXED: snapshots_for returns the latest
  package_version per package (DISTINCT ON). The source payload stays
  frozen at a fingerprint's first render BY DESIGN: the fingerprint
  hashes exactly the payload's user-editable inputs (answers, persons,
  nominations, titles), so a same-fingerprint payload cannot go stale
  from family/staff edits.
- H3 (any package on an old fingerprint silenced the reviewer for the
  matter forever) — FIXED: the current fingerprint is resolved over
  the answer-defined draft set (resolve_draft_set) using the latest
  package of each included type; packages outside the set are ignored.
- H4 (run lifecycle not atomic; failed runs unreachable) — FIXED:
  persist is a single write transaction (first fix pass) and failures
  in compute/persist now record a 'failed' run in its own transaction
  before the error propagates.
- M1 (no idempotency — model paid per generate+submit+manual run) —
  FIXED (first fix pass): per-fingerprint idempotency, deterministic-
  only generate runs, force gated to firm actors.
- M2 (auto-run synchronous in the family's request, inside a pooled
  transaction) — FIXED: model call holds no transaction (first fix
  pass); auto-runs now execute on a daemon thread after the core
  action commits, and the family page re-fetches findings on a short
  delay.
- M3 (placeholder check blind to identity slots + "___"-valued slots)
  — FIXED: slot sources extended for relationship-derived slots
  (spouse/testator/children) from the gated identity questions, and
  placeholder-only context values count as unfilled.
- M4 (firm section shows matter-current findings under a packet
  version selector) — SCOPED: findings bind to the draft-packet
  fingerprint; the panel now captions "Findings are for the matter's
  current packet · {run time}". Packet-version-scoped findings are
  deferred — review packets are a separate snapshot artifact, and
  post-submit the current packet IS the submitted packet until a
  resubmission creates a new one.
- M5 ("No issues found" even when the model layer never ran) — FIXED:
  a deterministic_only run renders a distinct neutral message naming
  the judgment pass as not yet run.
- M6 (finding_hash collapsed distinct people sharing evidence) —
  FIXED: person-scoped findings carry a subject, included in the hash.
- M7 (reference_integrity should_fix on legitimate outside
  beneficiaries) — FIXED: near-miss of a household person stays
  should_fix (probable typo); an unknown name is now a
  question_for_attorney (confirm intentional — friend/charity).
- L1 (ack note unbounded/empty; ack race) — FIXED: note validated
  (trimmed, 1..2000), ack requires the flag, and the UPDATE is
  disposition-guarded (a lost race conflicts, never flips a
  transitioned finding back).
- L2 (web handlers silent on failure; family had no re-run) — FIXED:
  try/catch + visible error line on both surfaces; family panel gets
  the Run button.
- L3 (acknowledging actor never shown) — FIXED: FindingOut carries
  acknowledged_by_actor ("firm" | "family"); the panel renders it.
- L4 (legal-conclusion fence gaps) — HARDENED: "not valid" and
  "no legal effect" added; a model finding's suggested_fix must be
  phrased as a question or it is dropped (§9.5).
- L5 (yardstick drift: profiles from newest pack on disk; ages as of
  run date) — FIXED: execution profiles load the matter's snapshotted
  pack version; age checks run as of the frozen source's recorded date.
- L6 (migration FKs without ON DELETE) — DEFERRED: friction for hard
  deletes only (ops cleanup deletes reviewer rows first, as the
  verification cleanup did); no runtime impact.
