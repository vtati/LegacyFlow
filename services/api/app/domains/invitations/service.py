"""Application services for the invitations domain (Slice 2).

Invitation flow: firm_admin/attorney creates an invitation carrying a random
token. Only the SHA-256 hex digest is stored (``token_hash``); the raw token
is returned to the creator once, and only outside production. Accepting an
invitation is idempotent and links the caller's user id to the invited
household or firm. No PII beyond the invitee email is logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core import authz, db
from app.core.audit import record_audit_event
from app.core.config import get_settings
from app.core.errors import (
    AuthError,
    GoneError,
    NotFoundError,
    ValidationAppError,
)
from app.domains.invitations import repository as repo
from app.domains.invitations.schemas import (
    InvitationAcceptResponse,
    InvitationCreate,
    InvitationCreateResponse,
    InvitationOut,
)

# Roles that may manage invitations (create/resend/revoke/list).
INVITE_MANAGER_ROLES = {"firm_admin", "attorney"}

# Invitation role -> household_members.relationship_role for client invites.
CLIENT_ROLE_TO_HOUSEHOLD_ROLE = {
    "client_primary": "primary_client",
    "client_collaborator": "collaborator",
}

VALID_ROLES = frozenset(
    {
        "client_primary",
        "client_collaborator",
        "firm_admin",
        "firm_attorney",
        "firm_staff",
        "firm_auditor",
    }
)

# Invitation firm role -> firm_memberships.role (different check values).
FIRM_ROLE_TO_MEMBERSHIP_ROLE = {
    "firm_admin": "firm_admin",
    "firm_attorney": "attorney",
    "firm_staff": "staff",
    "firm_auditor": "auditor",
}

VALID_STATUSES = frozenset({"pending", "accepted", "expired", "revoked"})


def _user_id_or_raise(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def _mint_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hex) for a fresh invitation token."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _is_expired(row: dict[str, Any]) -> bool:
    return bool(row["expires_at"] < datetime.now(UTC))


def create_invitation(
    conn: Any,
    *,
    actor_sub: str,
    payload: InvitationCreate,
    correlation_id: UUID | None = None,
) -> InvitationCreateResponse:
    """Create an invitation for the caller's firm."""
    actor_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, INVITE_MANAGER_ROLES)

    if payload.role not in VALID_ROLES:
        raise ValidationAppError("The invitation role is not recognized.")

    household_id = payload.household_id
    if payload.role in CLIENT_ROLE_TO_HOUSEHOLD_ROLE:
        if household_id is not None:
            household = repo.get_household(conn, household_id)
            if household is None or UUID(str(household["firm_id"])) != firm_id:
                raise NotFoundError("Household not found.")
        # Pre-create the invited household member row so accept() can claim
        # it (user_id stays NULL until the invitee accepts).
        if household_id is not None:
            repo.insert_household_member(
                conn,
                household_id=household_id,
                relationship_role=CLIENT_ROLE_TO_HOUSEHOLD_ROLE[payload.role],
                access_status="invited",
                invited_by=actor_id,
            )

    raw_token, token_hash = _mint_token()
    row = repo.insert_invitation(
        conn,
        firm_id=firm_id,
        email=payload.email,
        role=payload.role,
        household_id=household_id,
        token_hash=token_hash,
        invited_by=actor_id,
    )
    record_audit_event(
        conn,
        action="invitations.created",
        resource_type="invitation",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
        metadata={"role": payload.role},
    )

    accept_token = None
    if get_settings().ENVIRONMENT != "production":
        accept_token = raw_token
    return InvitationCreateResponse(
        invitation=InvitationOut.model_validate(row), accept_token=accept_token
    )


def list_invitations(
    conn: Any, *, actor_sub: str, status: str | None = None
) -> list[InvitationOut]:
    """List invitations for the caller's firm."""
    actor_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, INVITE_MANAGER_ROLES)
    if status is not None and status not in VALID_STATUSES:
        raise ValidationAppError("The invitation status filter is not recognized.")
    return [
        InvitationOut.model_validate(row) for row in repo.list_invitations(conn, firm_id, status)
    ]


def _get_firm_invitation(conn: Any, invitation_id: UUID, firm_id: UUID) -> dict[str, Any]:
    """Load an invitation owned by ``firm_id`` (404 otherwise)."""
    row = repo.get_invitation(conn, invitation_id)
    if row is None or UUID(str(row["firm_id"])) != firm_id:
        raise NotFoundError("Invitation not found.")
    return row


def resend_invitation(
    conn: Any,
    *,
    actor_sub: str,
    invitation_id: UUID,
    correlation_id: UUID | None = None,
) -> InvitationCreateResponse:
    """Issue a fresh token + expiry for a pending/expired invitation."""
    actor_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, INVITE_MANAGER_ROLES)
    row = _get_firm_invitation(conn, invitation_id, firm_id)

    if row["status"] == "accepted":
        raise ValidationAppError("An accepted invitation cannot be resent.")
    if row["status"] == "revoked":
        raise ValidationAppError("A revoked invitation cannot be resent.")
    if row["status"] not in ("pending", "expired"):
        raise ValidationAppError("Only pending or expired invitations can be resent.")

    raw_token, token_hash = _mint_token()
    updated = repo.rotate_invitation_token(conn, invitation_id, token_hash)
    record_audit_event(
        conn,
        action="invitations.resent",
        resource_type="invitation",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=invitation_id,
        correlation_id=correlation_id,
    )
    accept_token = None
    if get_settings().ENVIRONMENT != "production":
        accept_token = raw_token
    return InvitationCreateResponse(
        invitation=InvitationOut.model_validate(updated), accept_token=accept_token
    )


