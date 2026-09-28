"""Interview router: start/resume sessions, post turns, decide proposals, dictate."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.interview import service
from app.domains.interview.schemas import (
    DictationOut,
    ProposalCorrect,
    ProposalOut,
    SessionCreate,
    SessionOut,
    SessionStatusUpdate,
    TurnCreate,
    TurnDetailOut,
    TurnOut,
    TurnSummaryOut,
)

router = APIRouter()


@router.post("/matters/{matter_id}/interview/sessions", response_model=SessionOut)
def start_session(
    matter_id: UUID,
    payload: SessionCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> SessionOut:
    """Start (or resume the active) interview session. Idempotent per matter+set."""
    with db.transaction() as conn:
        return service.start_session(
            conn, actor_sub=claims.sub, matter_id=matter_id, payload=payload
        )


@router.get("/matters/{matter_id}/interview/sessions/{session_id}", response_model=SessionOut)
def get_session(
    matter_id: UUID,
    session_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> SessionOut:
    """Session state + coverage checklist + open proposals."""
    with db.transaction() as conn:
        return service.get_session(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id
        )


@router.patch("/matters/{matter_id}/interview/sessions/{session_id}", response_model=SessionOut)
def update_session_status(
    matter_id: UUID,
    session_id: UUID,
    payload: SessionStatusUpdate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> SessionOut:
    """Pause / resume / abandon / complete (transitions validated server-side)."""
    with db.transaction() as conn:
        return service.update_session_status(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id, payload=payload
        )


@router.post("/matters/{matter_id}/interview/sessions/{session_id}/turns", response_model=TurnOut)
def post_turn(
    matter_id: UUID,
    session_id: UUID,
    payload: TurnCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> TurnOut:
    """Send a message; get the assistant reply + proposals + citations."""
    with db.transaction() as conn:
        return service.post_turn(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id, payload=payload
        )


@router.get(
    "/matters/{matter_id}/interview/sessions/{session_id}/turns",
    response_model=list[TurnSummaryOut],
)
def list_turns(
    matter_id: UUID,
    session_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[TurnSummaryOut]:
    """Recent turn summaries (no raw bodies)."""
    with db.transaction() as conn:
        return service.list_turns(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id
        )


@router.get(
    "/matters/{matter_id}/interview/sessions/{session_id}/turns/{turn_id}",
    response_model=TurnDetailOut,
)
def get_turn(
    matter_id: UUID,
    session_id: UUID,
    turn_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> TurnDetailOut:
    """Full text of one turn (the user's own conversation)."""
    with db.transaction() as conn:
        return service.get_turn_detail(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id, turn_id=turn_id
        )


@router.get(
    "/matters/{matter_id}/interview/sessions/{session_id}/proposals",
    response_model=list[ProposalOut],
)
def list_proposals(
    matter_id: UUID,
    session_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[ProposalOut]:
    """Open proposals awaiting verification."""
    with db.transaction() as conn:
        return service.list_proposals(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id
        )


@router.post(
    "/matters/{matter_id}/interview/proposals/{proposal_id}/confirm",
    response_model=ProposalOut,
)
def confirm_proposal(
    matter_id: UUID,
    proposal_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ProposalOut:
    """Confirm a proposal as-is; the value is written to intake with provenance."""
    with db.transaction() as conn:
        return service.decide_proposal(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            proposal_id=proposal_id,
            decision="confirmed",
        )


@router.post(
    "/matters/{matter_id}/interview/proposals/{proposal_id}/correct",
    response_model=ProposalOut,
)
def correct_proposal(
    matter_id: UUID,
    proposal_id: UUID,
    payload: ProposalCorrect,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ProposalOut:
    """Confirm with an edited value (both proposed and corrected are stored)."""
    with db.transaction() as conn:
        return service.decide_proposal(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            proposal_id=proposal_id,
            decision="corrected",
            corrected_value=payload.value,
        )


@router.post(
    "/matters/{matter_id}/interview/proposals/{proposal_id}/reject",
    response_model=ProposalOut,
)
def reject_proposal(
    matter_id: UUID,
    proposal_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ProposalOut:
    """Reject a proposal; the key returns to the missing list."""
    with db.transaction() as conn:
        return service.decide_proposal(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            proposal_id=proposal_id,
            decision="rejected",
        )


@router.post("/matters/{matter_id}/interview/dictate", response_model=DictationOut)
async def dictate(
    matter_id: UUID,
    request: Request,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> DictationOut:
    """Transcribe audio to text. The transcript is returned, never auto-sent."""
    body = await request.body()
    with db.transaction() as conn:
        return service.dictate(conn, actor_sub=claims.sub, matter_id=matter_id, audio=body)
