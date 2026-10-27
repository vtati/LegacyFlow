# Observability Runbook

How to see what LegacyFlow is doing, what to alert on, and how to recover.
Paths are relative to the repo root.

## Logging

- **API**: structured single-line JSON via `services/api/app/core/logging.py`
  (`JSONFormatter`, stdlib only). Every record carries:
  `timestamp`, `level`, `logger`, `message`, `request_id`, `service`
  (`service` is always `"legacyflow-api"`). Enabled in `create_app()`
  (`services/api/app/main.py`) with level from `LOG_LEVEL` (default `INFO`).
- **Correlation**: the request-ID middleware reads `X-Request-ID` (or mints
  one), stores it on `request.state`, binds it into the log context, and
  echoes it back as the `X-Request-ID` response header, alongside
  `X-Response-Time`. Pass your own `X-Request-ID` when reproducing an issue
  to trace it end to end.
- **Worker**: plain stdlib logging (`logging.getLogger(__name__)` in
  `services/worker/worker/*.py`) — not yet JSON. If you aggregate logs,
  parse the API's JSON and the worker's text separately.
- **Rule (engineering contract §9):** never log PII, plan content, documents,
  credentials, or raw AI prompts/responses. Log identifiers (request IDs,
  resource UUIDs) and counts only. Validation errors summarize field names,
  never values (`services/api/app/main.py`).

Example:

```bash
# follow API logs and filter one request
tail -f /var/log/legacyflow/api.log | jq 'select(.request_id=="<id>")'
```

## Health checks

- `GET /healthz` → `{"status":"ok","service":"legacyflow-api","version":"0.1.0","env":"…"}`
  (`services/api/app/main.py`). Use it for load-balancer / container health checks.
- `GET /` → service name, version, and links to `/docs` and `/healthz`.
- `GET /openapi.json` / `GET /docs` — live API contract.

## Sentry

- Optional. Set `SENTRY_DSN` (see `services/api/app/core/config.py`); when
  empty, Sentry is skipped entirely.
- Initialized in `services/api/app/main.py::_init_sentry()` with
  `send_default_pii=False` and the service release tag — error reports never
  carry PII or plan content by construction.
- `sentry-sdk` is an optional dependency: the API runs without it installed.
- **Pilot action:** set `SENTRY_DSN` in the pilot environment; confirm one
  test event arrives before the pilot starts.

## What to alert on

| Signal                                                                     | Where                                             | Threshold / action                                                                                                       |
| -------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 5xx rate                                                                   | API JSON logs (`level: ERROR`, unhandled handler) | page on sustained >1% of requests                                                                                        |
| 429 rate                                                                   | API logs / `Retry-After` responses                | warn on spike — may indicate abuse or a too-tight limit; `RATE_LIMIT_ENABLED=false` is the incident kill switch (config) |
| `worker_broker_unavailable` (501 from `POST /v1/admin/reminders/dispatch`) | API logs                                          | reminder dispatch degraded — check `REDIS_URL` and the worker                                                            |
| File scans `quarantined`                                                   | `file_uploads` scan status / worker logs          | investigate the upload; quarantine is working as designed                                                                |
| `document_assembly_disabled` (409) spikes                                  | API logs                                          | usually a misconfigured firm/pack, not an outage — check `document_assembly` flag + pack capabilities                    |
| Reminder dispatch runs with 0 sent for >1h during business hours           | `audit_events` (`action='reminder.sent'`)         | cron may be down — see below                                                                                             |
| Notification outbox `failed` rows                                          | `notification_outbox` table                       | worker `send_notifications` failing; sandbox writes land in `/tmp/legacyflow-mail` (dev)                                 |
| Auth failures spike (401/403)                                              | API logs                                          | possible credential rotation issue or JWKS outage (`SUPABASE_JWKS_URL`)                                                  |

## Audit events

All sensitive actions append to the **`audit_events`** table (append-only: no
UPDATE/DELETE RLS policies; `services/api/app/core/audit.py` only INSERTs).
Payloads are summaries — never raw content. Query by `correlation_id` to
reconstruct a request's side effects:

```sql
select action, resource_type, actor_type, created_at
from audit_events
where correlation_id = '<uuid-from-X-Request-ID>'
order by created_at;
```

