"""Persistence access for the privacy domain (Postgres via psycopg).

All functions take an open connection (``app.core.db.transaction``) and the
caller's ``user_id``; queries are scoped to the caller's own rows or to
households the caller actively belongs to. Nothing here ever reads another
user's data.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domains.assets import repository as assets_repo
from app.domains.household import repository as household_repo
from app.domains.matters import repository as matters_repo

# Per-collection cap for data exports (sensible upper bound; the response
# flags truncation so callers know the export is partial).
EXPORT_COLLECTION_LIMIT = 500


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


def firm_id_via_household(conn: Any, user_id: UUID) -> UUID | None:
    """Return the firm of one of the user's active households, if any.

    Deterministic fallback for household-only clients (no firm membership):
    the earliest-created active household membership wins.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h.firm_id AS firm_id"
            " FROM household_members hm"
            " JOIN households h ON h.id = hm.household_id"
            " WHERE hm.user_id = %s AND hm.access_status = 'active'"
            " ORDER BY hm.created_at, h.id"
            " LIMIT 1",
            (str(user_id),),
        )
        row = cur.fetchone()
    return UUID(str(row["firm_id"])) if row else None


def get_preferences(conn: Any, user_id: UUID) -> dict[str, Any] | None:
    """Return the caller's preference row, or ``None`` when unset."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM notification_preferences WHERE user_id = %s",
            (str(user_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_preferences(
    conn: Any,
    user_id: UUID,
    *,
    email_enabled: bool | None = None,
    reminder_enabled: bool | None = None,
) -> dict[str, Any]:
    """Create or patch the caller's preference row; return it."""
    existing = get_preferences(conn, user_id)
    with conn.cursor() as cur:
        if existing is None:
            cur.execute(
                """
                INSERT INTO notification_preferences
                  (user_id, email_enabled, reminder_enabled)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (
                    str(user_id),
                    True if email_enabled is None else email_enabled,
                    True if reminder_enabled is None else reminder_enabled,
                ),
            )
        else:
            cur.execute(
                """
                UPDATE notification_preferences
                SET email_enabled = COALESCE(%s, email_enabled),
                    reminder_enabled = COALESCE(%s, reminder_enabled)
                WHERE user_id = %s
                RETURNING *
                """,
                (email_enabled, reminder_enabled, str(user_id)),
            )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


def set_unsubscribed(conn: Any, user_id: UUID) -> dict[str, Any]:
    """Set ``unsubscribed_at``; idempotent (first timestamp is kept)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO notification_preferences (user_id, unsubscribed_at)
            VALUES (%s, now())
            ON CONFLICT (user_id) DO UPDATE
            SET unsubscribed_at = COALESCE(
                    notification_preferences.unsubscribed_at, now())
            RETURNING *
            """,
            (str(user_id),),
        )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


# ---------------------------------------------------------------------------
# Data deletion requests
# ---------------------------------------------------------------------------


def create_deletion_request(
    conn: Any,
    *,
    user_id: UUID,
    firm_id: UUID | None,
    notes: str | None,
) -> dict[str, Any]:
    """File an ``open`` deletion request for the caller; return it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_deletion_requests (user_id, firm_id, notes)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (str(user_id), str(firm_id) if firm_id else None, notes),
        )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


def list_deletion_requests(conn: Any, user_id: UUID) -> list[dict[str, Any]]:
    """List the caller's own deletion requests, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM data_deletion_requests WHERE user_id = %s ORDER BY requested_at DESC",
            (str(user_id),),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Data export (read-only assembly of the caller's own data)
# ---------------------------------------------------------------------------


def _get_profile(conn: Any, user_id: UUID) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, display_name, first_name, last_name, created_at"
            " FROM profiles WHERE id = %s",
            (str(user_id),),
        )
        row = cur.fetchone()
    assert row is not None, "profile must exist for an authenticated user"
    return {k: (str(v) if isinstance(v, UUID) else v) for k, v in dict(row).items()}


def _sanitize_member(row: dict[str, Any]) -> dict[str, Any]:
    """Membership structure only — never other users' contact details."""
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "relationship_role": row["relationship_role"],
        "access_status": row["access_status"],
        "created_at": row["created_at"],
    }


