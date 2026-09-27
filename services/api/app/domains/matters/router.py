"""Matter endpoints (Slice 2).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.matters.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.matters import service
from app.domains.matters import submit as submit_service
from app.domains.matters.schemas import (
    MatterCreate,
    MatterDetailResponse,
    MatterListResponse,
    MatterOut,
    MatterTransition,
)

router = APIRouter()


@router.post("/matters", status_code=201, response_model=MatterOut)
def create_matter(
    payload: MatterCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> MatterOut:
    """Create a matter for the caller's firm (firm staff+ only)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_matter(
            conn, actor_sub=claims.sub, payload=payload, correlation_id=correlation_id
        )


@router.get("/matters", response_model=MatterListResponse)
def list_matters(
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> MatterListResponse:
    """List matters visible to the caller (paginated)."""
    with db.transaction() as conn:
        return service.list_matters(
            conn, actor_sub=claims.sub, status=status, page=page, page_size=page_size
        )


@router.get("/matters/{matter_id}", response_model=MatterDetailResponse)
def get_matter(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> MatterDetailResponse:
    """Return a matter with household and progress summaries."""
    with db.transaction() as conn:
        return service.get_matter_detail(conn, actor_sub=claims.sub, matter_id=matter_id)


@router.post("/matters/{matter_id}/transition", response_model=MatterOut)
def transition_matter(
    matter_id: UUID,
    payload: MatterTransition,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> MatterOut:
    """Apply a validated matter status transition (firm staff+ only)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.transition_matter(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            payload=payload,
            correlation_id=correlation_id,
        )


@router.post("/matters/{matter_id}/submit", response_model=MatterOut)
def submit_intake(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> MatterOut:
    """Submit a completed intake for attorney review.

    Firm staff+ or household primary_client/collaborator. Requires status
    INTAKE_IN_PROGRESS, 100% required-question completeness, and no open
    blocking escalations (HTTP 409 otherwise).
    """
    with db.transaction() as conn:
        return submit_service.submit_matter(conn, actor_sub=claims.sub, matter_id=matter_id)
