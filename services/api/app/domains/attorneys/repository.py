"""Repository for attorney applications."""

import json
from typing import Any
from uuid import UUID


def insert_application(
    conn: Any,
    *,
    firm_id: UUID,
    user_id: UUID,
    full_name: str,
    email: str,
    phone: str | None,
    bar_number: str,
    jurisdictions: list[str],
    years_experience: int | None,
    practice_areas: str | None,
    document_urls: dict[str, Any],
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attorney_applications
              (firm_id, user_id, full_name, email, phone, bar_number,
               jurisdictions, years_experience, practice_areas, document_urls)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                str(firm_id),
                str(user_id),
                full_name,
                email,
                phone,
                bar_number,
                jurisdictions,
                years_experience,
                practice_areas,
                json.dumps(document_urls),
            ),
        )
        return dict(cur.fetchone())


def list_applications(
    conn: Any, *, firm_id: UUID, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """List applications for a firm, newest first, paginated."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    with conn.cursor() as cur:
        if status:
            cur.execute(
                """
                SELECT * FROM attorney_applications
                WHERE firm_id = %s AND status = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (str(firm_id), status, limit, offset),
            )
        else:
            cur.execute(
                """
                SELECT * FROM attorney_applications
                WHERE firm_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (str(firm_id), limit, offset),
            )
        return [dict(r) for r in cur.fetchall()]


def get_application(conn: Any, *, application_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM attorney_applications WHERE id = %s",
            (str(application_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def set_application_status(
    conn: Any,
    *,
    application_id: UUID,
    status: str,
    reviewed_by_user_id: UUID,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE attorney_applications
            SET status = %s,
                rejection_reason = %s,
                reviewed_by_user_id = %s,
                reviewed_at = now(),
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (status, rejection_reason, str(reviewed_by_user_id), str(application_id)),
        )
        return dict(cur.fetchone())


def find_routable_attorney(
    conn: Any, *, firm_id: UUID, jurisdiction_code: str
) -> dict[str, Any] | None:
    """Return the firm's routable attorney for a jurisdiction, or ``None``.

    Routable = an APPROVED application whose jurisdictions contain the
    code AND an active ``attorney`` firm membership (the same pair
    ``approve_application`` writes). Earliest approval wins, so routing
    is deterministic. ``full_name``/``email`` come from the application
    row — the enrolled identity.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.user_id, a.full_name, a.email
            FROM attorney_applications a
            JOIN firm_memberships m
              ON m.firm_id = a.firm_id AND m.user_id = a.user_id
             AND m.role = 'attorney' AND m.status = 'active'
            WHERE a.firm_id = %s AND a.status = 'approved'
              AND %s = ANY(a.jurisdictions)
            ORDER BY a.created_at ASC
            LIMIT 1
            """,
            (str(firm_id), jurisdiction_code),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def find_approved_application_for_user(
    conn: Any, *, firm_id: UUID, user_id: UUID
) -> dict[str, Any] | None:
    """Return the user's earliest approved application in a firm, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM attorney_applications
            WHERE firm_id = %s AND user_id = %s AND status = 'approved'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (str(firm_id), str(user_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def get_profile_identity(conn: Any, *, user_id: UUID) -> dict[str, Any] | None:
    """Return a user's profile identity (display name + email), or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, display_name, email FROM profiles WHERE id = %s",
            (str(user_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_attorney_membership(
    conn: Any, *, firm_id: UUID, user_id: UUID
) -> None:
    """Create or reactivate an attorney firm membership."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO firm_memberships (firm_id, user_id, role, status)
            VALUES (%s, %s, 'attorney', 'active')
            ON CONFLICT (firm_id, user_id)
            DO UPDATE SET role = 'attorney', status = 'active'
            """,
            (str(firm_id), str(user_id)),
        )
