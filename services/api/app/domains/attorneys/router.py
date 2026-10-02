"""Attorney application endpoints — manual onboarding (Sprint 3)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.attorneys import schemas, service

router = APIRouter(tags=["attorneys"])


@router.post("/attorney-applications", status_code=201, response_model=schemas.AttorneyApplicationOut)
def submit_application(
    payload: schemas.AttorneyApplicationCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.AttorneyApplicationOut:
    """Submit an attorney application (any authenticated user)."""
    with db.transaction() as conn:
        return service.submit_application(conn, actor_sub=claims.sub, payload=payload)


@router.get("/attorney-applications", response_model=list[schemas.AttorneyApplicationOut])
def list_applications(
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[schemas.AttorneyApplicationOut]:
    """List attorney applications for the caller's firm (firm staff+ only)."""
    with db.transaction() as conn:
        return service.list_applications(conn, actor_sub=claims.sub, status=status)


@router.post(
    "/attorney-applications/{application_id}/approve",
    response_model=schemas.AttorneyApplicationOut,
)
def approve_application(
    application_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.AttorneyApplicationOut:
    """Approve an application and grant the attorney firm role (admin/staff only)."""
    with db.transaction() as conn:
        return service.approve_application(
            conn, actor_sub=claims.sub, application_id=application_id
        )


@router.post(
    "/attorney-applications/{application_id}/reject",
    response_model=schemas.AttorneyApplicationOut,
)
def reject_application(
    application_id: UUID,
    payload: schemas.AttorneyApplicationReject,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.AttorneyApplicationOut:
    """Reject an application with a reason (admin/staff only)."""
    with db.transaction() as conn:
        return service.reject_application(
            conn, actor_sub=claims.sub, application_id=application_id, reason=payload.reason
        )
