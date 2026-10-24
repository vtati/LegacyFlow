# Interview v2 — Implementation Plan

**Spec:** `~/workspace/goals/legacyflow-product-build/files/interview-v2-design-spec-2026-10-24.md` (approved by Venugopal 2026-10-24)
**Repo:** `~/workspace/legacyflow` · API: `services/api` · Packs: `services/api/content-packs/us-va/`

## Global constraints (every task inherits these)

- Commits authored `vtati <vtati@users.noreply.github.com>`; every push to `main` auto-deploys (Vercel + Railway). Verify the author after each commit.
- TDD: failing test first, watch it fail, minimal implementation, green. No completion claim without fresh test output.
- Run tests with ALL proxy env vars unset: `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY -u no_proxy -u NO_PROXY .venv/bin/python -m pytest <files> -q` from `services/api`. DB-backed (pgserver) tests may error in the sandbox — that is pre-existing/environmental; pure-unit tests + production API probes are the verification stack.
- NEVER edit the v1 pack in place. v2 is a new directory copied from v1.
- `get_pack("US-VA")` with no version returns the HIGHEST semantic version, and matter creation snapshots it — so the v2 directory landing instantly makes new matters v2. Sequence the parts so the engine (Part 1) lands in the same push window as the pack.
- Feature flags are DB-backed: `feature_flags_service.is_enabled(conn, "<flag>", firm_id=..., pack_id=...)`. The v2 flow flag is named `interview_v2`.
- Safety: never delete firm `b7a9957e-aa67-4235-8ff9-2310468d92ff` (Demo Recording Firm) or matter `b9353a06-16b2-47e3-8967-8895d19ec471` (genalphalabs@gmail.com). Test account: test.recording@legacyflow.app. Supabase Management SQL: `python3 ~/workspace/custom-connectors/custom.supabase-management/bin/run_sql.py nlrxlpjkrmvubvsfepjs <file>` (returns only the LAST statement's rows).
- Each Part ends with: full targeted test run, an independent diff review of that Part's diff (fix Critical/Important before proceeding), commit(s), push, deploy verification.

---

## Part 1 — Applicability engine + us-va v2 pack + shared completeness

### Task 1.1 — `applies_if` evaluator (intake/question_sets.py)

- Create: tests in `tests/test_applicability.py`.
- Produces: `evaluate_applies_if(expr: str, answers: Mapping) -> bool | None` — `None` when any referenced key is unanswered (fail-closed: pending, never pruned); raises `ValueError` on malformed expressions.
- Grammar (recursive descent, no `eval`): `or`/`and`/`not`, parentheses, comparisons `key == literal` / `key != literal` where literal ∈ {`true`, `false`, number, `'string'`}. Keys are bare identifiers (question keys).
- Tests (name + assertion): true/false comparisons against boolean answers; string comparison (`marital_status == 'married'`); `and`/`or`/`not` precedence; parentheses; unanswered referenced key → `None`; unanswered key inside an `or` whose other side is decisively true → `True` (three-valued logic: `None or True == True`, `None and False == False`, otherwise `None`); malformed inputs raise (`"has_minor_children =="`, `"foo bar"`, unbalanced parens).

### Task 1.2 — Applicable set + progress contract

- Modify: `app/domains/intake/question_sets.py` (`compute_progress`), its callers in `app/domains/intake/service.py` and `app/domains/interview/service.py` (find every place "missing"/"still needed"/coverage is computed and route it through the same function).
- Produces: `partition_questions(question_sets, answers) -> (applicable, pending, pruned)` where a question is **applicable** when its section's and its own `applies_if` (either may be absent) evaluate True or are absent; **pending** when any evaluates None; **pruned** when any evaluates False. Section-level `applies_if` lives on the section dict.
- `compute_progress` signature gains the partition: returns `(per_set, completeness_pct, missing_required_keys, pruned_required_keys)`; per-set and overall denominators count applicable+pending required only (pending still counts — fail-closed); `missing` = applicable-or-pending required unanswered, in pack order. Update all call sites; the HTTP progress payload stays shape-compatible (additive only).
- Tests: full-yes Sharma profile → 38 applicable, 0 pruned; childless profile (has_minor_children=false answered) → guardian keys pruned; gate unanswered → guardian keys pending (counted, not pruned); section-level prune (poa set) works; answered-but-later-pruned keys do not error and are excluded from missing.

### Task 1.3 — us-va v2 pack

- Create: `content-packs/us-va/v2/` — copy of v1 with: `manifest.yaml` semantic_version `2.0.0` (keep capabilities incl. `document_assembly: true` — verify against v1 manifest and mirror it); question-set `version: 2.0.0`.
- Annotate per the spec table: guardians set questions + `primary_guardian_address` (addresses set) get `applies_if: "has_minor_children == true"`; trust detail questions (successor_trustee_person, trust_initial_property, trust_distribution_age, trust_marital_share) get `applies_if: "wants_revocable_trust == true"`; `specific_bequests` becomes `required: true` + `applies_if: "has_specific_bequests == true"`; beneficiaries set gains required booleans `has_retirement_accounts`, `has_life_insurance`, and the two `*_beneficiaries_current` notes get matching `applies_if`s; poa section gets `applies_if: "wants_poa_document != false"` and healthcare section `applies_if: "wants_healthcare_directive != false"`, with `wants_poa_document` / `wants_healthcare_directive` added as optional booleans in the household set (absent = wanted, so `!= false` keeps them applicable until explicitly declined — evaluator must treat an unanswered key in a `!= false` comparison per the three-valued rules: None → pending; hmm — pending would block progress forever for users who never see the question. Resolve: these two questions are asked in the identity cluster, so they WILL be answered; pending is correct until then).
- Every question gains a `cluster:` field (Part 2 consumes it): household+addresses sets → `identity`; household gate keys stay in `identity` except has_minor_children cluster `children`; guardians + executors person/address keys → `people`; assets + beneficiaries → `money`; bequests → `wishes`; healthcare + poa → `agents`; trust → `trust`; final_wishes → `final`. (Per-question field, since clusters cut across sets: has_minor_children is in household but cluster `children`, guardian keys are their own set but cluster `people`.)
- Tests: pack-validation — every `applies_if` parses and references existing keys; every `maps_to` nominations binding is DB-legal (extend the existing binding test to load v2); profile counts: full-yes → 40 applicable required (38 + 2 new gates), pruned profile (no minors, no trust, no bequests, no retirement/insurance, declines nothing else) → 30; loader test: `get_pack("US-VA")` resolves 2.0.0, `get_pack("US-VA", "1.0.0")` still resolves v1.

### Task 1.4 — Documents completeness pre-check

- Modify: `app/domains/documents/service.py` — in `generate_demo` and `release_production`, after `_require_document_assembly`: compute the matter's missing applicable-required via Part 1's partition over the matter's snapshotted pack + stored answers; if non-empty raise `IntakeIncompleteError` (new domain error, HTTP 409, message lists the missing question labels). v1 matters: no `applies_if` anywhere → missing == today's required-missing; matters already complete (E2E #3's) must still generate.
- Tests: mock-level unit test (incomplete → raises with labels; complete → passes through); live probe after deploy: E2E #3 matter still generates a draft; a fresh incomplete v2 matter is refused with the 409 + labels.

**Part 1 live verification:** create a new matter as test.recording (snapshots v2), save `has_minor_children=false` + the identity basics via the intake API, GET progress → required denominator excludes the 4 guardian keys; documents generate on it → 409 listing remaining missing labels.

---

## Part 2 — Cluster selector + prompt v6 (flag-gated)

### Task 2.1 — Next-gap selector

- Create: `next_gap_cluster(question_sets, answers) -> dict | None` in `app/domains/interview/service.py` (pure). Uses Part 1's partition; groups applicable-missing questions by their `cluster:` field; order: `identity, children, money, wishes, people, agents, trust, final`. Returns `{cluster, questions: [{key, label, type}], }` for the first non-empty cluster, else None.
- Tests: Sharma-empty → identity cluster with its missing keys; identity done → children; pruned profile skips guardian keys entirely (they're never in any cluster's missing list); all applicable answered → None.

### Task 2.2 — Prompt v6

- Modify: `app/domains/interview/prompts.py` — `PROMPT_VERSION = "v6"`; when the turn context carries a cluster gap, the system prompt's state section lists ONLY that cluster's missing questions + a bundled-question instruction: ask ONE question covering up to 4 of the listed fields, in plain language, grouped naturally; never mention fields outside the list; never state counts; proposals may cover any listed field the user's answer addresses. Keep every v5 guardrail (recorded-claims rule etc.) — v6 is v5 + cluster section.
- The interview turn flow (`process_turn` in interview/service.py): when the `interview_v2` flag is enabled for the matter's firm+pack, compute the gap via Task 2.1 and pass it into the prompt builder; otherwise the v5 path byte-for-byte. The flag is read once per turn.
- Tests: prompt snapshot — flag-on context contains the cluster's missing labels and NOT another cluster's; flag-off path unchanged (existing interview tests green); selector+prompt integration test with the Sharma-empty profile.

**Part 2 live verification:** with the flag still OFF everywhere, production behavior is unchanged (health + one v1-style turn on a throwaway v2 matter via API behaves as today). (Flag seeding happens in Part 4.)

---

## Part 3 — Role bundles + identity capture

### Task 3.1 — Bundle engine

- Create in `app/domains/interview/service.py` (pure helpers + turn-flow wiring):
  - `PRIMARY_BUNDLE = ("primary_executor_person", "poa_agent_person", "healthcare_agent_person")`
  - `BACKUP_BUNDLE = ("backup_executor_person", "successor_poa_agent_person", "successor_healthcare_agent_person", "successor_trustee_person")`
  - `pending_bundle_offer(answers) -> dict | None`: for each bundle, if exactly one member is answered (the anchor, with its person name) and ≥1 member is unanswered AND applicable (skip pruned members — e.g. no-trust removes successor_trustee_person), return `{bundle, anchor_key, anchor_name, fill_keys}`.
  - Turn flow (flag on): a pending offer takes priority over the cluster gap — the prompt instructs the model to ask the grouped yes/no question naming the anchor and the roles. Detect the user's yes/no deterministically from their reply to the offer turn (reuse `_parse_yes_no`): on YES, the server creates proposals for each fill key with the anchor name, confidence 1.0, source marker `bundle` (they flow through the normal confirm/auto-confirm path so answers + nominations + audit all stay consistent — do NOT write answers directly). On NO, record the decline so the offer never repeats for that anchor: persistence — first check `interview_sessions` for an existing JSON/state column; if none exists, add migration `interview_sessions.state jsonb NOT NULL DEFAULT '{}'` and store `{"bundles_declined": ["primary:<anchor lower>"]}`. 
  - Address bundle: when `testator_street_address` + `testator_city` + `primary_residence_state` are answered and any of (`primary_executor_address`, `poa_agent_address`, `primary_guardian_address` — only if applicable) is missing, the same offer mechanism asks "same address as yours?" and on YES fills each missing address key with `"<street>, <city>, <state>"` via proposals (source `bundle`).
- Tests: offer triggers (one member answered), no offer (0 or ≥2 answered), pruned member excluded, yes → proposals for exactly the fill keys with the anchor name, no → declined recorded and offer suppressed on the next turn, address bundle composition.

### Task 3.2 — Identity capture (testator name)

- v2 pack: household set gains `testator_full_name` (type text, required: true, cluster `identity`, first question). (Edit the v2 YAML from Part 1 + bump nothing else; validation tests updated: full-yes applicable required becomes 41.)
- Turn flow (flag on): the prompt context gains `signed_in_name` = the matter's primary client user full name (from the user record via the household's primary membership); the identity cluster brief instructs opening with a name confirmation ("You're signed in as X — is that the full legal name for the documents?").
- On `testator_full_name` being saved (intake save path, any flow): sync the household's primary-client person — first inspect the persons schema for a user-link column; if present, update that person's `full_name` when it is empty or "Unknown person"; if no link column exists, update the earliest adult person whose name is empty/"Unknown person"; never overwrite a real human-entered name. Implement as `_sync_testator_name` beside the other syncs in intake/service.py, best-effort + SAVEPOINT-armored.
- Tests: sync updates the unknown person; never overwrites a real name; no-op when no unknown person exists.

**Part 3 live verification (API, flag ON for the Demo firm — seed in this part for testing, Part 4 handles rollout):** drive a v2 matter: answer identity incl. name confirm → Family persons show the real name; answer executor only → next assistant turn asks the bundle question; reply yes → the other two primary roles appear as confirmed proposals; progress reflects all three.

---

## Part 4 — Model split + A/B + rollout

### Task 4.1 — Model split

- Modify: `app/core/config.py` — add `OPENAI_INTERVIEW_MODEL: str = "gpt-4o"`. `app/domains/interview/service.py` (chat call) and `app/domains/interview/extraction.py` use it; `consistency.py` keeps `OPENAI_CHAT_MODEL`. Tests: settings default assertion + a call-site test asserting the interview paths read the new setting (monkeypatch settings and assert the model kwarg).

### Task 4.2 — A/B harness + runs

- Create: `~/workspace/goals/legacyflow-product-build/hidden_files/interview_ab.py` — drives the production API as test.recording: password-grant token; create matter (Complete Estate Plan Bundle, Virginia); loop: read the latest assistant message + pending proposals, choose the scripted reply by matching the assistant's text/proposal keys against a keyword→answer script (the Sharma script from the spec, plus the pruned script: single, no children, no trust, no bequests, no retirement/insurance); confirm correct proposals, edit none (log mismatches); stop at 100% or 40 turns. Log per run: user_turns, wall_clock_s, repeated_questions (same key proposed/asked after already answered), wrong_source_proposals (proposal value not matching the script's value for that key), final progress, documents_generate_ok (create one package per template + generate-demo).
- Runs: (a) v2 flag ON for Demo firm `b7a9957e-aa67-4235-8ff9-2310468d92ff` pack `us-va` (seed the `interview_v2` flag row — inspect the feature_flags table/service for the row shape first), Sharma script; (b) pruned script. Baseline for context: E2E #3 browser run (≈50 turns, repeats 3/2/3/2, 1 wrong-source + 1 timing anomaly).
- **Pass bar (spec §4):** Sharma ≤ 12 user turns, pruned ≤ 8 turns with denominator 30 (41-key pack: pruned profile applicable = 41 − 4 guardians − 4 trust − 1 specific_bequests... recompute from the v2 pack at runtime and assert the harness prints it; expected ≈ 32 with the 2 beneficiary gates answered-no and testator_full_name included — the harness must print the actual denominator and the report must use it), 0 repeats, 0 wrong-source proposals, documents generate. If the bar fails: root-cause, fix, re-run (max 3 iterations, then stop and report).

