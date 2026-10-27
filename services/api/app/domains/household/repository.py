"""Persistence access for the household domain (psycopg, Slice 2)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID


def insert_household(
    conn: Any, *, firm_id: UUID, display_name: str, created_by_user_id: UUID
) -> dict[str, Any]:
    """Insert a household; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO households (firm_id, display_name, created_by_user_id)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (str(firm_id), display_name, str(created_by_user_id)),
        )
        return dict(cur.fetchone())


def list_households_for_firm(conn: Any, firm_id: UUID) -> list[dict[str, Any]]:
    """List a firm's households, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM households WHERE firm_id = %s ORDER BY created_at DESC",
            (str(firm_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_household(conn: Any, household_id: UUID) -> dict[str, Any] | None:
    """Return a household row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM households WHERE id = %s", (str(household_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_members_for_household(conn: Any, household_id: UUID) -> list[dict[str, Any]]:
    """List household members with their linked person rows (if any)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT hm.*,
                   p.id AS person_id,
                   p.household_id AS person_household_id,
                   p.full_name AS person_full_name,
                   p.date_of_birth AS person_date_of_birth,
                   p.email AS person_email,
                   p.kind AS person_kind,
                   p.notes AS person_notes,
                   p.created_at AS person_created_at
            FROM household_members hm
            LEFT JOIN persons p ON p.id = hm.person_id
            WHERE hm.household_id = %s
            ORDER BY hm.created_at
            """,
            (str(household_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def insert_household_member(
    conn: Any,
    *,
    household_id: UUID,
    relationship_role: str,
    access_status: str,
    user_id: UUID | None = None,
    person_id: UUID | None = None,
    invited_by: UUID | None = None,
) -> dict[str, Any]:
    """Insert a household_members row; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO household_members
              (household_id, user_id, person_id, relationship_role, access_status, invited_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(household_id),
                str(user_id) if user_id else None,
                str(person_id) if person_id else None,
                relationship_role,
                access_status,
                str(invited_by) if invited_by else None,
            ),
        )
        return dict(cur.fetchone())


def find_profile_by_email(conn: Any, email: str) -> dict[str, Any] | None:
    """Return a profiles row by email (case-insensitive), or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM profiles WHERE lower(email) = lower(%s) LIMIT 1", (email,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_person(conn: Any, person_id: UUID) -> dict[str, Any] | None:
    """Return a person row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM persons WHERE id = %s", (str(person_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_persons_for_household(conn: Any, household_id: UUID) -> list[dict[str, Any]]:
    """List persons in a household, ordered by creation."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM persons WHERE household_id = %s ORDER BY created_at",
            (str(household_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def insert_person(
    conn: Any,
    *,
    household_id: UUID,
    full_name: str,
    date_of_birth: date | None = None,
    email: str | None = None,
    kind: str = "adult",
    notes: str | None = None,
) -> dict[str, Any]:
    """Insert a person; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO persons
              (household_id, full_name, date_of_birth, email, kind, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(household_id),
                full_name,
                date_of_birth,
                email,
                kind,
                notes,
            ),
        )
        return dict(cur.fetchone())


def update_person(conn: Any, person_id: UUID, fields: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update to a person; return the updated row."""
    assignments = ", ".join(f"{column} = %s" for column in fields)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE persons SET {assignments} WHERE id = %s RETURNING *",
            (*fields.values(), str(person_id)),
        )
        return dict(cur.fetchone())


def list_relationships_for_household(conn: Any, household_id: UUID) -> list[dict[str, Any]]:
    """List relationships in a household, ordered by creation."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM relationships WHERE household_id = %s ORDER BY created_at",
            (str(household_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def insert_relationship(
    conn: Any,
    *,
    household_id: UUID,
    from_person_id: UUID,
    to_person_id: UUID,
    relationship_type: str,
) -> dict[str, Any]:
    """Insert a relationship; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO relationships
              (household_id, from_person_id, to_person_id, relationship_type)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(household_id),
                str(from_person_id),
                str(to_person_id),
                relationship_type,
            ),
        )
        return dict(cur.fetchone())


def update_relationship_type(
    conn: Any, relationship_id: UUID, relationship_type: str
) -> dict[str, Any] | None:
    """Update a relationship row's type in place; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE relationships SET relationship_type = %s
            WHERE id = %s
            RETURNING *
            """,
            (relationship_type, str(relationship_id)),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_relationship(conn: Any, relationship_id: UUID) -> None:
    """Delete a relationship row by id."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM relationships WHERE id = %s",
            (str(relationship_id),),
        )
