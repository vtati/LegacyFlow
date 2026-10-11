"""Firm roster endpoints — current firm and member list (firm staff+)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.firms import schemas, service

router = APIRouter(tags=["firms"])


@router.get("/firms/current", response_model=schemas.FirmOut)
def get_current_firm(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.FirmOut:
    """Return the firm the caller belongs to (any active member)."""
    with db.transaction() as conn:
        return service.get_current_firm(conn, actor_sub=claims.sub)


@router.get("/firms", response_model=list[schemas.FirmOut])
def list_firms(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[schemas.FirmOut]:
    """List active firms (any authenticated user — for attorney applications)."""
    with db.transaction() as conn:
        return service.list_public_firms(conn)


@router.post("/firms", status_code=201, response_model=schemas.FirmOut)
def create_firm(
    payload: schemas.FirmCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.FirmOut:
    """Create a new firm. The creator becomes its firm_admin."""
    with db.transaction() as conn:
        return service.create_firm(
            conn,
            actor_sub=claims.sub,
            legal_name=payload.legal_name,
            display_name=payload.display_name,
        )


@router.get("/firms/members", response_model=list[schemas.FirmMemberDetailOut])
def list_members(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[schemas.FirmMemberDetailOut]:
    """List the member roster of the caller's firm (firm staff+ only)."""
    with db.transaction() as conn:
        return service.list_members(conn, actor_sub=claims.sub)
