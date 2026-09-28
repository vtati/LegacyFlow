# AI Interviewer — Design & Architecture

**Status:** Proposed (2026-09-28) — awaiting Venugopal's review before implementation.
**Decision context:** Build natively inside LegacyFlow (Option B). The conversational agent
platform (`Conversational_Agent_Platform`) is held in reserve for Phase 4 PSTN voice only.

## 1. What this is

A conversational interviewer embedded in the LegacyFlow web app that collects estate-planning
information through guided conversation, proposes structured field values with provenance,
verifies every value with the user, and produces an attorney-ready checklist. It behaves like
a meticulous intake paralegal: it gathers, organizes, cites approved sources, and flags gaps.
It never advises, never recommends a legal strategy, and never issues verdicts.

## 2. Guiding constraints

### 2.1 Product / legal (from stakeholder)

1. **No "AI attorney" framing** — in UI, marketing, code comments, or prompts. Terms used:
   intake assistant, interviewer, completeness check, consistency review, pre-review package.
2. **UPL safety:** the AI gathers facts, explains approved general information, identifies
   missing/contradictory information, and raises questions for attorney review. It does not
   issue legal conclusions or tell users what legal choice to make.
3. **Licensed attorney remains responsible** for legal advice, final review, approval, release.
4. **AI flags are open questions / checklist items for the attorney** — never verdicts.
5. **Jurisdiction packs are authoritative.** The AI cites pack content or escalates; it never
   answers from unconstrained model knowledge.
6. **Provenance on every value:** source type (`user_stated`, `document_extracted`,
   `ai_inferred`), source document/page/snippet, extraction confidence, model/provider/version,
   prompt template version, jurisdiction-pack version, user confirmation state, timestamps,
   immutable audit events.
7. **Document-derived fields are shown next to their source snippets and confirmed by a human**
   before final use. AI inference never silently becomes a legal fact.
8. **Provider-neutral AI layer:** conversational generation, extraction, review, and STT sit
   behind internal interfaces; swapping models is a configuration change.
9. **Pipeline stages are independent:** interview, ingestion, extraction, mapping, verification,
   review, attorney package — each independently retryable, observable, and scalable.
10. **Security:** PII-grade — encryption in transit/at rest, tenant isolation everywhere,
    short-lived signed URLs, full audit trail of every AI action (model, version, prompt
    template, output summary). Must be able to answer "why did the system fill this field?"

### 2.2 Engineering contract (repo CLAUDE.md — binding)

- **Modular monolith. No microservices without an ADR.** Pipeline "stages" are domain modules
  + background jobs on the existing Dramatiq worker, not new services.
- Thin routers, behavior in domain services. Pydantic v2 schemas. Python type hints.
- Explicit enums and validated state transitions (no arbitrary strings).
- Idempotency for risky/retryable writes and jobs.
- RLS + FastAPI authorization on every new table/endpoint; confirm allow AND deny.
- Synthetic data only in dev/tests. No secrets or PII in logs or fixtures.
- **Contract rule 9:** never log sensitive plan content, documents, credentials, or raw
  AI prompts/responses without redaction. (See §9 for the one deliberate, ADR-recorded
  exception this feature needs.)

## 3. What already exists (reuse, don't rebuild)

| Building block | Location | How the interviewer uses it |
|---|---|---|
| Grounded AI gateway: classify → retrieve → generate → trace | `domains/ai/` (`service.py`, `provider.py`, `router.py`) | The conversation engine. Extend the provider registry; reuse the classification/refusal pattern. |
| `AIProvider` Protocol + `DeterministicDevProvider` | `domains/ai/provider.py` | Add production providers behind the same interface (registry). |
| Pack question sets + progress math | `domains/intake/question_sets.py` (`compute_progress`, `iter_questions`, `is_answered`) | **The interview coverage checklist.** The interviewer drives toward `missing_required_keys` — no parallel checklist store. |
| Intake answers + facts dict | `domains/intake/` | Confirmed field values land here; facts feed escalation rules. |
| Escalation rules engine | `domains/escalation/` (`evaluate_intake`) | Deterministic review layer; blocking → matter ESCALATED. AI review items become escalations. |
| Review packets, clarifications, approval gates | `domains/review/` | Attorney package + final approval gate (already server-side). |
| Matter state machine | `domains/matters/state_machine.py` | Interview completion can transition matters (e.g. INTAKE_IN_PROGRESS → INTAKE_SUBMITTED) via validated transitions. |
| Document storage (private, versioned) | `domains/documents/` + `file_uploads` / `document_versions` | Ingestion pipeline stores into the same private storage. |
| Audit events | `app.core.audit.record_audit_event` | Every AI action and confirmation emits an event. |
| Jurisdiction packs (versioned YAML) | `content-packs/us-va`, `pack_loader` | Source of truth for questions, rules, educational content. |
| Feature flags | `domains/feature_flags/` | Gate the interviewer per firm (`ai_interviewer` flag). |
| UI kit + design tokens | `packages/ui`, `packages/design-tokens` | Interview components compose the existing design system. |