### Task 4.3 — Rollout + report

- Enable `interview_v2` for all firms (flag rows) once the A/B passes; v1 matters keep v1 by snapshot. Write the results into the goal's hidden files (`interview-v2-ab-results-2026-10-24.md`) and report to the main chat: metrics vs the bar, commits, flag state.

## Review Focus (risks no task's tests fully exercise)

- Evaluator three-valued logic at SECTION level combined with question-level rules (pending section + pruned question, etc.) — partition tests must include one mixed case.
- Gate flips mid-interview: user answers guardian questions, then changes has_minor_children to false — pruned-but-answered keys must not break progress or the documents pre-check (they're simply out of the denominator; documents still render from stored answers).
- Bundle offer loop: offer → user answers something else entirely (ignores the question) → the offer must not re-fire every turn; treat a non-yes/no reply as "defer" and return to cluster flow, re-offering at most once more.
- Bundle anchor is the spouse for primary roles but the user may want a different healthcare agent — the NO path must fall through to normal cluster questions for the remaining roles (it does — they stay missing).
- `wants_poa_document != false` pending semantics: users who skip the identity cluster's optional questions must not be blocked at 100%-minus-POA forever; the questions are in the identity cluster brief so they get asked — the A/B pruned script must include answering them.
- Identity sync overwriting: a firm staff member may have corrected the person's name before the interview — the sync only fills empty/Unknown, never overwrites.
