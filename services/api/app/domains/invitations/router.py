"""Invitation endpoints (Slice 2).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.invitations.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.invitations import service
from app.domains.invitations.schemas import (
    InvitationAcceptRequest,
    InvitationAcceptResponse,
    InvitationCreate,
    InvitationCreateResponse,
    InvitationOut,
)

router = APIRouter()


@router.post("/firms/invitations", status_code=201, response_model=InvitationCreateResponse)
def create_invitation(
    payload: InvitationCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> InvitationCreateResponse:
    """Create an invitation for the caller's firm (firm_admin/attorney only)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_invitation(
            conn, actor_sub=claims.sub, payload=payload, correlation_id=correlation_id
        )


@router.get("/firms/invitations", response_model=list[InvitationOut])
def list_invitations(
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[InvitationOut]:
    """List the caller's firm invitations (firm_admin/attorney only)."""
    with db.transaction() as conn:
        return service.list_invitations(conn, actor_sub=claims.sub, status=status)


@router.post(
    "/firms/invitations/{invitation_id}/resend",
    response_model=InvitationCreateResponse,
)
def resend_invitation(
    invitation_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> InvitationCreateResponse:
    """Re-issue a token for a pending/expired invitation."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.resend_invitation(
            conn,
            actor_sub=claims.sub,
            invitation_id=invitation_id,
            correlation_id=correlation_id,
        )


@router.post("/firms/invitations/{invitation_id}/revoke", response_model=InvitationOut)
def revoke_invitation(
    invitation_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> InvitationOut:
    """Revoke an invitation."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.revoke_invitation(
            conn,
            actor_sub=claims.sub,
            invitation_id=invitation_id,
            correlation_id=correlation_id,
        )


@router.post("/invitations/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    payload: InvitationAcceptRequest,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> InvitationAcceptResponse:
    """Accept an invitation with its raw token (idempotent)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.accept_invitation(
            conn,
            actor_sub=claims.sub,
            raw_token=payload.token,
            correlation_id=correlation_id,
        )