## 4. Architecture

### 4.1 New domain: `interview`

One new domain module, `services/api/app/domains/interview/`, following the existing
domain layout (`__init__.py`, `router.py`, `schemas.py`, `service.py`, `repository.py`,
`state_machine.py`, `prompts.py`). All interview behavior lives here; it orchestrates the
existing domains above and adds only what is missing.

```
apps/web (Next.js)
  └─ Interview experience: chat thread + coverage checklist + verification cards
        + dictation mic + citations + pause/resume
        │  HTTPS /v1 (JWT)
services/api (FastAPI, modular monolith)
  └─ domains/interview/          ← NEW: sessions, turns, proposals, state machine
     ├─ domains/ai/              ← EXTEND: provider registry (OpenAI/Anthropic/Gemini/self-hosted), STT
     ├─ domains/intake/          ← REUSE: question sets, progress, answers, facts
     ├─ domains/escalation/      ← REUSE: deterministic rules → open escalations
     ├─ domains/documents/       ← REUSE: private storage, versions
     ├─ domains/review/          ← REUSE: packets, clarifications, approval gates
     └─ worker (Dramatiq)        ← async jobs: doc ingestion, extraction, AI review
Supabase (Postgres + RLS) — system of record
```

### 4.2 Interview session state machine

```
DRAFT → IN_PROGRESS ⇄ PAUSED → COMPLETED
   ↓         ↓                    ↓
ABANDONED  ABANDONED          (matter transitions via matters state machine)
```

- One active session per (matter, question-set) pair; enforced by partial unique index.
- `IN_PROGRESS ⇄ PAUSED` is free and unlimited — pause/resume is a core requirement.
- `COMPLETED` requires `compute_progress` completeness at the pack's required threshold
  OR explicit user confirmation that remaining items go to the attorney as open questions
  (recorded as escalations — nothing silently dropped).
- All transitions validated server-side in `state_machine.py`, mirroring the matters pattern.

### 4.3 Conversation turns (synchronous, request/response)

`POST /v1/matters/{matter_id}/interview/sessions/{session_id}/turns`

Each turn runs the **extended grounded pipeline** (same shape as `/ai/ask`, §3):

1. **Classify** the user message with deterministic rules (extends the existing
   `_ADVICE_PATTERNS`): advice/strategy request → refusal + escalation record
   (`ai_advice_request`), exactly like `/ai/ask` today.
2. **Resolve interview context:** session state, answered keys, `missing_required_keys`
   from `compute_progress`, matter jurisdiction pack + version.
3. **Retrieve** pack educational content relevant to the turn (same retrieval as `/ai/ask`).
4. **Generate** via the configured provider. The system prompt frames the model as an intake
   specialist: advance the checklist conversationally, propose field values as structured
   tool calls, cite pack sources, never advise. The model does NOT write directly to intake —
   it returns **field proposals**; the service layer validates and stores them as `proposed`.
5. **Trace + persist:** model id/version, prompt template version, pack version, token counts,
   latency, retrieved doc ids, summary-only text (see §9).

**Idempotency:** turns carry a client-generated idempotency key; retried turns return the
original response instead of double-advancing the interview.

### 4.4 Field proposals + provenance (the verification UX backing)

The model never writes facts. It emits proposals; the user confirms each one.

```python
class ProposalSource(str, Enum):
    USER_STATED = "user_stated"            # parsed from the user's own words in a turn
    DOCUMENT_EXTRACTED = "document_extracted"  # from the doc pipeline, with page + snippet
    AI_INFERRED = "ai_inferred"            # model inference — always needs human review

class ProposalStatus(str, Enum):
    PROPOSED → CONFIRMED | CORRECTED | REJECTED
```

