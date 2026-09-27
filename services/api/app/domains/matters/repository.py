"""Persistence access for the matters domain (psycopg, Slice 2).

Repository functions take an open connection; services own transactions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID


def get_household_in_firm(conn: Any, household_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
    """Return a household row when it belongs to ``firm_id``, else ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM households WHERE id = %s AND firm_id = %s",
            (str(household_id), str(firm_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def resolve_jurisdiction_pack(
    conn: Any, country_code: str, subdivision_code: str
) -> dict[str, Any] | None:
    """Resolve a jurisdiction code to its jurisdiction + pack ids.

    Picks the most recent pack in an intake-usable status
    ('internal_testing', 'approved').
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT j.id AS jurisdiction_id, jp.id AS jurisdiction_pack_id
            FROM jurisdictions j
            JOIN jurisdiction_packs jp ON jp.jurisdiction_id = j.id
            WHERE j.country_code = %s
              AND j.subdivision_code = %s
              AND jp.status IN ('internal_testing', 'approved')
            ORDER BY jp.effective_from DESC
            LIMIT 1
            """,
            (country_code, subdivision_code),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def insert_matter(
    conn: Any,
    *,
    firm_id: UUID,
    household_id: UUID,
    primary_jurisdiction_id: UUID,
    jurisdiction_pack_id: UUID,
    plan_type: str,
    title: str,
    jurisdiction_snapshot: dict[str, Any],
    completeness_pct: int,
) -> dict[str, Any]:
    """Insert a matter in INTAKE_IN_PROGRESS; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matters
              (firm_id, household_id, primary_jurisdiction_id,
               jurisdiction_pack_id, plan_type, status, title,
               jurisdiction_snapshot, completeness_pct, current_step)
            VALUES (%s, %s, %s, %s, %s, 'INTAKE_IN_PROGRESS', %s,
                    %s::jsonb, %s, 'INTAKE_IN_PROGRESS')
            RETURNING *
            """,
            (
                str(firm_id),
                str(household_id),
                str(primary_jurisdiction_id),
                str(jurisdiction_pack_id),
                plan_type,
                title,
                json.dumps(jurisdiction_snapshot),
                completeness_pct,
            ),
        )
        return dict(cur.fetchone())


def insert_status_history(
    conn: Any,
    *,
    matter_id: UUID,
    from_status: str | None,
    to_status: str,
    reason_code: str | None,
    actor_user_id: UUID | None,
) -> None:
    """Append a matter_status_history row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matter_status_history
              (matter_id, from_status, to_status, reason_code, actor_user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                str(matter_id),
                from_status,
                to_status,
                reason_code,
                str(actor_user_id) if actor_user_id else None,
            ),
        )


def _page_clause(page: int, page_size: int) -> tuple[int, int]:
    return page_size, (page - 1) * page_size


def list_matters_for_firm(
    conn: Any,
    firm_id: UUID,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """List a firm's matters (newest first) with a total count."""
    limit, offset = _page_clause(page, page_size)
    where = "firm_id = %s"
    params: list[Any] = [str(firm_id)]
    if status is not None:
        where += " AND status = %s"
        params.append(status)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM matters WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        items = [dict(row) for row in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) AS total FROM matters WHERE {where}", params)
        total = int(cur.fetchone()["total"])
    return items, total


def list_matters_for_households(
    conn: Any,
    household_ids: list[UUID],
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """List matters for the given households (newest first) with a total."""
    limit, offset = _page_clause(page, page_size)
    id_params = [str(h) for h in household_ids]
    where = "household_id = ANY(%s)"
    params: list[Any] = [id_params]
    if status is not None:
        where += " AND status = %s"
        params.append(status)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM matters WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        items = [dict(row) for row in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) AS total FROM matters WHERE {where}", params)
        total = int(cur.fetchone()["total"])
    return items, total


def active_household_ids_for_user(conn: Any, user_id: UUID) -> list[UUID]:
    """Return household ids where the user is an active member."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT household_id FROM household_members"
            " WHERE user_id = %s AND access_status = 'active'",
            (str(user_id),),
        )
        return [UUID(str(row["household_id"])) for row in cur.fetchall()]


def get_household(conn: Any, household_id: UUID) -> dict[str, Any] | None:
    """Return a household row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM households WHERE id = %s", (str(household_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_matter(conn: Any, matter_id: UUID) -> dict[str, Any] | None:
    """Return a matter row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM matters WHERE id = %s", (str(matter_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_matter_pack_fallback(conn: Any, matter_id: UUID) -> dict[str, Any] | None:
    """Legacy fallback: resolve a matter's pack version via the Slice-1 FKs.

    Used when ``matters.jurisdiction_snapshot`` is empty (rows created before
    snapshots existed). Returns ``country_code``/``subdivision_code``/
    ``version``, or ``None`` when the matter (or its pack FKs) is missing.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT j.country_code, j.subdivision_code, jp.version
            FROM matters m
            JOIN jurisdictions j ON j.id = m.primary_jurisdiction_id
            JOIN jurisdiction_packs jp ON jp.id = m.jurisdiction_pack_id
            WHERE m.id = %s
            """,
            (str(matter_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_completeness(conn: Any, matter_id: UUID, completeness_pct: int | float) -> None:
    """Persist the answer-driven completeness percentage for a matter."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE matters SET completeness_pct = %s WHERE id = %s",
            (int(completeness_pct), str(matter_id)),
        )


def update_matter_status(
    conn: Any,
    matter_id: UUID,
    *,
    to_status: str,
    completeness_pct: int | float,
    submitted_at: datetime | None,
) -> dict[str, Any]:
    """Apply a status transition; return the updated row."""
    pct = int(completeness_pct)  # column is integer; callers may compute floats
    with conn.cursor() as cur:
        if submitted_at is not None:
            cur.execute(
                """
                UPDATE matters
                SET status = %s,
                    status_version = status_version + 1,
                    current_step = %s,
                    completeness_pct = %s,
                    submitted_at = COALESCE(submitted_at, %s)
                WHERE id = %s
                RETURNING *
                """,
                (
                    to_status,
                    to_status,
                    pct,
                    submitted_at,
                    str(matter_id),
                ),
            )
        else:
            cur.execute(
                """
                UPDATE matters
                SET status = %s,
                    status_version = status_version + 1,
                    current_step = %s,
                    completeness_pct = %s
                WHERE id = %s
                RETURNING *
                """,
                (to_status, to_status, pct, str(matter_id)),
            )
        return dict(cur.fetchone())
