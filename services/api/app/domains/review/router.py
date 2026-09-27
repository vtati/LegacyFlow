"""Attorney-review endpoints (Slice 3).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.review.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.review import service
from app.domains.review.schemas import (
    ClarificationCreate,
    ClarificationOut,
    ClarificationRespond,
    CommentCreate,
    CommentOut,
    GateCreate,
    GateDecide,
    GateOut,
    PackImportResponse,
    PackOut,
    PackTransitionIn,
    PackTransitionOut,
    ReviewPacketCreate,
    ReviewPacketDetailOut,
    ReviewPacketOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Review packets
# ---------------------------------------------------------------------------


@router.post("/matters/{matter_id}/review-packets", status_code=201)
def generate_review_packet(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    payload: ReviewPacketCreate | None = None,
) -> ReviewPacketDetailOut:
    """Generate a point-in-time review packet (firm staff only)."""
    with db.transaction() as conn:
        return service.generate_packet(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            notes=payload.notes if payload else None,
        )


@router.get("/matters/{matter_id}/review-packets")
def list_review_packets(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[ReviewPacketOut]:
    """List a matter's review packets, newest version first (firm staff only)."""
    with db.transaction() as conn:
        return service.list_packets(conn, actor_sub=claims.sub, matter_id=matter_id)


@router.get("/matters/{matter_id}/review-packets/{packet_id}")
def get_review_packet(
    matter_id: UUID,
    packet_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ReviewPacketDetailOut:
    """Return one review packet snapshot (firm staff only)."""
    with db.transaction() as conn:
        return service.get_packet(
            conn, actor_sub=claims.sub, matter_id=matter_id, packet_id=packet_id
        )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.post("/matters/{matter_id}/comments", status_code=201)
def create_comment(
    matter_id: UUID,
    payload: CommentCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> CommentOut:
    """Post a matter comment (firm staff or household member)."""
    with db.transaction() as conn:
        return service.create_comment(
            conn, actor_sub=claims.sub, matter_id=matter_id, body=payload.body
        )


@router.get("/matters/{matter_id}/comments")
def list_comments(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[CommentOut]:
    """List a matter's comments in chronological order."""
    with db.transaction() as conn:
        return service.list_comments(conn, actor_sub=claims.sub, matter_id=matter_id)


# ---------------------------------------------------------------------------
# Clarification requests
# ---------------------------------------------------------------------------


@router.post("/matters/{matter_id}/clarifications", status_code=201)
def create_clarification(
    matter_id: UUID,
    payload: ClarificationCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ClarificationOut:
    """Ask the household a clarification question (attorney/firm_admin).

    Moves the matter ATTORNEY_REVIEW -> CLARIFICATION_REQUESTED.
    """
    with db.transaction() as conn:
        return service.create_clarification(
            conn, actor_sub=claims.sub, matter_id=matter_id, question=payload.question
        )


@router.post("/clarifications/{clarification_id}/respond")
def respond_clarification(
    clarification_id: UUID,
    payload: ClarificationRespond,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> ClarificationOut:
    """Answer an open clarification (household side).

    Moves the matter CLARIFICATION_REQUESTED -> ATTORNEY_REVIEW.
    """
    with db.transaction() as conn:
        return service.respond_clarification(
            conn,
            actor_sub=claims.sub,
            clarification_id=clarification_id,
            response=payload.response,
        )


@router.get("/matters/{matter_id}/clarifications")
def list_clarifications(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[ClarificationOut]:
    """List a matter's clarification requests, oldest first."""
    with db.transaction() as conn:
        return service.list_clarifications(conn, actor_sub=claims.sub, matter_id=matter_id)


# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------


@router.post("/matters/{matter_id}/gates", status_code=201)
def create_gate(
    matter_id: UUID,
    payload: GateCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> GateOut:
    """Create a pending approval gate (firm staff only)."""
    with db.transaction() as conn:
        return service.create_gate(conn, actor_sub=claims.sub, matter_id=matter_id, payload=payload)


@router.post("/gates/{gate_id}/decide")
def decide_gate(
    gate_id: UUID,
    payload: GateDecide,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> GateOut:
    """Approve or reject an approval gate (attorney/firm_admin only).

    An approval transitions the matter through the gate's target state;
    a rejection leaves the matter where it is.
    """
    with db.transaction() as conn:
        return service.decide_gate(conn, actor_sub=claims.sub, gate_id=gate_id, payload=payload)


@router.get("/matters/{matter_id}/gates")
def list_gates(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[GateOut]:
    """List a matter's approval gates, newest first."""
    with db.transaction() as conn:
        return service.list_gates(conn, actor_sub=claims.sub, matter_id=matter_id)


# ---------------------------------------------------------------------------
# Content packs
# ---------------------------------------------------------------------------


@router.post("/admin/packs/import", status_code=201)
def import_pack(
    claims: Annotated[Claims, Depends(get_current_user)],
    pack_dir: Annotated[str | None, Form()] = None,
    expected_bundle_hash: Annotated[str | None, Form()] = None,
    bundle: Annotated[UploadFile | None, File()] = None,
) -> PackImportResponse:
    """Import a content pack from a directory or an uploaded zip bundle.

    Validates the manifest and the bundle hash, then registers the version
    in ``jurisdiction_packs``. Firm_admin only.
    """
    with db.transaction() as conn:
        return service.import_pack(
            conn,
            actor_sub=claims.sub,
            pack_dir=pack_dir,
            bundle=bundle,
            expected_bundle_hash=expected_bundle_hash,
        )


@router.get("/packs")
def list_packs(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[PackOut]:
    """List available content packs with versions."""
    with db.transaction() as conn:
        return service.list_packs(conn, actor_sub=claims.sub)


@router.post("/admin/packs/{pack_id}/transition", response_model=PackTransitionOut)
def transition_pack(
    pack_id: str,
    payload: PackTransitionIn,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> PackTransitionOut:
    """Transition a content pack through its lifecycle.

    Ordering: draft -> internal_testing -> attorney_review -> approved, and
    internal_testing / attorney_review / approved -> retired. The
    attorney_review -> approved step requires an attorney or firm_admin
    role; all other transitions require firm_admin. Emits the
    ``packs.status_changed`` audit event.
    """
    with db.transaction() as conn:
        return service.transition_pack(
            conn, actor_sub=claims.sub, pack_ref=pack_id, to_status=payload.to_status
        )
