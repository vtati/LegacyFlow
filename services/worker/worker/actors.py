"""Slice-1 stub actors. No real jobs exist yet; jobs arrive in Slice 4."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dramatiq

from worker.broker import get_broker
from worker.db import get_connection
from worker.scanning import perform_file_scan
from worker.settings import get_worker_settings

logger = logging.getLogger(__name__)

broker = get_broker()


@dramatiq.actor(broker=broker)
def example_ping() -> dict[str, bool]:
    """Slice-1 stub actor: logs and returns ``{"pong": True}``.

    Exists only to prove the broker wiring end to end; it performs no real
    work and must not be treated as a production job.
    """
    logger.info("example_ping executed")
    return {"pong": True}


# ---------------------------------------------------------------------------
# Notification delivery (Slice 3)
# ---------------------------------------------------------------------------

_SUBJECTS: dict[str, str] = {
    "matter.submitted": "A matter was submitted for attorney review",
    "clarification.created": "Your attorney has a question about your matter",
    "clarification.responded": "A clarification was answered",
    "gate.decided": "An approval gate was decided",
    "invitation.created": "You are invited to LegacyFlow",
    # Reminder subjects stay generic (FR-FUND-004): the reminder type and
    # details live in the payload/body, never in the subject line.
    "reminder.due": "A reminder from LegacyFlow",
}


def _sandbox_path(outbox_id: UUID) -> Path:
    settings = get_worker_settings()
    return Path(settings.NOTIFICATION_SANDBOX_DIR) / f"{outbox_id}.json"


def _render_notification(row: dict[str, Any]) -> dict[str, Any]:
    """Render an outbox row into the email-sandbox payload."""
    outbox_id = UUID(str(row["id"]))
    template_key = str(row["template_key"])
    return {
        "outbox_id": str(outbox_id),
        "to": row.get("recipient_email"),
        "to_user_id": str(row["recipient_user_id"]) if row.get("recipient_user_id") else None,
        "template_key": template_key,
        "subject": _SUBJECTS.get(template_key, f"LegacyFlow notification: {template_key}"),
        "payload": row.get("payload") or {},
        "rendered_at": datetime.now(UTC).isoformat(),
    }


def _write_to_sandbox(rendered: dict[str, Any], outbox_id: UUID) -> None:
    """Write the rendered notification to the sandbox directory.

    This is the email sandbox: a local JSON file per message. Real email is
    never sent here.
    """
    path = _sandbox_path(outbox_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rendered, indent=2, default=str), encoding="utf-8")


@dramatiq.actor(broker=broker)
def send_notifications(batch_size: int = 25) -> dict[str, int]:
    """Drain pending ``notification_outbox`` rows through the email sandbox.

    Each pending row is rendered to a JSON file in the sandbox directory
    and marked ``sent``; rows that raise are retried up to
    ``NOTIFICATION_MAX_ATTEMPTS`` and then marked ``failed``. Returns a
    small ``{"sent": n, "failed": m}`` ledger.
    """
    return drain_outbox(batch_size)


def drain_outbox(batch_size: int = 25) -> dict[str, int]:
    """Plain-function core of :func:`send_notifications` (directly testable)."""
    settings = get_worker_settings()
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL is not set; send_notifications has nothing to drain.")
        return {"sent": 0, "failed": 0}

    sent = 0
    failed = 0
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM notification_outbox
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (batch_size,),
                )
                rows = [dict(row) for row in cur.fetchall()]

            for row in rows:
                outbox_id = UUID(str(row["id"]))
                try:
                    # ERR-021: per-row savepoint. A Postgres-level failure
                    # (constraint, trigger, serialization) aborts only this
                    # row's nested transaction; without it the recovery
                    # UPDATE below would run on an aborted transaction,
                    # raise InFailedSqlTransaction, and roll back rows
                    # already marked sent (-> duplicate notifications).
                    with conn.transaction():
                        rendered = _render_notification(row)
                        _write_to_sandbox(rendered, outbox_id)
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE notification_outbox
                                SET status = 'sent', attempts = attempts + 1, sent_at = now()
                                WHERE id = %s
                                """,
                                (str(outbox_id),),
                            )
                    sent += 1
                except Exception:  # a failing row must not kill the batch
                    logger.exception("send_notifications failed for outbox row %s", outbox_id)
                    # Recovery UPDATE runs AFTER the failed savepoint has
                    # rolled back, in its own savepoint, on a healthy
                    # transaction.
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE notification_outbox
                                SET attempts = attempts + 1,
                                    status = CASE
                                        WHEN attempts + 1 >= %s THEN 'failed'
                                        ELSE 'pending'
                                    END
                                WHERE id = %s
                                """,
                                (settings.NOTIFICATION_MAX_ATTEMPTS, str(outbox_id)),
                            )
                    failed += 1
    logger.info("send_notifications processed batch: sent=%d failed=%d", sent, failed)
    return {"sent": sent, "failed": failed}


# ---------------------------------------------------------------------------
# Reminder dispatch (Slice 4, Track B4 — wires documents.service.send_reminders)
# ---------------------------------------------------------------------------

_REMINDER_TEMPLATE_KEY = "reminder.due"


@dramatiq.actor(broker=broker)
def send_due_reminders(batch_size: int = 100) -> dict[str, int]:
    """Enqueue ``reminder.due`` notifications for due, opted-in reminders.

    Deployment schedule: every 15 minutes (see the worker deployment notes;
    the API also exposes ``POST /v1/admin/reminders/dispatch`` so a firm
    admin can trigger one run on demand). The plain-function core
    :func:`dispatch_due_reminders` is directly testable.
    """
    return dispatch_due_reminders(batch_size)


def _count_suppressed_reminders(cur: Any) -> int:
    """Count due reminders suppressed by notification preferences (no lock)."""
    cur.execute(
        """
        SELECT COUNT(*) AS suppressed
        FROM reminders r
        LEFT JOIN notification_preferences np ON np.user_id = r.user_id
        WHERE r.sent_at IS NULL
          AND r.scheduled_for <= now()
          AND NOT (np.user_id IS NULL
                   OR (np.reminder_enabled AND np.unsubscribed_at IS NULL))
        """,
    )
    return int(cur.fetchone()["suppressed"])


def _select_due_reminders(
    cur: Any, batch_size: int
) -> tuple[list[dict[str, Any]], int | None]:
    """Lock and return due reminders whose recipients have not opted out.

    Users with ``notification_preferences.reminder_enabled = false`` or a
    non-null ``unsubscribed_at`` are skipped (their rows stay unsent so a
    later re-subscribe still delivers them).

    PERF-007: the suppressed count rides along as a scalar column on the
    same query (one statement, supported by the partial index on
    ``reminders (scheduled_for) WHERE sent_at IS NULL``) instead of a
    separate full COUNT on every dispatch. Returns ``(rows, count)``;
    the count is ``None`` when no deliverable row was selected (the
    caller falls back to the standalone count on that cold path only).
    """
    cur.execute(
        """
        SELECT r.*, (
            SELECT COUNT(*)
            FROM reminders r2
            LEFT JOIN notification_preferences np2 ON np2.user_id = r2.user_id
            WHERE r2.sent_at IS NULL
              AND r2.scheduled_for <= now()
              AND NOT (np2.user_id IS NULL
                       OR (np2.reminder_enabled AND np2.unsubscribed_at IS NULL))
        ) AS suppressed_count
        FROM reminders r
        LEFT JOIN notification_preferences np ON np.user_id = r.user_id
        WHERE r.sent_at IS NULL
          AND r.scheduled_for <= now()
          AND (np.user_id IS NULL
               OR (np.reminder_enabled AND np.unsubscribed_at IS NULL))
        ORDER BY r.scheduled_for ASC
        LIMIT %s
        FOR UPDATE OF r SKIP LOCKED
        """,
        (batch_size,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return rows, None
    suppressed = int(rows[0].get("suppressed_count") or 0)
    for row in rows:
        row.pop("suppressed_count", None)
    return rows, suppressed


def _enqueue_reminder_notification(cur: Any, reminder: dict[str, Any]) -> None:
    """Queue one ``reminder.due`` outbox row for a due reminder."""
    payload = dict(reminder.get("payload") or {})
    cur.execute(
        """
        INSERT INTO notification_outbox
          (firm_id, matter_id, recipient_user_id, template_key,
           payload, status, attempts)
        VALUES (%s, %s, %s, %s, %s::jsonb, 'pending', 0)
        """,
        (
            str(reminder["firm_id"]),
            str(reminder["matter_id"]) if reminder.get("matter_id") else None,
            str(reminder["user_id"]),
            _REMINDER_TEMPLATE_KEY,
            json.dumps(
                {
                    "reminder_id": str(reminder["id"]),
                    "reminder_type": reminder["reminder_type"],
                    "scheduled_for": str(reminder["scheduled_for"]),
                    **payload,
                },
                default=str,
            ),
        ),
    )


def _mark_reminder_sent(cur: Any, reminder_id: UUID) -> bool:
    """Mark a reminder sent; idempotent (only fires when still unsent)."""
    cur.execute(
        "UPDATE reminders SET sent_at = now() WHERE id = %s AND sent_at IS NULL",
        (str(reminder_id),),
    )
    rowcount: int = cur.rowcount or 0
    return rowcount == 1


def _audit_reminder_sent(cur: Any, reminder: dict[str, Any]) -> None:
    """Record the auditable ``reminder.sent`` event (worker actor)."""
    cur.execute(
        """
        INSERT INTO audit_events
          (id, firm_id, matter_id, actor_user_id, actor_type, action,
           resource_type, resource_id, after_summary, metadata)
        VALUES (%s, %s, %s, NULL, 'worker', 'reminder.sent',
                'reminders', %s, %s::jsonb, '{}'::jsonb)
        """,
        (
            str(uuid4()),
            str(reminder["firm_id"]),
            str(reminder["matter_id"]) if reminder.get("matter_id") else None,
            str(reminder["id"]),
            json.dumps({"reminder_type": reminder["reminder_type"]}),
        ),
    )


def dispatch_due_reminders(batch_size: int = 100) -> dict[str, int]:
    """Plain-function core of :func:`send_due_reminders` (directly testable).

    Selects due reminders (``sent_at IS NULL AND scheduled_for <= now()``)
    under ``FOR UPDATE SKIP LOCKED``, skips recipients who disabled
    reminders or unsubscribed, enqueues one ``notification_outbox`` row per
    delivered reminder with a generic subject (FR-FUND-004), marks
    ``sent_at`` idempotently, and records an audit event per send.

    Returns ``{"processed": p, "queued": q, "skipped": s, "failed": f}``
    where ``processed`` counts due rows examined, ``queued`` counts only
    successful enqueues (FUNC-017), and ``failed`` counts rows whose
    enqueue failed (ERR-002 — previously indistinguishable from a
    successful run). Never logs reminder payloads or recipient PII —
    only ids and counts (contract rule 9).

    Quarantine note (ERR-002): the ``reminders`` schema has no
    attempts/failure-state column, so a poison row cannot be given a
    terminal failed state here — it stays unsent and is retried on the
    next scheduled dispatch, but every failure is now exception-logged
    with its id and counted in ``failed``; permanently failing ACTOR
    messages (as opposed to single rows) land in ``worker_dead_letters``
    via the ERR-003 dead-letter path.
    """
    settings = get_worker_settings()
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL is not set; send_due_reminders has nothing to dispatch.")
        return {"processed": 0, "queued": 0, "skipped": 0, "failed": 0}

    processed = 0
    queued = 0
    skipped = 0
    failed = 0
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                due, folded_skipped = _select_due_reminders(cur, batch_size)
                # PERF-007: the folded count covers the hot path; the
                # standalone COUNT only runs when nothing deliverable
                # was selected (cold path) so `skipped` stays truthful.
                skipped = (
                    folded_skipped
                    if folded_skipped is not None
                    else _count_suppressed_reminders(cur)
                )
            for reminder in due:
                # FUNC-017: counters track reality — ``processed``
                # counts rows examined, ``queued`` counts only
                # successful enqueues. Each reminder runs in its own
                # savepoint so a failing enqueue neither aborts the
                # batch nor gets counted (or marked sent).
                processed += 1
                reminder_id = UUID(str(reminder["id"]))
                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            _enqueue_reminder_notification(cur, reminder)
                            if not _mark_reminder_sent(cur, reminder_id):
                                raise RuntimeError(
                                    "reminder was already marked sent; "
                                    "rolling back the duplicate enqueue"
                                )
                            _audit_reminder_sent(cur, reminder)
                except Exception:
                    # ERR-002: exception-level log WITH the reminder id
                    # (was a warning) + honest failure count.
                    logger.exception(
                        "send_due_reminders: reminder %s could not be queued; "
                        "leaving it unsent",
                        reminder_id,
                    )
                    failed += 1
                    continue
                queued += 1
    logger.info(
        "send_due_reminders processed batch: processed=%d queued=%d skipped=%d failed=%d",
        processed,
        queued,
        skipped,
        failed,
    )
    return {"processed": processed, "queued": queued, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# File scanning (Slice 4, Track B4)
# ---------------------------------------------------------------------------


@dramatiq.actor(broker=broker)
def scan_file(upload_id: str) -> dict[str, Any]:
    """Scan one upload's bytes with the DevScanner magic-byte check.

    Records ``scan_status`` ``clean`` or ``quarantined`` on the
    ``file_uploads`` row. Deployments trigger this per completed upload
    (or run ``worker.scanning.scan_pending_uploads`` periodically). The
    plain-function core :func:`worker.scanning.perform_file_scan` is
    directly testable.
    """
    settings = get_worker_settings()
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL is not set; scan_file has nothing to scan.")
        return {"scanned": 0, "status": "no_database"}
    with get_connection() as conn:
        with conn.transaction():
            return perform_file_scan(conn, UUID(upload_id))
