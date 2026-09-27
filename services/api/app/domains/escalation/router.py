"""Escalation endpoints (Slice 2, Track C).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.escalation.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.escalation import service
from app.domains.escalation.schemas import EscalationOut, EvaluateOut

router = APIRouter()


@router.post("/matters/{matter_id}/intake/evaluate", response_model=EvaluateOut)
def evaluate_intake(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> EvaluateOut:
    """Evaluate intake facts against the pack's escalation rules.

    Write access required. A triggered ``blocking`` rule transitions the
    matter to ESCALATED; ``review``/``info`` rules are recorded for
    attorney review without a transition.
    """
    with db.transaction() as conn:
        return service.evaluate_intake(conn, actor_sub=claims.sub, matter_id=matter_id)


@router.get("/matters/{matter_id}/escalations", response_model=list[EscalationOut])
def list_escalations(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[EscalationOut]:
    """List a matter's escalations, optionally filtered by status."""
    with db.transaction() as conn:
        return service.list_escalations(
            conn, actor_sub=claims.sub, matter_id=matter_id, status=status
        )
