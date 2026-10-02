"""Service for attorney applications (manual onboarding)."""

from typing import Any
from uuid import UUID

import psycopg.errors

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.domains.attorneys import repository as repo
from app.domains.attorneys.schemas import (
    AttorneyApplicationCreate,
    AttorneyApplicationOut,
)


def _out(row: dict[str, Any]) -> AttorneyApplicationOut:
    data = dict(row)
    # psycopg may return jurisdictions as list already; normalize.
    if data.get("jurisdictions") is None:
        data["jurisdictions"] = []
    return AttorneyApplicationOut.model_validate(data)


def _actor_id(actor_sub: str) -> UUID:
    try:
        return UUID(actor_sub)
    except ValueError as exc:
        raise AuthorizationError("Invalid caller identity.") from exc


def submit_application(
    conn: Any, *, actor_sub: str, payload: AttorneyApplicationCreate
) -> AttorneyApplicationOut:
    """Submit an attorney application (any authenticated user)."""
    actor_id = _actor_id(actor_sub)
    try:
        row = repo.insert_application(
            conn,
            firm_id=payload.firm_id,
            user_id=actor_id,
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            bar_number=payload.bar_number,
            jurisdictions=[j.upper() for j in payload.jurisdictions],
            years_experience=payload.years_experience,
            practice_areas=payload.practice_areas,
            document_urls=payload.document_urls,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ConflictError(
            "You already have a pending attorney application."
        ) from exc
    record_audit_event(
        conn,
        action="attorney_applications.submitted",
        resource_type="attorney_application",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=payload.firm_id,
        resource_id=UUID(str(row["id"])),
    )
    return _out(row)


def list_applications(
    conn: Any, *, actor_sub: str, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[AttorneyApplicationOut]:
    """List applications for the caller's firm (firm staff+ only)."""
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, authz.STAFF_ROLES)
    return [_out(r) for r in repo.list_applications(conn, firm_id=firm_id, status=status, limit=limit, offset=offset)]


def approve_application(
    conn: Any, *, actor_sub: str, application_id: UUID
) -> AttorneyApplicationOut:
    """Approve an application: mark approved + grant attorney membership."""
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin", "staff"})

    app_row = repo.get_application(conn, application_id=application_id)
    if app_row is None or UUID(str(app_row["firm_id"])) != firm_id:
        raise NotFoundError("Application not found.")
    if app_row["status"] != "pending":
        raise AuthorizationError("Only pending applications can be approved.")

    repo.upsert_attorney_membership(
        conn, firm_id=firm_id, user_id=UUID(str(app_row["user_id"]))
    )
    row = repo.set_application_status(
        conn,
        application_id=application_id,
        status="approved",
        reviewed_by_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="attorney_applications.approved",
        resource_type="attorney_application",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=application_id,
    )
    return _out(row)


def reject_application(
    conn: Any, *, actor_sub: str, application_id: UUID, reason: str
) -> AttorneyApplicationOut:
    """Reject an application with a reason (firm admin/staff only)."""
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin", "staff"})

    app_row = repo.get_application(conn, application_id=application_id)
    if app_row is None or UUID(str(app_row["firm_id"])) != firm_id:
        raise NotFoundError("Application not found.")
    if app_row["status"] != "pending":
        raise AuthorizationError("Only pending applications can be rejected.")

    row = repo.set_application_status(
        conn,
        application_id=application_id,
        status="rejected",
        reviewed_by_user_id=actor_id,
        rejection_reason=reason,
    )
    record_audit_event(
        conn,
        action="attorney_applications.rejected",
        resource_type="attorney_application",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=application_id,
    )
    return _out(row)
