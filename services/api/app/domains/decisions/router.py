"""Planning-decision endpoints (Slice 2, Track C).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.decisions.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.decisions import service
from app.domains.decisions.schemas import DecisionIn, DecisionOut

router = APIRouter()


@router.put("/matters/{matter_id}/decisions", response_model=DecisionOut)
def save_decision(
    matter_id: UUID,
    payload: DecisionIn,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> DecisionOut:
    """Record or replace a planning decision (firm staff+ or primary_client/collaborator)."""
    with db.transaction() as conn:
        return service.save_decision(
            conn, actor_sub=claims.sub, matter_id=matter_id, key=payload.key, value=payload.value
        )


@router.get("/matters/{matter_id}/decisions", response_model=list[DecisionOut])
def list_decisions(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[DecisionOut]:
    """List a matter's planning decisions."""
    with db.transaction() as conn:
        return service.list_decisions(conn, actor_sub=claims.sub, matter_id=matter_id)
