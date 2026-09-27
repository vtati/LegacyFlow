"""Slice-1 stub actors. No real jobs exist yet; jobs arrive in Slice 4."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import dramatiq
import psycopg
from psycopg.rows import dict_row

from worker.broker import get_broker
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
    with psycopg.connect(settings.DATABASE_URL, row_factory=dict_row) as conn:
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