Every proposal stores: question key, proposed value, source, confidence (0–1),
`source_turn_id` and/or `source_document_id` + page + snippet excerpt, model id/version,
prompt template version, pack version, timestamps. On `CONFIRMED`/`CORRECTED`, the value
writes to intake answers with its provenance attached; the audit log records who confirmed
what, when, from which proposal.

**Verification cards (UI):** each `PROPOSED` item renders as a card — field label, proposed
value, source snippet ("You said: …" / "From deed, page 2: …"), confidence, and
Confirm / Edit / Reject. The interview cannot complete while required-key proposals are
unresolved.

### 4.5 Dictation (Whisper as an input accessory)

`POST /v1/matters/{matter_id}/interview/dictate` — accepts a short audio blob, returns a
transcript. It does **not** send the message; the transcript lands in the composer for the
user to review, edit, and send. Review-before-send is a safety feature for legal facts.

- New `STTProvider` Protocol in `domains/ai/`, same registry pattern as the LLM providers:
  `WhisperAPIProvider` now, self-hosted Whisper later via config.
- Audio is transcribed in-memory and never persisted; only the transcript the user chooses
  to send enters the turn pipeline. Rate-limited per user (cost control).

### 4.6 Provider abstraction (extends `domains/ai/provider.py`)

```python
class LLMProvider(Protocol):
    def generate(self, *, system_prompt: str, messages: list[Message],
                 context: InterviewContext, tools: list[ToolSpec]) -> LLMResponse: ...

class STTProvider(Protocol):
    def transcribe(self, audio: bytes, *, language: str | None) -> Transcription: ...
```

- Registry keyed by config (`AI_LLM_PROVIDER=openai|anthropic|gemini|self_hosted`,
  `AI_STT_PROVIDER=whisper_api|whisper_selfhosted`). Adapters are the only
  provider-specific code; domain logic never names a vendor.
- The grounding contract is unchanged and non-negotiable: providers receive
  already-retrieved approved content + interview state, and every legal claim in output
  must cite a pack source. `DeterministicDevProvider` stays for tests.
- Prompt templates are versioned files (`domains/interview/prompts/`), referenced by
  version in every trace — so "why did it say that?" is always answerable.

### 4.7 Document pipeline (Phase 2 — async jobs on the existing worker)

Uploads are untrusted (contract rule 7). Each document runs as an idempotent Dramatiq job:

```
validate (type/size/magic bytes) → malware scan → quarantine-or-accept
  → classify (deed | statement | prior will | ID | other)
  → OCR / layout extraction → entity/field extraction (LLM, provider-abstracted)
  → map to question keys → field proposals (source=document_extracted, page + snippet)
  → user verifies each proposal in the same verification-card UX
```

