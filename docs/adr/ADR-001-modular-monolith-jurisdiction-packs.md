# ADR-001: Modular Monolith with Jurisdiction Packs

**Status:** Accepted
**Date:** 2026-09-26
**Deciders:** LegacyFlow engineering
**Context refs:** Product artifacts §§6.1–6.4 (architecture), §7 (data model)

## Context

LegacyFlow is a multi-tenant, attorney-enabled estate-planning workflow platform that
must expand state-by-state, starting with Virginia (`US-VA`). Early scale is a single
design-partner firm and 10–20 pilot matters; cost control and development velocity
matter more than independent deployability of components. At the same time, legal
behavior must never be hard-coded to a state, firm, or template — every rule,
content block, clause variant, and signing checklist must come from versioned,
auditable, attorney-approved sources.

Two questions had to be settled before building:

1. **Deployment shape:** modular monolith vs. microservices.
2. **Jurisdiction modeling:** how state-specific behavior is represented and evolved
   without scattering `if state ==` conditionals through the codebase.

## Decision

### 1. Modular monolith (FastAPI), not microservices

- One FastAPI application organized into domain modules
  (`identity`, `matters`, `family`, `assets`, `planning`, `legal_content`,
  `attorney_review`, `documents`, `signing`, `funding`, `ai`, `notifications`, `audit`).
- One worker process (Redis + Dramatiq) for async jobs; one Postgres (Supabase);
  one Redis. No Kubernetes.
- Module boundaries are enforced by convention and recorded in `CLAUDE.md`:
  routers stay thin, domain services own business logic, repositories hide
  persistence, cross-domain actions go through explicit application services and
  outbox events.
- A new microservice requires a new ADR demonstrating operational or security
  benefit.

### 2. Jurisdiction-pack architecture

- All jurisdiction-specific behavior ships as **versioned jurisdiction packs**
  (jurisdiction → pack versions with manifests, legal sources, content versions,
  rules, signing guidance, funding playbooks).
- A matter is associated with an **immutable pack version**; existing matters keep
  their applied pack even when newer packs are released.
- Firm overlays may customize content but can never override platform safety
  constraints.
- A **synthetic jurisdiction pack (`US-XZ`)** exists for automated tests: it
  produces visibly different question/signing behavior without code changes, so
  tests can prove no logic is hard-coded to Virginia.
- No client, API, or worker code may branch on a hard-coded state; behavior is
  resolved through the generic jurisdiction-pack engine.

## Consequences

**Positive:**

- Single deployable keeps pilot operations simple and cheap (one API service, one
  worker, managed Supabase/Redis).
- Module boundaries give most of the maintainability benefits of services without
  the distributed-systems overhead (no cross-service auth, versioning, or tracing
  to build yet).
- Jurisdiction packs make state expansion a content/attorney workflow, not an
  engineering project; Virginia-first does not become Virginia-only.
- Immutable pack versions + audit trail satisfy the legal-content versioning
  requirement; `US-XZ` gives tests a real "second jurisdiction" to catch
  hard-coding.

**Negative / costs:**

- Module-boundary discipline is conventional, not compiler-enforced; it needs
  code review and lint rules to hold.
- Pack schema changes are migrations of content, not code — pack versioning,
  migration, and validation tooling must be built and maintained.
- If one domain later needs independent scaling or a separate trust boundary,
  extraction from the monolith will require refactoring (accepted: an ADR will
  govern that move when warranted).

**Risks and mitigations:**

- _Risk:_ developers sneak state-specific branches into code. _Mitigation:_
  `US-XZ` synthetic-pack tests assert generic resolution; code review checklist in
  `CLAUDE.md`.
- _Risk:_ pack content ships without attorney approval. _Mitigation:_ pack
  lifecycle states (`draft` → `internal_testing` → `attorney_review` →
  `approved` → `retired`) enforced server-side; matters can only bind approved
  packs.
