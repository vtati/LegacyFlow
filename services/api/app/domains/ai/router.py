"""Grounded AI gateway endpoints (Slice 4).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.ai.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.ai import service
from app.domains.ai.schemas import AIAnswer, AIAskRequest

router = APIRouter()


@router.post("/matters/{matter_id}/ai/ask", response_model=AIAnswer)
def ask_ai(
    matter_id: UUID,
    payload: AIAskRequest,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> AIAnswer:
    """Ask a question grounded in the matter's jurisdiction pack.

    Read access required. Advice/strategy questions are refused with an
    escalation record; questions with no matching approved content are
    escalated to the attorney; everything else is answered from approved
    pack educational content with citations.
    """
    with db.transaction() as conn:
        return service.ask(conn, actor_sub=claims.sub, matter_id=matter_id, payload=payload)
