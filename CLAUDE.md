# LegacyFlow Engineering Contract

## Mission
Build a secure, maintainable, multi-tenant and multi-jurisdiction attorney-enabled estate-planning workflow platform. Virginia is the first content pack. This repository must never hard-code legal behavior to a state, firm, or template.

## Non-negotiable safety rules
1. Never implement autonomous legal advice, legal strategy selection, or claims of legal validity.
2. AI can only use approved, scoped content and must return citations or escalate.
3. No final legal document release without an explicit server-side attorney approval gate.
4. All legal content, rules, templates, and clauses must be versioned and auditable.
5. All firm/client data must be tenant-isolated via Supabase RLS and FastAPI authorization.
6. Do not place Supabase service role keys or other privileged secrets in browser code.
7. User-uploaded files are untrusted; validate, isolate, scan, and store privately.
8. Use synthetic data only in development and tests.
9. Never log sensitive plan content, documents, credentials, or raw AI prompts/responses without redaction.
10. If requirements are ambiguous, implement the safest reversible option and record an ADR/TODO; do not invent legal behavior.

## Architecture
- Monorepo: Next.js web, FastAPI API, Python worker, shared contracts/UI/tokens.
- Modular monolith: organize business logic by domains. Do not create microservices without an ADR.
- Postgres is system of record. Frontend does not directly access privileged tables.
- All writes are validated server-side and emit auditable events where sensitive.
- State-specific behavior comes from jurisdiction packs, never scattered `if state ==` logic.

## Quality gates
Before claiming any task is complete:
- Run formatting, lint, type checking, unit tests, affected integration tests.
- Add/adjust tests for new domain behavior and authorization paths.
- Confirm RLS allow and deny behavior for new exposed tables.
- Confirm API changes update OpenAPI/client contracts.
- Confirm loading, error, empty, and permission-denied states exist in UI.
- Confirm accessibility labels/keyboard behavior for interactive UI.
- Confirm no secrets or PII entered logs/test fixtures.

## Coding conventions
- TypeScript strict mode; avoid `any`.
- Python type hints required; Pydantic v2 request/response schemas.
- Keep routers thin; put behavior in domain services.
- Use idempotency for risky/retryable writes/jobs.
- Prefer explicit enums/state transitions over arbitrary strings.
- Prefer small, focused commits and files.
- Do not add dependencies without documented reason.

## Testing
- Use synthetic persons/assets only.
- Unit test rules and state transitions.
- Integration test authorization/RLS behavior.
- Playwright test the critical invite → intake → review → demo document → funding journey.
- AI evaluations test citations, refusal/escalation, schema validation, and isolation.