def revoke_invitation(
    conn: Any,
    *,
    actor_sub: str,
    invitation_id: UUID,
    correlation_id: UUID | None = None,
) -> InvitationOut:
    """Revoke an invitation. Idempotent when already revoked."""
    actor_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, INVITE_MANAGER_ROLES)
    row = _get_firm_invitation(conn, invitation_id, firm_id)

    if row["status"] == "accepted":
        raise ValidationAppError("An accepted invitation cannot be revoked.")
    if row["status"] != "revoked":
        row = repo.set_invitation_status(conn, invitation_id, "revoked")
        record_audit_event(
            conn,
            action="invitations.revoked",
            resource_type="invitation",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=firm_id,
            resource_id=invitation_id,
            correlation_id=correlation_id,
        )
    return InvitationOut.model_validate(row)


def accept_invitation(
    conn: Any,
    *,
    actor_sub: str,
    raw_token: str,
    correlation_id: UUID | None = None,
) -> InvitationAcceptResponse:
    """Accept an invitation, linking the caller to the invited firm/household.

    Idempotent: accepting twice returns the existing membership (HTTP 200).
    """
    actor_id = _user_id_or_raise(actor_sub)
    presented_hash = _hash_token(raw_token)
    row = repo.get_invitation_by_token_hash(conn, presented_hash)
    if row is None or not hmac.compare_digest(presented_hash, str(row["token_hash"])):
        raise NotFoundError("Invitation not found.")

    if row["status"] == "revoked":
        raise GoneError("This invitation has been revoked.")
    if row["status"] == "expired" or _is_expired(row):
        if row["status"] != "expired":
            # Commit the expiry marking in its own transaction: the 410 raised
            # below rolls back the surrounding transaction.
            with db.transaction() as expiry_conn:
                row = repo.set_invitation_status(expiry_conn, UUID(str(row["id"])), "expired")
        raise GoneError("This invitation has expired.")
    if row["status"] == "accepted":
        # Idempotent re-accept by the same user; a different user must not
        # inherit the invitation (existence-oracle safe 404).
        accepted_user_id = row.get("accepted_user_id")
        if accepted_user_id is None or UUID(str(accepted_user_id)) != actor_id:
            raise NotFoundError("Invitation not found.")
        return _accepted_response(
            conn, row, actor_id, already_accepted=True, correlation_id=correlation_id
        )
    if row["status"] != "pending":
        raise NotFoundError("Invitation not found.")

    return _accepted_response(
        conn, row, actor_id, already_accepted=False, correlation_id=correlation_id
    )


def _accepted_response(
    conn: Any,
    row: dict[str, Any],
    actor_id: UUID,
    *,
    already_accepted: bool,
    correlation_id: UUID | None = None,
) -> InvitationAcceptResponse:
    invitation_id = UUID(str(row["id"]))
    firm_id = UUID(str(row["firm_id"]))
    role = str(row["role"])

    if role in CLIENT_ROLE_TO_HOUSEHOLD_ROLE:
        membership = _accept_client_invitation(conn, row, actor_id, firm_id, role)[0]
        membership_type = "household"
    else:
        membership = repo.upsert_firm_membership(
            conn, firm_id=firm_id, user_id=actor_id, role=FIRM_ROLE_TO_MEMBERSHIP_ROLE[role]
        )
        membership_type = "firm"

    if not already_accepted:
        row = repo.mark_invitation_accepted(conn, invitation_id, actor_id)
        record_audit_event(
            conn,
            action="invitations.accepted",
            resource_type="invitation",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=firm_id,
            matter_id=None,
            resource_id=invitation_id,
            correlation_id=correlation_id,
            metadata={"role": role, "membership_type": membership_type},
        )

    return InvitationAcceptResponse(
        invitation=InvitationOut.model_validate(row),
        membership_type=membership_type,  # type: ignore[arg-type]
        membership_id=UUID(str(membership["id"])),
    )


def _accept_client_invitation(
    conn: Any, row: dict[str, Any], actor_id: UUID, firm_id: UUID, role: str
) -> tuple[dict[str, Any], UUID]:
    """Resolve the household and activate/create the member row."""
    invitation_id = UUID(str(row["id"]))
    invited_by = UUID(str(row["invited_by"]))
    household_role = CLIENT_ROLE_TO_HOUSEHOLD_ROLE[role]
    household_id_raw = row.get("household_id")
    if household_id_raw is None:
        # Only a primary client may bootstrap a new household; collaborators
        # must be invited to an existing one.
        if role != "client_primary":
            raise ValidationAppError("A household invitation is required for this role.")
        household = repo.insert_household(
            conn,
            firm_id=firm_id,
            display_name="New household",
            created_by_user_id=actor_id,
        )
        household_id = UUID(str(household["id"]))
        # Persist the created household so a repeat accept reuses it.
        repo.set_invitation_household(conn, invitation_id, household_id)
    else:
        household_id = UUID(str(household_id_raw))

    # Idempotent: reuse the caller's existing row, claim the pending invited
    # row, or insert a fresh active row.
    existing = repo.get_household_member_for_user(conn, household_id, actor_id)
    if existing is not None:
        if existing["access_status"] != "active":
            repo.activate_household_member(conn, UUID(str(existing["id"])))
            existing = repo.get_household_member_for_user(conn, household_id, actor_id)
        assert existing is not None
        return existing, household_id

    pending = repo.find_pending_household_invite_row(conn, household_id, household_role, invited_by)
    if pending is not None:
        return repo.claim_household_member(conn, UUID(str(pending["id"])), actor_id), household_id

    return (
        repo.insert_household_member(
            conn,
            household_id=household_id,
            user_id=actor_id,
            relationship_role=household_role,
            access_status="active",
        ),
        household_id,
    )
