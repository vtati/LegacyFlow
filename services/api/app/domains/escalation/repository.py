"""Persistence for escalation records (Postgres via psycopg, Slice 2).

Repository functions take an open connection; services own transactions.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import psycopg.errors

logger = logging.getLogger(__name__)


def open_escalation_exists(conn: Any, matter_id: UUID, rule_id: str) -> bool:
    """Whether an ``open`` escalation already exists for (matter, rule)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM escalations"
            " WHERE matter_id = %s AND rule_id = %s AND status = 'open' LIMIT 1",
            (str(matter_id), rule_id),
        )
        return cur.fetchone() is not None


def insert_escalation(
    conn: Any,
    *,
    matter_id: UUID,
    rule_id: str,
    severity: str,
    outcome: str,
    facts_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Record an escalation as ``open``; return the inserted row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO escalations
              (matter_id, rule_id, severity, outcome, facts_snapshot, status)
            VALUES (%s, %s, %s, %s, %s::jsonb, 'open')
            RETURNING *
            """,
            (
                str(matter_id),
                rule_id,
                severity,
                outcome,
                json.dumps(facts_snapshot),
            ),
        )
        return dict(cur.fetchone())


def insert_escalation_isolated(
    conn: Any,
    *,
    matter_id: UUID,
    rule_id: str,
    severity: str,
    outcome: str,
    facts_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Savepoint-isolated insert for multi-row escalation writers (ERR-016).

    Extracted from the consistency reviewer's incident fix (2026-10-27):
    one bad row (a CHECK surprise, an FK race) must degrade to a skipped
    escalation + an exception log — never a 500 that rolls back the
    caller's whole transaction and its sibling rows. Returns the
    inserted row, or ``None`` when the insert failed and was skipped.
    The nested ``conn.transaction()`` is a SAVEPOINT inside the caller's
    transaction, so the failure rolls back alone. Only DB errors are
    swallowed (mirroring consistency.py): a programming error in the
    insert path must still fail loudly, not silently drop escalations.
    """
    try:
        with conn.transaction():
            return insert_escalation(
                conn,
                matter_id=matter_id,
                rule_id=rule_id,
                severity=severity,
                outcome=outcome,
                facts_snapshot=facts_snapshot,
            )
    except psycopg.errors.Error:
        logger.exception(
            "escalation insert failed matter_id=%s rule_id=%s severity=%s; skipping",
            matter_id,
            rule_id,
            severity,
        )
        return None


def list_escalations(conn: Any, matter_id: UUID, status: str | None = None) -> list[dict[str, Any]]:
    """List a matter's escalations, oldest first, optionally by status."""
    with conn.cursor() as cur:
        if status is None:
            cur.execute(
                "SELECT * FROM escalations WHERE matter_id = %s ORDER BY created_at",
                (str(matter_id),),
            )
        else:
            cur.execute(
                "SELECT * FROM escalations WHERE matter_id = %s AND status = %s"
                " ORDER BY created_at",
                (str(matter_id), status),
            )
        return [dict(row) for row in cur.fetchall()]


def list_open_blocking_rule_ids(conn: Any, matter_id: UUID) -> list[str]:
    """Return rule ids of ``open`` + ``blocking`` escalations for a matter."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rule_id FROM escalations"
            " WHERE matter_id = %s AND severity = 'blocking' AND status = 'open'"
            " ORDER BY created_at",
            (str(matter_id),),
        )
        return [str(row["rule_id"]) for row in cur.fetchall()]