def _sanitize_matter(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "firm_id": row["firm_id"],
        "household_id": row["household_id"],
        "plan_type": row["plan_type"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _sanitize_person(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "household_id": row["household_id"],
        "full_name": row["full_name"],
        "date_of_birth": row["date_of_birth"],
        "email": row["email"],
        "kind": row["kind"],
        "created_at": row["created_at"],
    }


def _list_decisions_for_matters(
    conn: Any, matter_ids: list[UUID], *, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Read-only fetch of full decision rows for the given matters."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total FROM planning_decisions WHERE matter_id = ANY(%s)",
            ([str(m) for m in matter_ids],),
        )
        total = int(cur.fetchone()["total"])
        cur.execute(
            "SELECT * FROM planning_decisions WHERE matter_id = ANY(%s)"
            " ORDER BY created_at LIMIT %s",
            ([str(m) for m in matter_ids], limit),
        )
        return [dict(row) for row in cur.fetchall()], total > limit


def _sanitize_decision(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "matter_id": row["matter_id"],
        "decision_key": row["decision_key"],
        "value": row["value"],
        "decided_by": row["decided_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _sanitize_asset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "household_id": row["household_id"],
        "matter_id": row["matter_id"],
        "asset_type": row["asset_type"],
        "label": row["label"],
        "estimated_value_cents": row["estimated_value_cents"],
        "ownership": row["ownership"],
        "beneficiary_designation": row["beneficiary_designation"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def export_user_data(
    conn: Any, user_id: UUID, *, limit: int = EXPORT_COLLECTION_LIMIT
) -> dict[str, Any]:
    """Assemble the caller's own data from existing domain repositories.

    Scope: households where the caller is an active member, plus the
    matters/persons/decisions/assets hanging off those households. Other
    users' rows (profiles, other firms' data) are never included.
    """
    household_ids = matters_repo.active_household_ids_for_user(conn, user_id)

    households: list[dict[str, Any]] = []
    persons: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    truncated_assets = False
    for household_id in household_ids:
        household = household_repo.get_household(conn, household_id)
        if household is None:
            continue
        members = household_repo.list_members_for_household(conn, household_id)
        households.append(
            {
                "id": household["id"],
                "firm_id": household["firm_id"],
                "display_name": household["display_name"],
                "created_at": household["created_at"],
                "members": [_sanitize_member(dict(m)) for m in members[:limit]],
            }
        )
        for person in household_repo.list_persons_for_household(conn, household_id)[:limit]:
            persons.append(_sanitize_person(dict(person)))
        asset_items, asset_total = assets_repo.list_assets_for_household(
            conn, household_id, page=1, page_size=limit
        )
        truncated_assets = truncated_assets or asset_total > limit
        for asset in asset_items:
            assets.append(_sanitize_asset(dict(asset)))

    matters: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    truncated = truncated_assets
    if household_ids:
        matter_items, matter_total = matters_repo.list_matters_for_households(conn, household_ids)
        truncated = truncated or matter_total > limit
        matter_ids = [UUID(str(m["id"])) for m in matter_items[:limit]]
        for matter in matter_items[:limit]:
            matters.append(_sanitize_matter(dict(matter)))
        decision_rows, decisions_truncated = _list_decisions_for_matters(
            conn, matter_ids, limit=limit
        )
        truncated = truncated or decisions_truncated
        for decision in decision_rows:
            decisions.append(_sanitize_decision(decision))

    truncated = (
        truncated
        or len(persons) >= limit
        or len(assets) >= limit
        or len(decisions) >= limit
        or any(len(h["members"]) >= limit for h in households)
    )

    return {
        "profile": _get_profile(conn, user_id),
        "households": households,
        "matters": matters,
        "persons": persons,
        "decisions": decisions,
        "assets": assets,
        "truncated": truncated,
    }
