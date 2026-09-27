"""Persistence access for the nominations domain (psycopg, Slice 2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID


def insert_nomination(
    conn: Any,
    *,
    matter_id: UUID,
    nomination_type: str,
    person_id: UUID,
    priority: int,
    notes: str | None,
) -> dict[str, Any]:
    """Insert a nomination; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO nominations
              (matter_id, nomination_type, person_id, priority, notes)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(matter_id),
                nomination_type,
                str(person_id),
                priority,
                notes,
            ),
        )
        return dict(cur.fetchone())


def list_nominations(
    conn: Any, matter_id: UUID, nomination_type: str | None = None
) -> list[dict[str, Any]]:
    """List a matter's nominations ordered by type, then priority."""
    with conn.cursor() as cur:
        if nomination_type is None:
            cur.execute(
                "SELECT * FROM nominations WHERE matter_id = %s ORDER BY nomination_type, priority",
                (str(matter_id),),
            )
        else:
            cur.execute(
                "SELECT * FROM nominations WHERE matter_id = %s AND nomination_type = %s"
                " ORDER BY nomination_type, priority",
                (str(matter_id), nomination_type),
            )
        return [dict(row) for row in cur.fetchall()]


def get_nomination(conn: Any, nomination_id: UUID) -> dict[str, Any] | None:
    """Return a nomination row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM nominations WHERE id = %s", (str(nomination_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def delete_nomination(conn: Any, nomination_id: UUID) -> None:
    """Delete a nomination row."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM nominations WHERE id = %s", (str(nomination_id),))


def get_person(conn: Any, person_id: UUID) -> dict[str, Any] | None:
    """Return a person row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM persons WHERE id = %s", (str(person_id),))
        row = cur.fetchone()
    return dict(row) if row else None
