# LegacyFlow AI Interview — Technical Design

**Status:** Current as of 2026-10-29 (main @ `9725fa2`). Every mechanism described
here was verified against the code named inline. Where the code and earlier
documentation disagree, this document follows the code and says so.
**Parent document:** [LegacyFlow — Technical Design](technical-design.md) (system
architecture, data model, authorization). This document is the deep reference for
the interview subsystem only.
**Supersedes:** `docs/ai-interviewer-design.md` (2026-09-28), which was a
pre-implementation proposal. Two of its positions were deliberately reversed in
the shipped product — it required the user to verify *every* proposed value, and
it framed provenance as a confirmation workflow; the shipped design auto-populates
high-confidence non-material values and reserves confirmation cards for legally
material facts (§6). Treat the 2026-09-28 document as historical.

## Table of contents

1. [Design principles, as implemented](#1-design-principles-as-implemented)
2. [Actors and surfaces](#2-actors-and-surfaces)
3. [Session lifecycle and state machine](#3-session-lifecycle-and-state-machine)
4. [The turn loop, end to end](#4-the-turn-loop-end-to-end)
5. [The two interview generations: v1 and v2](#5-the-two-interview-generations-v1-and-v2)
6. [Proposals and the confirmation gradient](#6-proposals-and-the-confirmation-gradient)
7. [From answers to people: the sync layer](#7-from-answers-to-people-the-sync-layer)
8. [Interplay with the structured intake](#8-interplay-with-the-structured-intake)
9. [Adjacent AI in the interview orbit](#9-adjacent-ai-in-the-interview-orbit)
10. [Reliability, degradation, and limits](#10-reliability-degradation-and-limits)
11. [Data written per turn](#11-data-written-per-turn)
12. [Configuration surface](#12-configuration-surface)
13. [Open questions and future work](#13-open-questions-and-future-work)

---

## 1. Design principles, as implemented

1. **Layperson-first.** The site supplies the legal framing; the AI asks only for
   genuine gaps. The interviewer's system prompt (`domains/interview/prompts.py`)
   presents the pack's questions with their plain-language labels; the model never
   invents legal concepts, never gives legal advice, and — mechanically, not just
   by prompt — a message classified as requesting advice/strategy is refused and
   escalated before any model call (§4.2, §10).
2. **Extracted information auto-populates.** When the model extracts a value with
   confidence ≥ 0.9 for a *non-material* question, it is confirmed automatically
   and written to `intake_answers` in the same request (§6.2). The user sees the
   result in their answers and can edit it at any time — there is no confirmation
   step. This was a deliberate reversal of the original proposal (see header).
3. **Legally material facts are never auto-confirmed.** Proposals that would write
   a person, relationship, nomination, or asset fact — or that render directly
   into generated documents — always surface as confirmation cards, regardless of
   model confidence (SEC-013, §6.1). The model's self-reported confidence is never
   the sole authority over a legal fact.
4. **Partial answers are normal.** Progress counts only *applicable* required
   questions (pack `applies_if` gates partition questions into applicable /
   pending / pruned — §8.1). A family can stop anywhere, resume later, and park
   questions they can't answer yet (§8.2); parked items leave the interview's
   focus and missing list but still gate document generation.
5. **Inference is graded, not binary.** The confirmation gradient is:
   auto-confirm (high-confidence, non-material) → confirmation card (material, or
   document-extracted) → deterministic refusal (advice requests). On top of that,
   every model proposal passes deterministic *grounding guards* server-side
   before it exists at all (§4.4) — an ungrounded value is dropped, not shown.
6. **Provenance on everything.** Every proposed value records its source
   (`user_stated` / `document_extracted` / `ai_inferred`), confidence, source
   snippet, model id, prompt version, and pack version in `field_proposals`.
   Raw conversation bodies are encrypted at rest and purged on a retention
   schedule; summaries, proposals, and traces are the permanent record (§11).
7. **The interviewer is one writer among several.** Interview, structured wizard,
   Review-screen edits, and document extraction all converge on a single write
   funnel — `intake.save_answers` — so validation, coercion, person/relationship
   syncs, and audit behave identically no matter which surface produced the
   answer (§7).

## 2. Actors and surfaces

**Actors.** The family member (household `primary_client`/`collaborator` with
matter write scope) is the only direct actor. Firm staff never post turns; they
see the results through answers, proposals, escalations, and the review packet.
The model is an untrusted proposer: it can only *propose* values through one
function tool (§4.3); all writes are server-side.

**Surfaces.**

- **Interview chat** (`apps/web` `components/interview/interview-chat.tsx`): a
  message thread with a composer; on mobile it follows a strict contract
  (stick-to-bottom intent with a 96px threshold, focus restored after send,
  the composer is never focus-disabled — fixed 2026-10-27 after a real device
  report). Dictation is available in the composer (§9.1).
- **Structured intake wizard** (`components/intake/intake-wizard.tsx`): the same
  pack questions as forms, section by section. It is a full peer of the chat —
  both read/write the same answers — and is covered in §8.
- **Review screen**: shows progress, open (parked) items, Attorney Reviewer
  findings, and is where draft generation/submission happen (parent document).

**API routes** (`domains/interview/router.py`, all under `/v1`, all requiring a
verified Supabase JWT; matter scope is resolved per call):

| Method & path | Purpose |
|---|---|
| `POST /matters/{id}/interview/sessions` | Start a session, or resume the active one (idempotent per matter + question set) |
| `GET /matters/{id}/interview/sessions/{sid}` | Session state + coverage checklist + open proposals |
| `PATCH /matters/{id}/interview/sessions/{sid}` | Pause / resume / abandon / complete (transitions validated server-side) |
| `POST /matters/{id}/interview/sessions/{sid}/turns` | Post a message; returns reply + proposals + citations + progress (`TurnOut`) |
| `GET /matters/{id}/interview/sessions/{sid}/turns` | Recent turn summaries (no raw bodies) |
| `GET /matters/{id}/interview/sessions/{sid}/turns/{tid}` | Full text of one turn (the user's own conversation) |
| `GET /matters/{id}/interview/sessions/{sid}/proposals` | Open proposals awaiting a decision |
| `POST /matters/{id}/interview/proposals/{pid}/confirm` | Confirm as-is → value written to intake with provenance |
| `POST /matters/{id}/interview/proposals/{pid}/correct` | Confirm with an edited value (both values stored) |
| `POST /matters/{id}/interview/proposals/{pid}/reject` | Reject → the key returns to the missing list; the source snippet is scrubbed |
| `POST /matters/{id}/interview/sessions/{sid}/parked-items` | Park a question ("don't know yet") |
| `DELETE /matters/{id}/interview/sessions/{sid}/parked-items/{key}` | Unpark; the item rejoins the missing list |
| `GET /matters/{id}/interview/parked-items` | The matter's open parked items (Review screen list) |
| `POST /matters/{id}/interview/dictate` | Transcribe audio → text (returned for review, never auto-sent) |
| `POST /matters/{id}/interview/sessions/{sid}/extractions` | Upload a source document → `document_extracted` proposals |
| `GET /matters/{id}/interview/sessions/{sid}/extractions` | Past extraction runs for the session |
| `POST /matters/{id}/interview/sessions/{sid}/consistency-review` | Run the Consistency Reviewer over verified facts (§9.3) |

Handlers are plain `def` (FastAPI threadpool) wherever they run blocking DB + LLM
work; the one handler that must be `async` — dictation, because it streams the
request body — offloads its blocking work explicitly with `run_in_threadpool`
(PERF-015).

## 3. Session lifecycle and state machine

**Creation.** `service.start_session` requires matter *write* scope and the
`ai_interviewer` feature flag for the matter's firm (`_require_flag` — the whole
interview surface is gated on this one flag). The session binds to:

- the **matter** (and through it, the firm and household);
- a **question set** from the matter's pack (validated against the pack —
  an unknown set id is a 422 validation error);
- the **pack version** from the matter's `jurisdiction_snapshot` pin
  (`pack_version` column). A session never silently follows a newer pack.

One active session per (matter, question set): if a session in an active status
exists, start returns it (resume semantics — the POST is idempotent per
matter+set). Session creation **fails closed** when
`INTERVIEW_TURN_ENCRYPTION_KEY` is unset: the interviewer refuses to start
rather than store plaintext turn bodies (ADR-002).

**State machine** (`domains/interview/state_machine.py`):

```
draft ──▶ in_progress ──▶ paused ──▶ in_progress (resume, free & unlimited)
  │           │  │
  │           │  └──────▶ completed (terminal)
  └───────────┴─────────▶ abandoned (terminal)
```

Posting a turn to a `paused` session is a 409 ("Resume the interview session
before posting turns"); to a terminal session, a 409 naming the state. The first
turn posted to a `draft` session moves it to `in_progress` (deferred to the
turn's write phase — §4.1). Pause/resume is free and unlimited; a session ends
only by completion, abandonment, or explicit user action.

**Resume behavior.** History is reconstructed from the last 10 stored turns
(`_HISTORY_TURNS = 10`), decrypted on read. Sessions survive across devices and
days because everything is server-side; the web client holds no interview state
beyond the current thread view.

## 4. The turn loop, end to end

`POST …/turns` is the heart of the subsystem. Since PERF-016 it runs in three
phases so the provider call never holds a database transaction (previously the
sync AI paths held pooled connections across the model call):

```
prepare (short read txn) ──▶ provider call (NO txn open) ──▶ complete (short write txn)
        │                                                            │
        └─ short-circuits return early: idempotent replay, ──────────┘
           advice refusal (both fully persisted in the prepare txn)
```

After the turn commits, the router runs one more batched step: auto-confirmation
of eligible proposals (§6.2). A failure there never breaks the turn response —
proposals simply stay pending for manual card decisions.

### 4.1 Prepare phase (`_prepare_turn`)

In one short transaction:

1. Resolve the caller and require matter write scope; check the `ai_interviewer`
   flag; enforce the per-user turn rate limit (counted from `interview_turns`:
   a per-minute and a per-day ceiling — `RateLimitError` when exceeded).
2. Load the session; reject turns on paused/terminal sessions (above).
3. **Idempotent replay:** if the payload carries an `idempotency_key` that
   already exists for this session, rebuild and return the original response
   from the stored assistant turn (ordered by turn `seq`, because `created_at`
   ties inside one transaction). If the user turn exists but no reply does yet:
   409 "still being processed."
4. Enforce the session turn cap (`INTERVIEW_MAX_TURNS_PER_SESSION`, default 200):
   beyond it, a 409 directs the family to continue via the attorney review
   package — a cost guard, phrased for laypeople.
5. **Advice classification:** if the message requests legal advice/strategy
   (`ai_service._requests_advice`), short-circuit to a refused turn (§10).
6. Assemble context: current answers (`_answers_map`), display progress with
   parked items factored out (`_display_progress` — §8), the keys with open
   proposals (the model is told not to re-ask those), question labels for every
   pack key, conversation history (last 10 bodies, decrypted — §10 for the
   incomplete case), retrieved approved pack excerpts for grounding, and — on
   the v2 path — the turn's cluster focus, gate-shadow fields, other-fields
   inventory, and any pending bundle offers (§5).
7. Build the system prompt (§5.2) and select the provider + effective model
   (§5.1). Writes the prepare phase would historically have made inline
   (draft→in_progress, conversational parks, bundle state) are **deferred** and
   applied at the top of the complete phase, preserving the turn's
   all-or-nothing semantics across the transaction split.

### 4.2 Provider call (`_call_turn_provider`)

One Chat Completions call (`domains/ai/provider.py::OpenAIChatProvider.chat`):
system prompt + approved-excerpts block, history + the new user message, and a
single function tool (below). Payload shape comes from `MODEL_CAPABILITIES`
(parent document §9.3): for gpt-5-family models — no `temperature`,
`max_completion_tokens`, and `reasoning_effort="none"` (function tools are
rejected otherwise, a verified live 400). Transport failures raise
`ProviderError` → a generic 502 to the client; provider internals are logged
server-side only. Because no transaction is open during the call, a provider
failure persists *nothing* — no user turn, no proposals, no state change.

### 4.3 The one tool

The model's only capability is the `propose_field_values` function
(`_propose_tool_spec` in `domains/ai/provider.py`): "Record structured field
values extracted from the user's message. Call once per turn with every
confident value; omit uncertain ones." Each proposal carries `question_key`,
`value`, `confidence` (0–1), and a `source_snippet` — the user's own words the
value came from. The model has no other tools, no write access, and no way to
address the user except its reply text.

### 4.4 Complete phase (`_complete_turn`) — deterministic guards

The model's output is *not* trusted. Before any proposal is persisted, the
complete phase runs it through a battery of deterministic validators in
`domains/interview/service.py` (this guard layer is a substantial part of the
file — several thousand lines — and is the subsystem's real accuracy story):

- **Known keys only.** Proposals for keys not in the matter's pack are dropped
  (loudly logged). Value shapes are checked against the question type.
- **Grounding.** A proposed value must be grounded in the user's actual message:
  name-grounding (`_person_name_grounded`), short-value grounding, clause and
  successor-signal analysis (`_successor_value_licensed`,
  `_grounding_drop_reason`). The canonical failure these guards exist for is the
  *wrong-source* extraction: a value lifted from the wrong clause/person (e.g.
  a successor's name recorded as the primary, or a name copied from the
  assistant's own question text — `_copy_guard_asked_keys`, `_is_whole_message_copy`).
  Ungrounded proposals are dropped, never shown.
- **Role coherence.** Person proposals are checked for role mismatch
  (`_person_role_mismatch`, `_primary_role_swapped`, `_clause_names_another_nominee`):
  a person named for one purpose is not thereby nominated for another role.
- **Gate licensing.** Values for questions behind an `applies_if` gate are only
  accepted when the gate's own answer licenses them in the same message
  (`_drop_unlicensed_gate_proposals`).
- **Deterministic extraction alongside the model.** For well-structured cases
  the server also derives proposals deterministically (boolean answers —
  `_deterministic_boolean_proposal`; person names — `_deterministic_person_proposal`;
  short text/phone — `_deterministic_text_proposal`, `_extract_phone_value`), so
  common answers don't depend on model behavior at all.
- **Duplicates.** A proposal that restates an already-stored answer is a no-op
  (`_is_duplicate_answer`); a proposal restating a *rejected* pair is blocked
  (`_rejected_pair_restated`) — the user's earlier "no" sticks.

Surviving proposals are inserted into `field_proposals` with `source` =
`user_stated` (from conversation) and full provenance. Both turns (user +
assistant) are then stored (§11), an `ai_traces` row records the call
(model, prompt version, pack version, tokens, latency, trace hash), and an
`interview.turn` audit event records summaries only. The response (`TurnOut`)
carries the reply text, citations (assembled deterministically from the
retrieved documents, not by the model), the surviving proposals, refreshed
completeness/missing/parked key lists, `refused`/`escalated` flags, and
`history_incomplete` (§10).

---

## 5. The two interview generations: v1 and v2

### 5.1 Selection and models

There is one interview engine, one prompt builder, and one turn loop. "v1" and
"v2" are two *configurations* of it, selected per turn by
`service._v2_enabled`: the `interview_v2` feature flag resolved for the matter's
firm + pack (flag service precedence: env override > firm > jurisdiction >
environment > default `False`). A flag-read error falls back to the v1 path —
a flag failure must never break a turn.

| | v1 path | v2 path |
|---|---|---|
| Flag | `interview_v2` off | `interview_v2` on for the firm/pack |
| Model | `OPENAI_INTERVIEW_MODEL` = `gpt-4o` | `OPENAI_INTERVIEW_MODEL_V2` = `gpt-5.5` |
| Prompt rendering | Template **v5** | Template **v6–v10** layers as applicable (below) |
| Turn focus | Next unanswered questions, adaptively chosen (`_next_adaptive_question`) | **Topic-cluster focus**: the turn centers on one cluster of related gaps (`_cluster_gap_for_turn`, `_focus_for_turn`) |
| Extra mechanics | — | Bundle offers, gate-shadow fields, other-fields inventory, natural-question presentation |

Rollout history: v2 was enabled per firm (Demo first) behind the flag, A/B
benchmarked (§5.3), and then enabled **globally** on 2026-10-26 via a single
environment-scope flag row — no code change, and any firm can still be pinned
back by a firm-scope row. Document extraction follows the matter's path: v2
matters extract with `gpt-5.5`, v1 matters with `gpt-4o` (`extraction._extraction_model`
— an earlier version hardcoded the v1 model, an F1 drift bug).

### 5.2 Prompt construction (`domains/interview/prompts.py`)

One builder, `build_system_prompt`, renders layered template versions. Each
layer is **byte-identical to its predecessor when its feature is inactive**, and
every turn traces the version that actually rendered:

- **v5** — the base prompt: role and conduct rules (gather facts, explain only
  from approved excerpts, no legal advice), the focus questions with labels,
  the already-answered state, pending-proposal keys not to re-ask, and the
  grounding instruction ("ground every factual claim in the approved
  educational excerpts provided with this turn" — the excerpts are appended to
  the system prompt by the provider, and citations are assembled
  deterministically server-side).
- **v6** — adds the optional **CLUSTER FOCUS** section (v2): this turn's cluster
  of related gaps.
- **v7** — turn-count economics (v2): the cluster ask covers every focus field;
  several **bundle offers** can be presented in one message (§5.4); a verdict
  turn doesn't re-ask the offer.
- **v8** — **GATE FIELDS** (v2): conditional questions unlocked by a yes to a
  gate question ride along as "record-on-yes" shadow fields; a no records none
  of them.
- **v9** — **OTHER FIELDS** inventory (v2): the remaining applicable fields are
  listed as reference-only, so a paragraph the user volunteers *outside* the
  focus can still be extracted against real keys — with the explicit rule that
  a fact stated for one field is never evidence for another.
- **v10** — Phase 4 presentation discipline (v2, locked brief 2026-10-26): the
  same content composed as **one natural question in flowing sentences** —
  never an enumerated list of sub-questions; fields asked earlier return as
  brief follow-ups; a "don't know" is met with acknowledge-and-move-on (the
  server parks the item deterministically; the model must not press).

Context assembled per turn (§4.1) also includes the household/person context the
prompts need to avoid re-asking (e.g. the signed-in user's name for the testator
anchor) and the retrieved approved excerpts. Sample (attorney-pending) pack
content is excluded from grounding unless `AI_ALLOW_SAMPLE_CONTENT` is
explicitly enabled (default off).

### 5.3 Why these models: the recorded-paragraph benchmark

Model selection was empirical, using a **recorded-paragraph benchmark**: fixed
recorded user paragraphs (the Sharma-scenario corpus) run through the extractor,
scored on recall/precision of extracted fields and — the disqualifying metric —
**wrong-source proposals** (values attributed to the wrong person/clause).

- Extractor: gpt-5.5 beat gpt-4o (recall .933 vs .908; wrong-source proposals
  **0 vs 6**) → v2 extraction on gpt-5.5 (config comment, `app/core/config.py`).
- Consistency reviewer: gpt-4o-mini caught 6/6 seeded contradictions but
  false-flagged 1/6 consistent cases; gpt-5.5 caught 6/6 with 0 false positives
  → `OPENAI_CONSISTENCY_MODEL = gpt-5.5` (§9.3).
- **Responses API — evaluated and rejected.** The v2 path was trialled on the
  Responses API and rejected on the recorded evidence: wrong-source extractions
  rose 0 → 4 and latency rose ~27–59% versus Chat Completions. The interview
  remains on Chat Completions + function tools. (Evidence:
  project records `interview-v2-ab-results-2026-10-24`.)
- **LLM challengers — deferred.** A challenger slate (Muse Spark 1.3 via the
  Meta API, GPT-5.4, Claude Sonnet 5) is specced against the same benchmark
  with the bar at 0 wrong-source and recall/precision ≥ .933, but the owner
  deferred the comparison on 2026-10-27 ("continue with OpenAI… I'll provide
  the keys"). Until keys arrive, OpenAI is the only provider in production.
  Standing disqualification: any `muse-spark-*-contributor` model id trains on
  prompts/files/completions and is blocklisted for estate PII.

### 5.4 Bundle offers (v2)

Some question groups are natural bundles — most importantly role bundles
(address, executor/guardian sets). Instead of asking field-by-field, the
interviewer can present a **bundle offer**: a composed statement of several
related values ("Your executor is X, your backup is Y — shall I record that?")
which the user accepts or rejects with a yes/no verdict
(`pending_bundle_offers`, `_bundle_verdict`). An accepted bundle produces
proposals with source `bundle`. This matters for §6: a bundle proposal carries
the user's *own explicit verdict*, not a model assertion, so it is the one
exception to the material-key confirmation rule.

## 6. Proposals and the confirmation gradient

### 6.1 Materiality (SEC-013)

`router._material_question_keys(pack)` derives the legally material keys from
the pack + the intake sync triggers. A key is material when confirming it writes
a person, relationship, nomination, or asset fact:

- every `person`-typed question, and every question binding to a nomination
  (`maps_to: nominations.*`);
- the household-graph sync keys not derivable from pack metadata:
  `marital_status`, `spouse_full_name`, `testator_full_name`,
  `has_minor_children`, `minor_children_details`, `asset_categories_present`;
- document-distribution keys that render directly into generated documents but
  write no graph rows: `specific_bequests`, `residuary_beneficiaries`,
  `trust_marital_share`, `trust_initial_property`, `digital_asset_wishes`
  (verified against the pack's `maps_to: facts.*` bindings and the documents
  slot table).

### 6.2 Auto-confirm

After each turn commits, `_auto_confirm_proposals` runs in **one** transaction
with **one** `save_answers` call (PERF-019 — previously one transaction + a full
save cascade per proposal):

- Candidates: proposals from this turn with `confidence ≥ 0.9` and status
  `proposed`.
- **Material keys are skipped** — they stay `proposed` and surface as
  confirmation cards. Exception: `bundle`-source proposals (§5.4).
- Each candidate value is pre-coerced with the *same* rules `save_answers`
  applies; an uncoercible value is excluded *before* deciding (otherwise the
  proposal would phantom-confirm: status confirmed, answer never written, card
  never shown).
- Decisions are compare-and-set: if the user already decided the proposal
  (e.g. rejected a fabrication while a slow turn was in flight), the CAS
  matches nothing and **the user's decision wins**.
- Only confirmations that actually committed are reflected in the response
  (progress is recomputed in the same transaction from the answers just
  written). Any failure rolls the batch back and leaves everything pending —
  the turn response is never broken by auto-confirm.

### 6.3 Manual decisions

`decide_proposal` (confirm / correct / reject endpoints):

- The decision is CAS-guarded by status (`proposed` only) — double-decides 409.
- Confirm/correct write through `intake.save_answers` (the single funnel, §7),
  so pack validation, coercion, syncs, and audit are identical to a wizard save.
  Both the proposed and corrected values are retained on a correction.
- Reject scrubs the `source_snippet` (ADR-002: only snippets the user explicitly
  confirmed are retained) and returns the key to the missing list.
- The audit event (`interview.proposal_decided`) records the decision and the
  question key — never the value (contract rule 9).

### 6.4 Document-extracted proposals

Extraction proposals are **never auto-confirmed**, at any confidence: they
always wait for an explicit confirm/correct/reject (§9.2). The gradient is
deliberately stricter for documents than for the user's own spoken words.

## 7. From answers to people: the sync layer

`intake.save_answers` (`domains/intake/service.py`) is the single write funnel
for answers from every surface (wizard saves, interview confirmations,
Review-screen edits). It validates keys against the matter's pack, coerces
values to the question types (`_coerce_answer_value` — batched saves coerce
non-strictly and drop uncoercible values, which is why auto-confirm pre-coerces
with the same function), upserts `intake_answers`, and then runs the
household-graph syncs. Each sync is **best-effort and savepoint-armored**: a
sync failure is logged and rolled back to its savepoint; it can never fail the
answer save or poison the caller's transaction.

| Sync | What it reconciles |
|---|---|
| `_sync_testator_name` | The confirmed `testator_full_name` lands on the household's primary-client person (fill a placeholder "Unknown person", or create + link when the primary_client membership has no person). Never overwrites a real human-entered name. |
| `_sync_spouse_relationship` | The testator's spouse/partner `relationships` row — the *only* spouse source the document renderer reads (§7.1). |
| `_sync_minors_from_answers` | Minor children answers → child persons. |
| `_sync_nominations_from_answers` | Nomination answers (executors, guardians, agents) → nomination records + nominee persons. |
| `_sync_assets_from_answers` | Asset-category answers → asset records. |

### 7.1 The spouse sync (2026-10-27) and the anchor rule

Until 2026-10-27 nothing in the product ever created a spouse `relationships`
row (there was no `spouse_full_name` question), so a married, interview-only
matter rendered the Will's spouse slot blank. The fix added the gated pack
question `spouse_full_name` (applicable only when `marital_status` is
married/partnered) and `_sync_spouse_relationship`, which runs inside
`save_answers` and reads the **full stored answer set** (post-upsert), so
partial saves and corrections converge identically:

- married/partnered + a captured name → exactly one row from the testator to
  that person, typed spouse/partner; a name change re-points it; a
  married↔partnered change corrects the type **in place** (the row's identity
  survives); stale rows to anyone else are removed. The family's latest
  recorded answers are authoritative — including over rows created by hand
  through the relationships API — and every mutation is audit-trailed
  (`relationships.spouse_sync_created/_updated/_removed`).
- Any other recorded marital status → the testator's spouse/partner rows are
  removed.
- No marital status recorded, or married with the name not yet captured → no
  signal; nothing is created or removed.
- Guards: a captured name resolving to a **minor**, an unmatched UUID-shaped
  value, or the testator themselves never produces a row; each abstention is
  logged, never silent. Legacy-pack matters (no `spouse_full_name` question)
  are entirely out of scope. The renderer was *not* loosened — it still reads
  only the relationships row.

**Anchor semantics.** Both the spouse sync and the renderer anchor on the
testator person — the household's adult person linked to the `primary_client`
membership. Name resolution mirrors this anchor exactly; there is no
positional or "first adult" fallback in the render path.

### 7.2 The ordering invariant (hardened 2026-10-29)

A sync's create/abstain condition must key on **the specific linkage it
serves**, never on a global row count that sibling syncs can pollute. The
failure that taught this (deep-review Phase 3, production E2E, 2026-10-29):
`_sync_testator_name` originally created the testator person only when *no
adult person at all* existed. A bulk save whose nomination sync ran first
created executor/guardian adults; the testator was therefore never minted; the
spouse sync had no anchor and abstained; and a complete married packet's Will
rendered "I am married to ___." The Attorney Reviewer flagged the packet's
unfilled placeholders — correctly. The capture now keys on the
**primary_client membership being unlinked**: if the membership is linked to a
differently-named real person, that is a genuine identity conflict and the sync
abstains rather than minting a second primary-client person. The sibling
lesson: render verification must read the composed recital prose, not just
count slot names — the earlier E2E counted name occurrences per slot and
missed the broken Article I recital entirely.

## 8. Interplay with the structured intake

### 8.1 One question model, two front-ends

The pack's question sets (`question-sets/*.yaml` per section — 21 sections in
us-va v2) define every question once: key, label, type, `required`,
`applies_if` gate, `maps_to`. The wizard renders them as forms; the interview
asks them conversationally; both write via `save_answers`.

Progress is computed by one function — `intake/question_sets.py::compute_progress`
— which partitions questions into **applicable / pending / pruned** by their
gates: pruned questions (gate answered decisively against them) leave the
denominator entirely; pending questions still count (fail-closed). The
interview's display progress (`_display_progress`) layers parked items on top:
parked questions are neither missing nor counted, so the interviewer never
nags about a deferred item. The Attorney Reviewer's completeness check reuses
the same partition — there is exactly one definition of "required and
applicable" in the system.

**Known display quirk (S1, open).** The interview *panel* can read
"1 required item still needed" and count **42 declared vs 41 applicable** at
true completion, while the Review screen and the API read 41/41 = 100%. The
partition math underneath is the shared, correct one; the discrepancy is in the
panel's declared-count display. It is display-only, root-caused, and its fix
remains gated on an explicit product decision — documented here so nobody
re-diagnoses it from scratch.

### 8.2 Parked items

Parking ("don't know yet") is a first-class state, not a skipped question:

- API: park/unpark endpoints (§2) plus a matter-level list feeding the Review
  screen's open-items list. Conversational parking also exists: a "don't
  know" in chat parks the item deterministically server-side, and the v10
  prompt instructs the model to acknowledge and move on without pressing.
- Effect: the item leaves the interview's focus and missing list (and the
  model's context treats it as deferred), but it **still gates document
  generation** — a parked required item must be answered (or its gate pruned)
  before drafts can be generated. `TurnOut.parked_required_keys` reports the
  parked-and-required set after each turn.

### 8.3 Topic clusters (Phase 4 presentation)

On the v2 path the interview works in **topic clusters**: `_gaps_by_cluster`
groups the open gaps, `next_gap_cluster` picks the cluster for the turn, and
the prompt presents that cluster as one natural line of questioning (§5.2, v10)
rather than a checklist. Partial answers within a cluster are normal — the
cluster simply remains partially open and is revisited. The wizard presents the
same grouping spatially (sections); the two views agree because both derive
from the same pack structure and progress partition.

---

## 9. Adjacent AI in the interview orbit

### 9.1 Dictation

`POST …/interview/dictate` transcribes recorded audio with the STT provider
(`AI_STT_PROVIDER`; production: OpenAI `whisper-1`, `OPENAI_WHISPER_MODEL`).
The transcript is **returned for the user's review and never auto-sent** — it
lands in the composer as editable text. Guards: the body is streamed with a
hard cap (`DICTATION_MAX_BYTES`, 8 MiB) and refused with 413 beyond it
(SEC-014); the transcription call (120s timeout in the provider) runs in a
threadpool (PERF-015). An `interview.dictated` audit event records provider +
character count, never the transcript.

> **Deadline:** OpenAI deprecated `whisper-1` on 2026-08-26 and removes it on
> **2027-02-26**. Migration to `gpt-transcribe` (or the Muse voice-transcribe
> equivalent) after a 20-clip spot-check is scheduled work, not an option.

### 9.2 Document extraction

`POST …/sessions/{sid}/extractions` (`domains/interview/extraction.py`) accepts
an uploaded source document (PDF/DOCX; 10 MB capped streaming read, SEC-014),
extracts its text locally (`text_extract.py`, with OCR fallback whose
used/attempted/unavailable states are reported distinctly), and sends **only
the extracted plain text (capped)** — never raw file bytes — to the JSON
provider (90s timeout) with the matter-path model (§5.1).

Uploads are untrusted, and the hardening is explicit:

- the document text is wrapped in `<UNTRUSTED_DOCUMENT>` delimiters and the
  system prompt forbids following any instruction inside it;
- the model must answer in a fixed JSON shape containing only known question
  keys — invented keys are discarded server-side;
- every value is type-checked, length-capped (500 chars), control-character
  stripped, and non-scalars are rejected; at most 50 proposals per run;
- each surviving value becomes a `document_extracted` `field_proposal` with
  `source_page` + `source_snippet` (the prompt caps it at 200 chars), and waits
  for explicit user confirmation (§6.4). Values for questions the family has
  already answered are skipped, and the run reports
  `skipped_existing` / `discarded` / `truncated` counts.

### 9.3 Consistency Reviewer

`domains/interview/consistency.py`, invoked via
`POST …/sessions/{sid}/consistency-review` (the family-facing "consistency
check"; it runs over *verified* facts, so it is meaningful once answers exist).

- **Inputs — a fence, not a prompt suggestion.** Only verified facts are sent:
  proposals in status `confirmed`/`corrected` (with provenance: `interview` vs
  `document`), plus the session's remaining stored `intake_answers`. Nothing
  unverified ever reaches the model.
- **Call.** One JSON-provider call (`OPENAI_CONSISTENCY_MODEL` = `gpt-5.5`,
  temperature 0, 90s timeout), phased PERF-016-style: prepare (read txn) →
  compute (no txn) → persist (write txn). Prompt version `consistency-v1`.
- **Output discipline.** Findings must be **questions** — anything not ending
  in "?" is discarded server-side. `fields` may only name keys present in the
  facts sent; a finding with no surviving fields is dropped. Severity is
  clamped to the vocabulary **`review` | `attention`** (anything else becomes
  `review`). Questions are truncated at 500 chars.
- **Persistence.** Each finding becomes an **escalation** whose rule id is a
  content hash of (question, sorted fields) — re-running the review is
  idempotent (an already-open identical finding is not duplicated). Each
  insert runs in its own **savepoint**: one bad finding degrades to a skipped
  finding + a log line; the review record and the remaining findings still
  persist (§10, case study). The run is recorded in
  `interview_consistency_reviews` + an `ai_traces` row.
- **Degradation (ERR-022).** A provider failure or unparseable output does not
  fail the request: the review persists with zero findings, the honest marker
  model id `provider_degraded` (nothing may claim the real model ran), and
  `degraded=True` in the response.

**Case study — the 2026-10-27 consistency 500.** The first production
`attention` finding ever persisted hit the `escalations` severity CHECK, which
— written in a pre-feature migration — allowed only
`info`/`review`/`blocking`. The CheckViolation rolled back the whole review
transaction and the endpoint returned a 500 in the family's face (found by the
product owner in live testing, request_id `79c2faa…`). It survived every QA
pass because the model benchmark had measured raw model output only (11 of its
13 findings were `attention`) and no test had ever persisted a finding to a
real database. Fix, three layers: migration `20261027170000` extended the
CHECK; the per-finding savepoint guard above; and persistence tests that run
the real migration chain with the provider stubbed. Standing rules that came
out of it: persisted model vocabularies are validated at load/import against
their single owner (the DB CHECK is a backstop, not the contract), and a
benchmark that skips persistence proves nothing about the endpoint.

### 9.4 Ask (grounded Q&A)

`domains/ai/service.py::ask` answers family questions about the process using
the pack's approved knowledge. Retrieval is keyword-based over approved pack
documents; `GroundedTemplateProvider` composes the answer **only** from
approved excerpts (which travel with the system prompt; citations are
assembled deterministically and traced by hash in `ai_traces`). If the
question requests legal advice, Ask refuses and opens an escalation for the
attorney instead (`_ensure_open_escalation`) — the same classification the
interview turn loop uses (§4.1). Sample (attorney-pending) pack content is
excluded unless `AI_ALLOW_SAMPLE_CONTENT` is explicitly enabled (default off,
tightened 2026-10-26 — previously sample content could ground answers
silently).

## 10. Reliability, degradation, and limits

| Concern | Behavior (as coded) |
|---|---|
| Provider transport failure on a turn | `ProviderError` → generic 502; nothing persisted (the call runs outside any transaction); internals logged server-side only |
| Provider failure in consistency review | Degraded review persisted (zero findings, `provider_degraded`, `degraded=True`) — never a 500, never a fake success (ERR-022) |
| Undecryptable history bodies | Skipped individually; the count escalates to an ERROR log with the session id, and the response carries `history_incomplete: true` so the client may show a non-blocking notice (ERR-009). The turn still runs on the surviving history |
| Advice/strategy request | Deterministic refusal turn: persisted like a normal turn (both sides stored), `refused=True`, and an escalation is opened for the attorney. No model call is made |
| Auto-confirm failure | Batch rolls back; proposals stay pending for cards; the turn response is unaffected (§6.2) |
| Sync failure (persons/relationships) | Savepoint rollback + log; the answer save succeeds (§7) |
| Flag-read failure for `interview_v2` | Falls back to the v1 path for that turn |
| Turn rate limit | Per-user per-minute + per-day ceilings counted from stored turns → `RateLimitError` (429-class) |
| Session turn cap | 200 turns (`INTERVIEW_MAX_TURNS_PER_SESSION`) → 409 with a layperson-readable redirect to the attorney path |
| Idempotent turn retry | Same `idempotency_key` returns the original response (`replayed=True`); a key whose reply doesn't exist yet → 409 "still being processed" |
| What a failed consistency review blocks | **Nothing.** It is advisory: submission gates on *blocking escalations and completeness*, not on the reviewer having run. A degraded review leaves no findings, which is visible in its record |

**Latency budget (from the code).** Shared HTTP client timeout 60s
(`domains/ai/provider.py::get_http_client`); JSON-provider calls (extraction,
consistency) 90s; Whisper transcription 120s. History is capped at the last 10
turns, which bounds prompt size; the phased turn design means none of these
waits holds a database connection.

## 11. Data written per turn

For one successful user turn, in the complete-phase transaction:

| Write | Detail |
|---|---|
| `interview_turns` ×2 | User + assistant rows: `summary` (truncated digest, in the clear), `citations`, `proposals_emitted`, `seq`; the user row carries the `idempotency_key` when supplied |
| `interview_turn_bodies` ×2 | The full texts, Fernet-encrypted (`INTERVIEW_TURN_ENCRYPTION_KEY`, key version recorded per body). A purge function (`purge_expired_turn_bodies`) deletes bodies once the matter has been terminal longer than `INTERVIEW_TURN_RETENTION_DAYS` (default 90), but **no scheduler invokes it in the current codebase** (§13) — until that wiring lands, bodies are retained; proposals/traces/audit are permanent by design |
| `field_proposals` ×N | Surviving proposals with provenance (source, confidence, snippet, model id, prompt version, pack version) |
| `interview_sessions` | `draft → in_progress` on the first turn (deferred write) |
| `interview_parked_items` | Any conversational parks from this turn (deferred writes) |
| `ai_traces` ×1 | Provider, model id, prompt template/version, pack version, token counts, latency, retrieved doc ids, trace hash |
| `audit_events` ×1 | `interview.turn` — summaries of both sides + proposal count + trace hash; never raw text (rule 9) |

Then, in the auto-confirm transaction (when eligible proposals exist):
`field_proposals` status updates (CAS) + `intake_answers` upserts via
`save_answers` + whatever household-graph rows the syncs reconcile
(persons/relationships/nominations/assets) + the syncs' own audit events.

Idempotency, stated precisely: **turn-level** idempotency exists (the
per-session `idempotency_key` on the user turn, above). The platform-wide
`Idempotency-Key` middleware (parent document §9.2) additionally covers
mutating requests that send the header — the web interview client does not
send one today, so turn retries rely on the turn-level mechanism.

## 12. Configuration surface

| Setting | Default | Effect |
|---|---|---|
| `AI_LLM_PROVIDER` | `deterministic_dev` (tests); `openai` in production | Chat/extraction/consistency adapter selection |
| `AI_STT_PROVIDER` | `deterministic_dev`; `whisper` in production | Dictation adapter |
| `OPENAI_INTERVIEW_MODEL` | `gpt-4o` | v1 interview + v1 extraction |
| `OPENAI_INTERVIEW_MODEL_V2` | `gpt-5.5` | v2 interview + v2 extraction |
| `OPENAI_CONSISTENCY_MODEL` | `gpt-5.5` | Consistency Reviewer (§9.3) |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Ask + vault analysis defaults |
| `OPENAI_WHISPER_MODEL` | `whisper-1` | Dictation (removal deadline 2027-02-26) |
| `OPENAI_API_KEY` | — | Required when a provider is `openai`; missing key with openai selected is a `ConfigurationError`, not a silent fallback |
| `AI_ALLOW_SAMPLE_CONTENT` | `False` | Whether attorney-pending sample pack content may ground interview/Ask answers |
| `INTERVIEW_TURN_ENCRYPTION_KEY` / `INTERVIEW_TURN_KEY_VERSION` | — / 1 | Fernet key for turn bodies; unset key = sessions refuse to start |
| `INTERVIEW_TURN_RETENTION_DAYS` | 90 | Body purge window after a terminal matter state |
| `INTERVIEW_MAX_TURNS_PER_SESSION` | 200 | Per-session cost cap |
| `DICTATION_MAX_BYTES` | 8 MiB | Dictation upload cap |
| Flag `ai_interviewer` | True | Master gate for the whole interview surface (per firm) |
| Flag `interview_v2` | False (built-in); **enabled globally** by an environment-scope row since 2026-10-26 | v2 path selection per firm/pack (§5.1) |

Per-firm variation is entirely through the flag system (parent document §6.5):
a firm can be pinned to v1, or have the interviewer disabled, without touching
another firm.

## 13. Open questions and future work

- **LLM challenger benchmark** — deferred by the owner pending API keys
  (§5.3). The benchmark harness and the bar (0 wrong-source, ≥ .933
  recall/precision) are the acceptance gate for any switch.
- **Whisper migration** — hard deadline 2027-02-26 (§9.1).
- **S1 progress-panel display** — root-caused, display-only, awaiting an
  explicit product decision (§8.1).
- **History window** — the 10-turn context window is a fixed constant
  (`_HISTORY_TURNS`); long interviews rely on the answers/proposals state, not
  the transcript, for continuity. A summarization tier is conceivable but not
  specced in code.
- **Turn-body purge not scheduled** — `purge_expired_turn_bodies` exists and is
  tested, but no worker actor or cron invokes it; the 90-day retention window
  is therefore currently unenforced. Wiring it into the worker is a small,
  known gap.
- **Prompt-version discipline** — the v5→v10 layering (each layer byte-identical
  when inactive) is the mechanism that keeps prompt changes reviewable; any
  future layer must preserve that property and bump the traced version.
