"""Persistence access for the invitations domain (psycopg, Slice 2).

Repository functions take an open connection; services own transactions
(``app.core.db.transaction()``). All timestamps are UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

INVITATION_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(UTC)


def insert_invitation(
    conn: Any,
    *,
    firm_id: UUID,
    email: str,
    role: str,
    household_id: UUID | None,
    token_hash: str,
    invited_by: UUID,
) -> dict[str, Any]:
    """Insert a pending invitation; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invitations
              (firm_id, email, role, household_id, token_hash, status, expires_at, invited_by)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
            RETURNING *
            """,
            (
                str(firm_id),
                email,
                role,
                str(household_id) if household_id else None,
                token_hash,
                _now() + INVITATION_TTL,
                str(invited_by),
            ),
        )
        row = cur.fetchone()
    return dict(row)


def get_invitation(conn: Any, invitation_id: UUID) -> dict[str, Any] | None:
    """Return an invitation row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM invitations WHERE id = %s", (str(invitation_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_invitation_by_token_hash(conn: Any, token_hash: str) -> dict[str, Any] | None:
    """Return an invitation row by token hash, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM invitations WHERE token_hash = %s", (token_hash,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_invitations(conn: Any, firm_id: UUID, status: str | None = None) -> list[dict[str, Any]]:
    """List a firm's invitations, newest first, optionally filtered by status."""
    with conn.cursor() as cur:
        if status is None:
            cur.execute(
                "SELECT * FROM invitations WHERE firm_id = %s ORDER BY created_at DESC",
                (str(firm_id),),
            )
        else:
            cur.execute(
                "SELECT * FROM invitations WHERE firm_id = %s AND status = %s"
                " ORDER BY created_at DESC",
                (str(firm_id), status),
            )
        return [dict(row) for row in cur.fetchall()]


def rotate_invitation_token(conn: Any, invitation_id: UUID, token_hash: str) -> dict[str, Any]:
    """Issue a fresh token + expiry and reset status to pending; return row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE invitations
            SET token_hash = %s, expires_at = %s, status = 'pending'
            WHERE id = %s
            RETURNING *
            """,
            (token_hash, _now() + INVITATION_TTL, str(invitation_id)),
        )
        row = cur.fetchone()
    return dict(row)


def set_invitation_status(conn: Any, invitation_id: UUID, status: str) -> dict[str, Any]:
    """Set an invitation's status; return the updated row."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invitations SET status = %s WHERE id = %s RETURNING *",
            (status, str(invitation_id)),
        )
        row = cur.fetchone()
    return dict(row)


def mark_invitation_accepted(conn: Any, invitation_id: UUID, user_id: UUID) -> dict[str, Any]:
    """Mark an invitation accepted by ``user_id``; return the updated row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE invitations
            SET status = 'accepted', accepted_user_id = %s, accepted_at = %s
            WHERE id = %s
            RETURNING *
            """,
            (str(user_id), _now(), str(invitation_id)),
        )
        row = cur.fetchone()
    return dict(row)


def set_invitation_household(conn: Any, invitation_id: UUID, household_id: UUID) -> dict[str, Any]:
    """Persist the household created during acceptance; return the updated row."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invitations SET household_id = %s WHERE id = %s RETURNING *",
            (str(household_id), str(invitation_id)),
        )
        row = cur.fetchone()
    return dict(row)


def find_pending_household_invite_row(
    conn: Any, household_id: UUID, relationship_role: str, invited_by: UUID
) -> dict[str, Any] | None:
    """Return the earliest unclaimed invited household_members row.

    Created when a client invitation is issued; claimed when the invitee
    accepts (user_id set, access_status -> 'active'). Matching on the inviter
    keeps claiming deterministic when several invitations share a role.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM household_members
            WHERE household_id = %s
              AND relationship_role = %s
              AND access_status = 'invited'
              AND user_id IS NULL
              AND invited_by = %s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (str(household_id), relationship_role, str(invited_by)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def claim_household_member(conn: Any, member_id: UUID, user_id: UUID) -> dict[str, Any]:
    """Activate an invited household_members row for ``user_id``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE household_members
            SET user_id = %s, access_status = 'active'
            WHERE id = %s
            RETURNING *
            """,
            (str(user_id), str(member_id)),
        )
        row = cur.fetchone()
    return dict(row)


def get_household_member_for_user(
    conn: Any, household_id: UUID, user_id: UUID
) -> dict[str, Any] | None:
    """Return the caller's membership row in a household, if any."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM household_members WHERE household_id = %s AND user_id = %s",
            (str(household_id), str(user_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def insert_household_member(
    conn: Any,
    *,
    household_id: UUID,
    relationship_role: str,
    access_status: str,
    user_id: UUID | None = None,
    invited_by: UUID | None = None,
) -> dict[str, Any]:
    """Insert a household_members row; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO household_members
              (household_id, user_id, relationship_role, access_status, invited_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(household_id),
                str(user_id) if user_id else None,
                relationship_role,
                access_status,
                str(invited_by) if invited_by else None,
            ),
        )
        row = cur.fetchone()
    return dict(row)


def activate_household_member(conn: Any, member_id: UUID) -> None:
    """Set a household_members row's access_status to 'active'."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE household_members SET access_status = 'active' WHERE id = %s",
            (str(member_id),),
        )


def get_firm_membership_for_user(conn: Any, firm_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    """Return the caller's firm_memberships row in a firm, if any."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM firm_memberships WHERE firm_id = %s AND user_id = %s",
            (str(firm_id), str(user_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_firm_membership(conn: Any, *, firm_id: UUID, user_id: UUID, role: str) -> dict[str, Any]:
    """Ensure an active firm_membership row for ``user_id``; return the row.

    Idempotent: an existing active row is returned unchanged; a non-active
    row is reactivated with the invited role.
    """
    existing = get_firm_membership_for_user(conn, firm_id, user_id)
    if existing is not None:
        if existing["status"] != "active":
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE firm_memberships
                    SET status = 'active', role = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (role, str(existing["id"])),
                )
                return dict(cur.fetchone())
        return existing
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO firm_memberships (firm_id, user_id, role, status)
            VALUES (%s, %s, %s, 'active')
            RETURNING *
            """,
            (str(firm_id), str(user_id), role),
        )
        return dict(cur.fetchone())


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


def get_household(conn: Any, household_id: UUID) -> dict[str, Any] | None:
    """Return a household row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM households WHERE id = %s", (str(household_id),))
        row = cur.fetchone()
    return dict(row) if row else None