- Storage reuses `file_uploads` / `document_versions` (immutable versions, private).
- Extraction output is **proposals only** — same confirmation gate as interview proposals.
- Failures land in a dead-letter pattern with user-visible status ("We couldn't read page 3 —
  your attorney can review the original").

### 4.8 AI review (Phase 3 — on demand, attorney-facing)

Triggered explicitly (button: "Run completeness check"), never automatic on completion:

1. **Deterministic layer first:** `escalation.evaluate_intake` against the snapshotted pack
   rules — blocking findings transition the matter to ESCALATED (existing behavior).
2. **LLM consistency layer:** the model receives the confirmed facts + pack rules and returns
   **checklist items**: `{ severity: info|review, question: "...", cites: [...] }` —
   phrased as open questions for the attorney, never verdicts
   ("Two documents list different middle names — which is correct?").
3. Output feeds the existing **review packet** as a pre-review section; the attorney works
   through clarifications and the existing approval gate. No new release path is created —
   production release still fails closed behind the attorney gate.

## 5. Data model (new tables, all RLS-isolated by firm/matter)

- `interview_sessions` — id, matter_id, question_set_id, pack_version (snapshotted),
  status (enum), idempotency-safe, timestamps.
- `interview_turns` — id, session_id, role, summary (≤200 chars, per existing precedent),
  citations (doc ids), proposals emitted, ai_trace_id, idempotency_key (unique).
- `interview_turn_bodies` — id, turn_id, **encrypted** full text (see §9), retained per
  policy, never logged.
- `field_proposals` — id, session_id, turn_id, question_key, proposed_value (jsonb),
  source (enum), confidence, source_document_id, source_page, source_snippet,
  model_id, prompt_version, pack_version, status (enum), confirmed_by/at, timestamps.
- `ai_traces` — id, turn_id (nullable), provider, model_id, model_version,
  prompt_template_version, pack_version, token_in/out, latency_ms, retrieved_doc_ids,
  created_at. Raw prompts/responses never stored — summaries only.
- `interview_documents` — joins uploaded files to sessions with pipeline status per stage
  (each stage independently retryable).

Migrations follow the existing `supabase/migrations` convention; RLS policies tested for
allow AND deny before merge (contract quality gate).

## 6. API surface (all under `/v1`, JWT, thin routers)

| Method & path | Purpose |
|---|---|
| `POST /matters/{id}/interview/sessions` | Start (or resume the active) session — idempotent per (matter, question-set). |
| `GET /matters/{id}/interview/sessions/{sid}` | Session state + `compute_progress` checklist + unresolved proposals. |
| `PATCH /matters/{id}/interview/sessions/{sid}` | Pause / resume / abandon (validated transitions). |
| `POST /matters/{id}/interview/sessions/{sid}/turns` | Send message (idempotency key) → reply + proposals + citations. |
| `POST /matters/{id}/interview/sessions/{sid}/proposals/{pid}/confirm` | Confirm as-is → writes to intake with provenance. |
| `POST .../proposals/{pid}/correct` | Confirm edited value (stores both proposed and corrected). |
| `POST .../proposals/{pid}/reject` | Reject (recorded; key returns to missing). |
| `POST /matters/{id}/interview/dictate` | Audio → transcript (not sent; rate-limited). |
| `POST /matters/{id}/interview/documents` | Upload → enqueues ingestion job (Phase 2). |
| `POST /matters/{id}/interview/review` | Run AI review → checklist items (Phase 3). |

Authorization mirrors existing patterns: household members on their own matters;
firm staff read-only except where the pack/role allows; auditors excluded.

## 7. Frontend (`apps/web`)

One interview route (e.g. `/family/matters/{id}/interview`), composed from `packages/ui`:

- **Chat thread** — turn list with citations rendered inline; refused/escalated turns show
  the "sent to your attorney" state.
- **Coverage checklist panel** — live from `compute_progress`: per-section progress bars,
  remaining required questions; the honest "what's left" view.
- **Verification cards** — §4.4; confirm/edit/reject; block completion while required
  proposals are unresolved.
- **Dictation mic** — records, shows transcript in the composer for review before send;
  full keyboard + screen-reader labels (contract a11y gate).
- **Pause/resume** — explicit control; state survives reloads (server-side session).
- Loading / error / empty / permission-denied states for every panel (contract gate).
- **No streaming in Phase 1** — request/response keeps the trace model simple and every
  turn idempotent. (Streaming can be added later without changing the domain model.)

## 8. Non-functionals

- **Idempotency:** turns, proposal decisions, session creation, and worker jobs all carry
  idempotency keys; retries are safe by construction.
- **Rate limits:** per-user caps on turns/hour and dictation minutes/day (cost + abuse).
- **Cost controls:** per-session token budgets; provider failures degrade gracefully —
  the interview stays usable as a form ("AI is temporarily unavailable — you can continue
  answering directly"), never a dead end.
- **Performance:** pack question sets parsed once and cached; prompt templates loaded by
  version; turn p95 budget set in evals and alerted on.
- **Observability:** structured JSON logs (redacted), metrics (turn latency, proposal
  confirm/correct/reject rates, escalation rate, dictation usage), one `ai_trace` row per
  model call. No raw prompts, responses, or document text in logs — ever.
- **Evals (contract gate):** citation accuracy, refusal/escalation on advice probes,
  schema validity of proposals, tenant-isolation (no cross-matter leakage), and
  regression on the existing `/ai/ask` eval suite.

## 9. Privacy note — the one deliberate exception (requires ADR)

The existing `/ai/ask` precedent stores **summaries only** (first 200 chars) and never raw
Q&A. The interviewer cannot work that way: pause/resume, proposal provenance ("you said …"),
and attorney audit all need the actual conversation. Proposal:

- Store full turn bodies in `interview_turn_bodies`, **encrypted at rest** (column-level),
  excluded from all logs, accessible only via the matter's authorization path.
- A documented retention/purge policy (e.g. purge raw bodies N days after matter archival;
  keep proposals + traces permanently as the audit record).
- Record the rationale in an ADR: why the exception is needed, what mitigations apply,
  and what would trigger revisiting it.

This is the safest reversible option per the contract — full functionality with the
smallest possible exposure surface.

## 10. Phasing (3 phases — approved by Venugopal 2026-09-28)

- **Phase 1 — Conversational interview:** sessions + state machine, turns pipeline
  (OpenAI adapter behind the provider registry — registry stays vendor-neutral),
  Whisper dictation (OpenAI), proposals + verification UI, coverage checklist,
  pause/resume, encrypted turn-body storage + ADR (§9), `ai_interviewer` feature flag.
  Runs **alongside** the existing guided intake UI as an alternative entry point —
  it does not replace it. Text interview fully working, UPL-safe, grounded in packs.
- **Phase 2 — Document pipeline:** ingestion jobs (validate → malware scan →
  classify → OCR/extract → map to question keys), document-sourced proposals through
  the same verification UX, dead-letter handling with user-visible status.
- **Phase 3 — AI review:** explicit "Run completeness check" — deterministic
  `escalation.evaluate_intake` first, then LLM consistency pass → attorney checklist
  of open questions (never verdicts) → review packet pre-review section → existing
  attorney approval gates. No new release path; production release still fails closed.

PSTN voice via the conversational agent platform is explicitly out of scope —
reserved as a future option pending user research. Nothing in Phases 1–3 depends on it.

## 11. Decisions (recorded 2026-09-28)

1. **Encrypted turn-body storage + retention policy: APPROVED.** ADR to be written in
   Phase 1 implementation.
2. **3 phases** as in §10 (sequencing at the implementer's discretion).
3. **Day-one LLM provider: OpenAI.** Registry abstraction is still built first;
   the OpenAI adapter is the first concrete adapter. API key to be supplied via
   secure flow at build time — goes into Railway environment, never into the repo.
4. **STT: OpenAI Whisper** (API) via the new `STTProvider` registry.
5. **Coexistence:** the interviewer runs **alongside** the guided intake UI as an
   alternative, not a replacement. Both write to the same intake answers store with
   provenance, so a user can switch between them freely.

## 12. Phase 1 build sequence (co-working plan)

Each step is independently testable and shippable; contract quality gates
(format, lint, types, tests, RLS allow+deny, OpenAPI/client updates) apply throughout.

1. **ADR** — record the §9 encrypted turn-body storage decision (rationale,
   mitigations, retention/purge policy, revisit triggers).
2. **Migration** — `interview_sessions`, `interview_turns`, `interview_turn_bodies`
   (encrypted), `field_proposals`, `ai_traces`; RLS policies; allow+deny tests.
3. **Provider registry** — `LLMProvider` / `STTProvider` protocols + config-driven
   registry in `domains/ai/`; OpenAI chat adapter; Whisper STT adapter;
   versioned prompt templates under `domains/interview/prompts/`; keep
   `DeterministicDevProvider` for tests.
4. **Interview domain** — `state_machine.py` (session transitions), `service.py`,
   `repository.py`, `schemas.py`, thin `router.py`: session start/resume/get,
   pause/resume/abandon.
5. **Turn pipeline** — extend the `/ai/ask` classify→retrieve→generate→trace shape:
   advice-pattern refusal reuse, interview context assembly (session state +
   `missing_required_keys`), tool-call proposals, idempotency keys, trace rows.
6. **Proposal decisions** — confirm / correct / reject endpoints; confirmed values
   write to intake answers with full provenance; audit events.
7. **Dictation endpoint** — audio → Whisper transcript (returned, not sent);
   per-user rate limits.
8. **Feature flag** — `ai_interviewer` gated per firm.
9. **Frontend** — interview route: chat thread with inline citations, coverage
   checklist panel, verification cards, dictation mic, pause/resume; a11y labels;
   loading/error/empty/permission-denied states. Composed from `packages/ui`.
10. **Evals + hardening** — refusal on advice probes, citation accuracy, tenant
    isolation, proposal schema validity; turn p95 budget; cost/rate-limit guards.
