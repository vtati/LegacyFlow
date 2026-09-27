"""Persistence for escalation records (Postgres via psycopg, Slice 2).

Repository functions take an open connection; services own transactions.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


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
