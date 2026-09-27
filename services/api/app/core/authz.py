"""Authorization helpers shared by domain services (§6.4, §11.1).

All helpers take an open connection and the caller's ``user_id`` (UUID from
the verified JWT ``sub`` claim). They raise :class:`AuthorizationError`
(HTTP 403) or :class:`NotFoundError` (HTTP 404) — never ``None``.

Existence-oracle rule: when the caller has no access to a matter at all,
helpers raise ``NotFoundError`` rather than ``AuthorizationError`` so a
cross-firm caller cannot distinguish "no such matter" from "not yours".

Role model (§3.2):
  * Firm staff roles live in ``firm_memberships``: firm_admin, attorney,
    staff, auditor.
  * Client access lives in ``household_members`` (primary_client,
    collaborator, dependent, trusted_contact) and grants access only to
    their household's matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.errors import AuthorizationError, NotFoundError

# Firm roles ordered by privilege (highest first).
FIRM_ROLE_RANK = {"firm_admin": 4, "attorney": 3, "staff": 2, "auditor": 1}

ATTORNEY_ROLES = {"firm_admin", "attorney"}
STAFF_ROLES = {"firm_admin", "attorney", "staff"}


@dataclass(frozen=True)
class MatterScope:
    """A matter the caller may access, plus how they may access it."""

    matter: dict[str, Any]
    via: str  # "firm" | "household"
    firm_role: str | None  # firm_memberships.role when via == "firm"
    household_role: str | None  # household_members.relationship_role when via == "household"


def get_user_firm_id(conn: Any, user_id: UUID) -> UUID:
    """Return the caller's single active firm (MVP: exactly one per user).

    Raises AuthorizationError when the caller has no active firm membership.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT firm_id FROM firm_memberships WHERE user_id = %s AND status = 'active'",
            (str(user_id),),
        )
        rows = cur.fetchall()
    if not rows:
        raise AuthorizationError("The caller has no active firm membership.")
    # MVP invariant: one active firm per user; take the first deterministically.
    return UUID(str(rows[0]["firm_id"]))


def get_firm_role(conn: Any, user_id: UUID, firm_id: UUID) -> str | None:
    """Return the caller's active role in ``firm_id``, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role FROM firm_memberships"
            " WHERE user_id = %s AND firm_id = %s AND status = 'active'",
            (str(user_id), str(firm_id)),
        )
        row = cur.fetchone()
    return str(row["role"]) if row else None


def require_firm_member(conn: Any, user_id: UUID, firm_id: UUID) -> str:
    """Require an active membership in ``firm_id``; return the role."""
    role = get_firm_role(conn, user_id, firm_id)
    if role is None:
        raise AuthorizationError("The caller is not a member of this firm.")
    return role


def require_firm_role(conn: Any, user_id: UUID, firm_id: UUID, allowed: set[str]) -> str:
    """Require the caller's firm role to be in ``allowed``; return the role."""
    role = require_firm_member(conn, user_id, firm_id)
    if role not in allowed:
        raise AuthorizationError(
            "This action requires one of the following firm roles: "
            + ", ".join(sorted(allowed))
            + "."
        )
    return role


def get_matter_scope(conn: Any, matter_id: UUID, user_id: UUID) -> MatterScope:
    """Load a matter the caller may access.

    Access is granted when the caller is an active member of the matter's
    firm, or an active member of the matter's household. Raises
    NotFoundError when the caller has no access (existence-oracle rule).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM matters WHERE id = %s", (str(matter_id),))
        matter = cur.fetchone()
        if matter is None:
            raise NotFoundError("Matter not found.")
        firm_id = UUID(str(matter["firm_id"]))
        household_id = UUID(str(matter["household_id"]))

        firm_role = get_firm_role(conn, user_id, firm_id)
        if firm_role is not None:
            return MatterScope(
                matter=dict(matter), via="firm", firm_role=firm_role, household_role=None
            )

        cur.execute(
            "SELECT relationship_role FROM household_members"
            " WHERE household_id = %s AND user_id = %s AND access_status = 'active'",
            (str(household_id), str(user_id)),
        )
        member = cur.fetchone()
        if member is None:
            # No firm path and no household path: hide existence.
            raise NotFoundError("Matter not found.")
        return MatterScope(
            matter=dict(matter),
            via="household",
            firm_role=None,
            household_role=str(member["relationship_role"]),
        )


def require_matter_firm_role(
    conn: Any, matter_id: UUID, user_id: UUID, allowed: set[str]
) -> MatterScope:
    """Require firm-side access to a matter with a role in ``allowed``."""
    scope = get_matter_scope(conn, matter_id, user_id)
    if scope.via != "firm" or scope.firm_role not in allowed:
        raise AuthorizationError("This action requires a firm staff role.")
    return scope
