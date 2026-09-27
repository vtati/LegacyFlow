"""Persistence for planning decisions (Postgres via psycopg, Slice 2).

Repository functions take an open connection; services own transactions.
``planning_decisions`` is keyed by ``(matter_id, decision_key)``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def upsert_decision(
    conn: Any, *, matter_id: UUID, key: str, value: Any, decided_by: UUID
) -> dict[str, Any]:
    """Insert or replace one planning decision; return the stored row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO planning_decisions (matter_id, decision_key, value, decided_by)
            VALUES (%s, %s, %s::jsonb, %s)
            ON CONFLICT (matter_id, decision_key)
            DO UPDATE SET value = EXCLUDED.value,
                          decided_by = EXCLUDED.decided_by,
                          updated_at = now()
            RETURNING decision_key, value, decided_by, updated_at
            """,
            (str(matter_id), key, json.dumps(value), str(decided_by)),
        )
        row = cur.fetchone()
        return dict(row)


def list_decisions(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """Return every stored planning decision for a matter."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT decision_key, value, decided_by, updated_at"
            " FROM planning_decisions WHERE matter_id = %s ORDER BY decision_key",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]
