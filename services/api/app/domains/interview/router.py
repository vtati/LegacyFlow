"""Interview router: start/resume sessions, post turns, decide proposals, dictate."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile

import logging

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.intake import service as intake_service
from app.domains.interview import extraction as extraction_service
from app.domains.interview import repository as interview_repo
from app.domains.interview import service
from app.domains.interview.schemas import (
    ConsistencyReviewOut,
    DictationOut,
    ExtractionOut,
    ExtractionProposalOut,
    ExtractionSummaryOut,
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
        turn_out = service.post_turn(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id, payload=payload
        )
    # Auto-confirm high-confidence proposals AFTER the turn transaction
    # commits, in a separate transaction with a statement timeout.
    # (Doing save_answers inside the turn transaction hung indefinitely.)
    # Best-effort: never let auto-confirm break the turn response.
    try:
        confirmed = _auto_confirm_proposals(turn_out, claims.sub, matter_id)
        if confirmed:
            # The turn's completeness snapshot was computed BEFORE its
            # proposals existed; refresh it so the client shows the
            # post-auto-confirm truth instead of lagging a turn behind.
            _refresh_turn_progress(turn_out, matter_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "Auto-confirm failed; proposals left pending for manual review"
        )
    return turn_out


def _refresh_turn_progress(turn_out: TurnOut, matter_id: UUID) -> None:
    """Recompute completeness/missing after auto-confirm wrote answers."""
    try:
        with db.transaction() as conn:
            from app.domains.matters import service as matters_service

            pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
            answers = service._answers_map(conn, matter_id)
            completeness, missing, _total, _answered = service._progress(pack, answers)
            turn_out.completeness_pct = completeness
            turn_out.missing_required_keys = missing
    except Exception:
        logging.getLogger(__name__).warning(
            "Progress refresh after auto-confirm failed", exc_info=True
        )


def _auto_confirm_proposals(turn_out: TurnOut, actor_sub: str, matter_id: UUID) -> int:
    """Best-effort auto-confirm of high-confidence proposals.

    Runs in its own transaction with a 10s statement timeout so it can
    never hang the interview. Failures leave proposals pending for
    manual card confirmation. Returns the number confirmed.
    """
    logger = logging.getLogger(__name__)
    confirmed = 0
    try:
        candidates = [
            p for p in (turn_out.proposals or [])
            if p.confidence is not None and p.confidence >= 0.9 and p.status == "proposed"
        ]
    except Exception:
        logger.warning("Auto-confirm: failed to extract candidates, skipping")
        return 0
    if not candidates:
        return 0
    from app.domains.matters import service as matters_service

    try:
        with db.transaction() as conn:
            actor_id = matters_service.caller_id(actor_sub)
    except Exception:
        logger.warning("Auto-confirm: caller lookup failed; skipping")
        return 0
    # One transaction PER candidate: a failure (or a lost compare-and-set)
    # on one proposal can neither poison nor overstate the others, and
    # the response only claims confirmations that actually committed.
    for p in candidates:
        try:
            with db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '10s'")
                # Compare-and-set FIRST: if the user already decided this
                # proposal (e.g. rejected a fabrication while a slow turn
                # was retrying), the CAS matches nothing and the user's
                # decision wins — no answer is written.
                row = interview_repo.decide_proposal(
                    conn, p.id, status="confirmed", corrected_value=None,
                    decided_by_user_id=actor_id, scrub_snippet=False,
                )
                if not row:
                    continue
                intake_service.save_answers(
                    conn, actor_sub=actor_sub, matter_id=matter_id,
                    answers={p.question_key: p.proposed_value},
                )
            p.status = "confirmed"  # reflect in the response
            confirmed += 1
        except Exception:
            logger.warning(
                "Auto-confirm failed for %r, leaving as pending",
                p.question_key,
            )
    return confirmed


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


@router.post(
    "/matters/{matter_id}/interview/sessions/{session_id}/extractions",
    response_model=ExtractionOut,
)
async def extract_document(
    matter_id: UUID,
    session_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
) -> ExtractionOut:
    """Upload a source document; extract interview facts into proposals.

    Extracted values become ``document_extracted`` proposals awaiting user
    verification — never auto-accepted.
    """
    filename = file.filename or "upload"
    body = await file.read()
    with db.transaction() as conn:
        result = extraction_service.run_extraction(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            session_id=session_id,
            filename=filename,
            file_bytes=body,
        )
    return ExtractionOut(
        extraction_id=result.extraction_id,
        filename=result.filename,
        status=result.status,
        proposals=[
            ExtractionProposalOut(
                proposal_id=p.proposal_id,
                question_key=p.question_key,
                value=p.value,
                confidence=p.confidence,
                source_page=p.source_page,
                source_snippet=p.source_snippet,
            )
            for p in result.proposals
        ],
        skipped_existing=result.skipped_existing,
        discarded=result.discarded,
        truncated=result.truncated,
        ocr_used=result.ocr_used,
    )


@router.get(
    "/matters/{matter_id}/interview/sessions/{session_id}/extractions",
    response_model=list[ExtractionSummaryOut],
)
def list_extractions(
    matter_id: UUID,
    session_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[ExtractionSummaryOut]:
    """Past document extraction runs for the session."""
    with db.transaction() as conn:
        return service.list_extractions(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id
        )


@router.post(
    "/matters/{matter_id}/interview/sessions/{session_id}/consistency-review",
    response_model=ConsistencyReviewOut,
)
def consistency_review(
    matter_id: UUID,
    session_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ConsistencyReviewOut:
    """AI consistency check over verified facts; findings become attorney questions."""
    with db.transaction() as conn:
        return service.consistency_review(
            conn, actor_sub=claims.sub, matter_id=matter_id, session_id=session_id
        )
