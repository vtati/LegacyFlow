"""Guided-intake endpoints (Slice 2, Track C).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.intake.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.intake import service
from app.domains.intake.schemas import (
    AnswersIn,
    AnswersOut,
    IntakeProgressOut,
    QuestionSetsOut,
)

router = APIRouter()


@router.get("/matters/{matter_id}/intake/question-sets", response_model=QuestionSetsOut)
def get_question_sets(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> QuestionSetsOut:
    """Serve the question sets from the matter's snapshotted pack version."""
    with db.transaction() as conn:
        return service.get_question_sets(conn, actor_sub=claims.sub, matter_id=matter_id)


@router.put("/matters/{matter_id}/intake/answers", response_model=AnswersOut)
def save_answers(
    matter_id: UUID,
    payload: AnswersIn,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> AnswersOut:
    """Autosave intake answers (firm staff+ or primary_client/collaborator)."""
    with db.transaction() as conn:
        return service.save_answers(
            conn, actor_sub=claims.sub, matter_id=matter_id, answers=payload.answers
        )


@router.get("/matters/{matter_id}/intake/answers", response_model=AnswersOut)
def get_answers(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> AnswersOut:
    """Return the matter's stored intake answers."""
    with db.transaction() as conn:
        return service.get_answers(conn, actor_sub=claims.sub, matter_id=matter_id)


@router.get("/matters/{matter_id}/intake/progress", response_model=IntakeProgressOut)
def get_progress(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> IntakeProgressOut:
    """Return per-set progress and the overall completeness percentage."""
    with db.transaction() as conn:
        return service.get_progress(conn, actor_sub=claims.sub, matter_id=matter_id)
