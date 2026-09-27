"""Persistence access for the assets domain (psycopg, Slice 3).

Repository functions take an open connection; services own transactions.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

# Columns serialized as jsonb on write.
_JSONB_COLUMNS = frozenset({"beneficiary_designation"})


def _page_clause(page: int, page_size: int) -> tuple[int, int]:
    return page_size, (page - 1) * page_size


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


def list_persons_for_household(conn: Any, household_id: UUID) -> list[dict[str, Any]]:
    """Return ``id``/``full_name`` for every person in a household."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, full_name FROM persons WHERE household_id = %s ORDER BY full_name",
            (str(household_id),),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def insert_asset(
    conn: Any,
    *,
    firm_id: UUID,
    household_id: UUID,
    matter_id: UUID | None,
    asset_type: str,
    label: str,
    estimated_value_cents: int,
    ownership: str,
    beneficiary_designation: dict[str, Any],
    notes: str | None,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Insert an asset; return the row.

    ``beneficiary_designation`` is always written (the column is NOT NULL);
    callers pass ``{}`` for "no designation".
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assets
              (firm_id, household_id, matter_id, asset_type, label,
               estimated_value_cents, ownership, beneficiary_designation,
               notes, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING *
            """,
            (
                str(firm_id),
                str(household_id),
                str(matter_id) if matter_id else None,
                asset_type,
                label,
                estimated_value_cents,
                ownership,
                json.dumps(beneficiary_designation),
                notes,
                str(created_by_user_id),
            ),
        )
        return dict(cur.fetchone())


def get_asset(conn: Any, asset_id: UUID) -> dict[str, Any] | None:
    """Return an asset row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM assets WHERE id = %s", (str(asset_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_assets_for_household(
    conn: Any, household_id: UUID, *, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    """List a household's assets (newest first) with a total count."""
    limit, offset = _page_clause(page, page_size)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM assets WHERE household_id = %s"
            " ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (str(household_id), limit, offset),
        )
        items = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) AS total FROM assets WHERE household_id = %s",
            (str(household_id),),
        )
        total = int(cur.fetchone()["total"])
    return items, total


def update_asset(conn: Any, asset_id: UUID, fields: dict[str, Any]) -> dict[str, Any]:
    """Apply a field patch to an asset; return the updated row."""
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key in _JSONB_COLUMNS:
            assignments.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            assignments.append(f"{key} = %s")
            params.append(str(value) if isinstance(value, UUID) else value)
    assignments.append("updated_at = now()")
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE assets SET {', '.join(assignments)} WHERE id = %s RETURNING *",
            (*params, str(asset_id)),
        )
        return dict(cur.fetchone())


def delete_asset(conn: Any, asset_id: UUID) -> None:
    """Delete an asset's flags, then the asset."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM asset_flags WHERE asset_id = %s", (str(asset_id),))
        cur.execute("DELETE FROM assets WHERE id = %s", (str(asset_id),))


# ---------------------------------------------------------------------------
# Liabilities
# ---------------------------------------------------------------------------


def insert_liability(
    conn: Any,
    *,
    firm_id: UUID,
    household_id: UUID,
    matter_id: UUID | None,
    liability_type: str,
    label: str,
    balance_cents: int,
    monthly_payment_cents: int | None,
    notes: str | None,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Insert a liability; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO liabilities
              (firm_id, household_id, matter_id, liability_type, label,
               balance_cents, monthly_payment_cents, notes, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(firm_id),
                str(household_id),
                str(matter_id) if matter_id else None,
                liability_type,
                label,
                balance_cents,
                monthly_payment_cents,
                notes,
                str(created_by_user_id),
            ),
        )
        return dict(cur.fetchone())


def get_liability(conn: Any, liability_id: UUID) -> dict[str, Any] | None:
    """Return a liability row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM liabilities WHERE id = %s", (str(liability_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_liabilities_for_household(
    conn: Any, household_id: UUID, *, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    """List a household's liabilities (newest first) with a total count."""
    limit, offset = _page_clause(page, page_size)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM liabilities WHERE household_id = %s"
            " ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (str(household_id), limit, offset),
        )
        items = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) AS total FROM liabilities WHERE household_id = %s",
            (str(household_id),),
        )
        total = int(cur.fetchone()["total"])
    return items, total


def update_liability(conn: Any, liability_id: UUID, fields: dict[str, Any]) -> dict[str, Any]:
    """Apply a field patch to a liability; return the updated row."""
    assignments = [f"{key} = %s" for key in fields]
    params: list[Any] = [
        str(value) if isinstance(value, UUID) else value for value in fields.values()
    ]
    assignments.append("updated_at = now()")
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE liabilities SET {', '.join(assignments)} WHERE id = %s RETURNING *",
            (*params, str(liability_id)),
        )
        return dict(cur.fetchone())


def delete_liability(conn: Any, liability_id: UUID) -> None:
    """Delete a liability."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM liabilities WHERE id = %s", (str(liability_id),))


# ---------------------------------------------------------------------------
# Alignment flags
# ---------------------------------------------------------------------------


def delete_asset_flags(conn: Any, asset_id: UUID) -> None:
    """Delete every flag for an asset (regeneration starts from a clean slate)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM asset_flags WHERE asset_id = %s", (str(asset_id),))


def insert_asset_flag(
    conn: Any,
    *,
    firm_id: UUID,
    asset_id: UUID,
    flag_type: str,
    severity: str,
    message: str,
) -> dict[str, Any]:
    """Insert one alignment flag; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO asset_flags (firm_id, asset_id, flag_type, severity, message)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(firm_id), str(asset_id), flag_type, severity, message),
        )
        return dict(cur.fetchone())


def list_asset_flags(conn: Any, asset_id: UUID) -> list[dict[str, Any]]:
    """Return every flag for an asset, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM asset_flags WHERE asset_id = %s ORDER BY created_at",
            (str(asset_id),),
        )
        return [dict(row) for row in cur.fetchall()]