Notable actions: `reminder.sent`, gate decisions, pack imports, invitation
events, matter transitions (also mirrored in `matter_status_history`).

## Reminder dispatch schedule

- The worker actor is `send_due_reminders` in
  `services/worker/worker/actors.py` (plain-function core
  `dispatch_due_reminders`, directly testable). It enqueues `reminder.due`
  notifications for due, opted-in reminders and marks them `sent_at`.
- **Cadence: every 15 minutes — provided by the deployment, not the repo.**
  There is no in-repo scheduler. Recommended: a cron entry such as
  `*/15 * * * *` that calls the admin endpoint (firm_admin JWT required):
  ```bash
  curl -s -X POST https://api.legacyflow.example/v1/admin/reminders/dispatch \
    -H "Authorization: Bearer $CRON_JWT"
  # → 202 {"dispatched": true, …}
  ```
  The endpoint only enqueues one run on the shared Dramatiq broker (Redis via
  `REDIS_URL`); it does not replace the cron. Without Redis it returns 501
  `worker_broker_unavailable`.
- Notification preferences are honored: users can toggle reminders at
  `PUT /v1/me/notification-preferences` or one-click
  `POST /v1/me/unsubscribe`; subjects stay generic (`reminder.due` →
  "A reminder from LegacyFlow") per FR-FUND-004.

## Backup / restore (Supabase Postgres)

Postgres is the system of record. Supabase manages backups:

- **PITR / daily backups:** enable Point-in-Time Recovery on the Supabase
  project for the pilot; verify the backup schedule in the dashboard before
  any real data exists.
- **Manual snapshot:** `pg_dump "$DATABASE_URL" -Fc -f legacyflow-$(date +%F).dump`
  (`DATABASE_URL` is the direct Postgres connection string).
- **Restore drill:** restore to a throwaway project, apply
  `supabase/migrations/*.sql` in filename order, then load the dump; run the
  RLS suite (`supabase/tests`) against it before pointing traffic at it.
- **Migrations are the schema source of truth** — never hand-edit production
  schema; add a new migration file instead.

## Interview turn encryption key (`INTERVIEW_TURN_ENCRYPTION_KEY`)

Interview turn bodies (the verbatim conversation) are Fernet-encrypted at
rest under this single key (ADR-002; `app/core/crypto.py`). Treat the key
like a database credential: it lives only in the deployment's secret store,
is backed up with the same care as the database credentials, and is never
logged, committed, or pasted into tickets.

- **Unset or invalid** — the interviewer fails closed by design: sessions
  cannot start and existing bodies cannot be read. This is the safe state,
  not an outage to "fix" by disabling encryption.
- **Key loss** — every `interview_turn_bodies` ciphertext becomes
  permanently unreadable; there is no recovery path. What survives: field
  proposals, `ai_traces`, saved intake answers, escalations, and the rest
  of the structured record — those are the permanent audit trail, and they
  are not encrypted under this key. The loss is the verbatim transcript
  text (which is retention-purged `INTERVIEW_TURN_RETENTION_DAYS` after a
  matter reaches a terminal state anyway), not the legal record.
- **Rotation** — there is NO re-encryption migration today. Each body row
  stores a `key_version` label, but decryption always uses the single
  configured key, so changing the env var (with or without bumping
  `INTERVIEW_TURN_KEY_VERSION`) strands all previously encrypted bodies
  exactly like a loss. Do not rotate this key until a re-encryption
  migration (decrypt under old key, re-encrypt under new, per row) has
  been written, tested, and run; rotate the database credentials freely,
  but schedule this key's rotation as its own project.

## Incident quick reference

1. **API down** — check `GET /healthz`; container logs (JSON) for tracebacks;
   Sentry if configured.
2. **Suspected abuse** — check 429 volume; tighten per-route tiers or flip
   `RATE_LIMIT_ENABLED=false` only as a last resort (it disables protection).
3. **Stuck reminders** — hit the dispatch endpoint manually (202 expected);
   check worker process + Redis; inspect `reminders` rows with
   `sent_at IS NULL AND scheduled_for <= now()`.
4. **Bad deploy** — the API is stateless; roll back the container. Schema
   changes only move forward via new migrations.
