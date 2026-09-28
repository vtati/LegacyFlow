# ADR-002: Encrypted Storage of AI Interviewer Turn Bodies

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** Venugopal Tati (stakeholder), LegacyFlow engineering
**Context refs:** `docs/ai-interviewer-design.md` §9; CLAUDE.md contract rule 9

## Context

The existing `/ai/ask` gateway (Slice 4) deliberately stores **summaries only**
(first 200 characters) and never persists raw questions or answers. This is the
right posture for a Q&A endpoint: the answer is self-contained, and nothing is lost
by discarding the raw text.

The AI Interviewer (Phase 1) cannot work that way. Three requirements force us to
retain actual conversation content:

1. **Pause/resume.** A user must be able to leave mid-interview and continue later
   with full context. Re-deriving context from 200-char summaries degrades answer
   quality and breaks the conversational experience.
2. **Proposal provenance.** Verification cards show "You said: …" next to each
   proposed field value. The source snippet must be the user's actual words, not a
   truncated summary — a wrong snippet next to a legal fact is worse than none.
3. **Attorney audit.** When the attorney reviews the pre-review package, the
   interview transcript is the evidence trail for *why* each confirmed value exists.
   Summaries are not auditable.

So the choice is not "store vs. don't store" — it is "store safely vs. ship a
broken feature." This ADR records the safest reversible option.

## Decision

Store full turn bodies in a dedicated `interview_turn_bodies` table with:

- **Column-level encryption (Fernet, AES-128-CBC + HMAC)** via the `cryptography`
  package. The data key comes from a new `INTERVIEW_TURN_ENCRYPTION_KEY` setting
  (Railway environment, never in the repo, never in browser code). Key rotation is
  supported by key-version prefixing on the ciphertext.
- **Fail-closed reads/writes:** if the key is unset, turn-body writes raise a
  configuration error and the interview endpoint refuses to start a session —
  never silently store plaintext.
- **No logging:** turn bodies are excluded from all structured logs, error reports,
  and audit `after_summary` payloads (contract rule 9 still applies everywhere else).
- **Access path:** readable only through the interview domain service, which
  enforces the same matter authorization (`get_matter_scope`) as everything else,
  plus RLS on the table.
- **Retention/purge:** raw bodies are purged N days after the matter reaches a
  terminal state (`PLAN_IMPLEMENTED` / `ARCHIVED`), where N is configurable
  (`INTERVIEW_TURN_RETENTION_DAYS`, default 90). Proposals, traces, and audit
  events are permanent — they are the durable audit record and contain no raw
  conversation text beyond short snippets the user explicitly confirmed.
- **Separation:** summaries (for list views, metrics) live on `interview_turns`;
  raw bodies live only in `interview_turn_bodies`. No join is needed for normal
  operation, so a compromised read path for one does not imply the other.

## Alternatives considered

1. **Summaries only (status quo).** Rejected: breaks pause/resume fidelity,
   provenance snippets, and attorney audit (§Context).
2. **Reconstruct context from confirmed proposals alone.** Rejected: loses the
   conversational nuance the attorney needs ("why did the user hesitate here?"),
   and the model still needs recent raw turns for quality.
3. **Client-side storage of history.** Rejected: the server needs the history for
   the turn pipeline, and pushing PII to browser storage widens the exposure
   surface (XSS, shared devices).

## Consequences

- New `cryptography` dependency with documented reason (this ADR).
- New setting `INTERVIEW_TURN_ENCRYPTION_KEY` required in staging/production;
  deployments fail fast when missing (same pattern as `DOWNLOAD_TOKEN_SECRET`).
- A key-rotation runbook is owed before production (not staging).
- If a future review finds a safer pattern, this decision reverses cleanly:
  stop writing bodies, purge the table, and the proposals + traces remain a
  complete (if less rich) audit record.

## Revisit triggers

- A data-incident involving the key or the table.
- Postgres-level encryption (e.g. TDE) making app-level encryption redundant.
- Regulatory guidance requiring a different retention posture.
