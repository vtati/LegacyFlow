"""Attorney Reviewer endpoints (plan 2026-10-27).

Thin routers: resolve the caller, delegate to the domain service.
Mounted under ``/v1``. The reviewer is advisory — no endpoint here can
create, approve, or annotate an approval gate.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims

from . import schemas as sch
from . import service

router = APIRouter(tags=["attorney-review"])

User = Annotated[Claims, Depends(get_current_user)]


@router.post(
    "/matters/{matter_id}/attorney-review/run",
    response_model=sch.RunResponseOut,
    status_code=201,
)
def run_attorney_review(
    user: User, matter_id: UUID, payload: sch.RunIn | None = None
) -> sch.RunResponseOut:
    """Run the automated pre-review over the frozen draft package.

    No transaction is opened here: the service manages short read and
    write transactions around the model call, which must never hold a
    database transaction open (review M1).
    """
    return service.run_review(
        actor_sub=user.sub,
        matter_id=matter_id,
        force=bool(payload and payload.force),
    )


@router.get(
    "/matters/{matter_id}/attorney-review/findings",
    response_model=sch.FindingsResponseOut,
)
def get_attorney_review_findings(
    user: User, matter_id: UUID
) -> sch.FindingsResponseOut:
    """Current findings for the matter's draft package (family + firm)."""
    with db.transaction() as conn:
        return service.get_findings(conn, actor_sub=user.sub, matter_id=matter_id)


@router.post(
    "/matters/{matter_id}/attorney-review/findings/{finding_id}/acknowledge",
    response_model=sch.FindingOut,
)
def acknowledge_attorney_review_finding(
    user: User, matter_id: UUID, finding_id: UUID, payload: sch.AcknowledgeIn
) -> sch.FindingOut:
    """Acknowledge (override) a finding with a note; recorded, never a gate."""
    with db.transaction() as conn:
        return service.acknowledge_finding(
            conn,
            actor_sub=user.sub,
            matter_id=matter_id,
            finding_id=finding_id,
            note=payload.note,
        )
