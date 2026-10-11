"""Application services for the firms domain — current firm + member roster."""

from typing import Any
from uuid import UUID

from app.core import authz
from app.core.errors import AuthError, NotFoundError
from app.domains.firms.schemas import FirmMemberDetailOut, FirmOut


def _caller_id(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def get_current_firm(conn: Any, *, actor_sub: str) -> FirmOut:
    """Return the firm the caller belongs to (any active member)."""
    actor_id = _caller_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM firms WHERE id = %s", (str(firm_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError("Firm not found.")
    return FirmOut(
        id=UUID(str(row["id"])),
        legal_name=row["legal_name"],
        display_name=row["display_name"],
        status=row["status"],
        created_at=row["created_at"],
    )


def list_members(conn: Any, *, actor_sub: str) -> list[FirmMemberDetailOut]:
    """List the member roster of the caller's firm (firm staff+ only)."""
    actor_id = _caller_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, authz.STAFF_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fm.id, fm.firm_id, fm.user_id, fm.role, fm.status, fm.created_at,
                   p.display_name, p.email
              FROM firm_memberships fm
              LEFT JOIN profiles p ON p.id = fm.user_id
             WHERE fm.firm_id = %s
             ORDER BY fm.created_at ASC
            """,
            (str(firm_id),),
        )
        rows = cur.fetchall()
    return [
        FirmMemberDetailOut(
            id=UUID(str(r["id"])),
            firm_id=UUID(str(r["firm_id"])),
            user_id=UUID(str(r["user_id"])),
            role=r["role"],
            status=r["status"],
            created_at=r["created_at"],
            display_name=r["display_name"],
            email=r["email"],
        )
        for r in rows
    ]


def list_public_firms(conn: Any) -> list[FirmOut]:
    """List all active firms (public — for attorney applications)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM firms WHERE status = 'active' ORDER BY display_name ASC"
        )
        rows = cur.fetchall()
    return [
        FirmOut(
            id=UUID(str(r["id"])),
            legal_name=r["legal_name"],
            display_name=r["display_name"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def create_firm(conn: Any, *, actor_sub: str, legal_name: str, display_name: str) -> FirmOut:
    """Create a new firm. The creator becomes its firm_admin.

    Any authenticated user may create a firm (workspace-creation model).
    """
    import uuid

    actor_id = _caller_id(actor_sub)
    firm_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO firms (id, legal_name, display_name, status)
            VALUES (%s, %s, %s, 'active')
            RETURNING id, legal_name, display_name, status, created_at
            """,
            (str(firm_id), legal_name.strip(), display_name.strip()),
        )
        row = cur.fetchone()
        cur.execute(
            """
            INSERT INTO firm_memberships (firm_id, user_id, role, status)
            VALUES (%s, %s, 'firm_admin', 'active')
            """,
            (str(firm_id), str(actor_id)),
        )
    return FirmOut(
        id=UUID(str(row["id"])),
        legal_name=row["legal_name"],
        display_name=row["display_name"],
        status=row["status"],
        created_at=row["created_at"],
    )
