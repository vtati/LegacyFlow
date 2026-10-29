"""Dead-letter sink for retry-exhausted worker messages (ERR-003).

When the ``Retries`` middleware gives up on a message, it forwards the
message to the actor named by the actor's ``on_retry_exhausted``
option (``record_dead_letter`` in ``worker/actors.py``), which calls
:func:`write_dead_letter` here. The row records only what an operator
needs to triage — actor name, message id, an args SUMMARY, the error
class, and the retry count. Payloads are never copied: argument values
are kept only when they are ids (UUID-shaped) or scalars; everything
else is summarized as a redacted placeholder (contract rule 9: no PII
in logs or ops tables beyond identifiers).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from worker.db import get_connection
from worker.settings import get_worker_settings

logger = logging.getLogger(__name__)

_EXCEPTION_NAME_RE = re.compile(r"^([A-Za-z_][\w.]*Error|[A-Za-z_][\w.]*Exception|[\w.]+)$")


def _summarize_value(value: Any) -> Any:
    """Keep ids and scalars; redact anything that could carry PII."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        try:
            UUID(value)
        except ValueError:
            return "<redacted:str>"
        return value
    if isinstance(value, (list, tuple)):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": sorted(str(k) for k in value.keys())}
    return f"<redacted:{type(value).__name__}>"


def args_summary(message: dict[str, Any]) -> dict[str, Any]:
    """Summarize a dramatiq message's args/kwargs without payloads."""
    args = message.get("args") or []
    kwargs = message.get("kwargs") or {}
    return {
        "args": [_summarize_value(a) for a in args],
        "kwargs": {str(k): _summarize_value(v) for k, v in kwargs.items()},
    }


def error_class(message: dict[str, Any]) -> str | None:
    """Best-effort exception class from the message's stored traceback."""
    traceback_text = str((message.get("options") or {}).get("traceback") or "")
    lines = [ln.strip() for ln in traceback_text.splitlines() if ln.strip()]
    if not lines:
        return None
    candidate = lines[-1].split(":", 1)[0].strip()
    if candidate and _EXCEPTION_NAME_RE.match(candidate) and " " not in candidate:
        return candidate
    return None


def write_dead_letter(message: dict[str, Any], retry_info: dict[str, Any]) -> bool:
    """Insert one ``worker_dead_letters`` row; return True iff persisted.

    Batch-D M-5: the log must let an alert distinguish PERSISTED from
    LOST, so nothing is logged before the insert attempt. On success an
    ERROR "worker dead letter persisted" fires (with actor/message id);
    on failure a distinct ERROR "failed to persist dead letter" is
    logged with the exception. When the database is not configured the
    failure log is the record (the runbook alerts on it).
    """
    actor_name = str(message.get("actor_name") or "unknown")
    message_id = str(message.get("message_id") or "") or None
    err = error_class(message)
    retries = retry_info.get("retries")
    settings = get_worker_settings()
    if not settings.DATABASE_URL:
        logger.error(
            "failed to persist dead letter: DATABASE_URL is not set"
            " actor=%s message_id=%s error_class=%s retries=%s",
            actor_name,
            message_id,
            err,
            retries,
        )
        return False
    try:
        with get_connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO worker_dead_letters
                          (actor_name, message_id, args_summary, error_class, retries)
                        VALUES (%s, %s, %s::jsonb, %s, %s)
                        """,
                        (
                            actor_name,
                            message_id,
                            json.dumps(args_summary(message)),
                            err,
                            int(retries) if retries is not None else None,
                        ),
                    )
    except Exception:
        logger.exception(
            "failed to persist dead letter:"
            " actor=%s message_id=%s error_class=%s retries=%s",
            actor_name,
            message_id,
            err,
            retries,
        )
        return False
    logger.error(
        "worker dead letter persisted: actor=%s message_id=%s error_class=%s retries=%s",
        actor_name,
        message_id,
        err,
        retries,
    )
    return True
