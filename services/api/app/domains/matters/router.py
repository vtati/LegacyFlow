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
    MatterAssign,
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
    """Create a matter (firm staff, household writers, or self-serve)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_matter(
            conn, actor_sub=claims.sub, payload=payload, correlation_id=correlation_id
        )


@router.get("/matters", response_model=MatterListResponse)
def list_matters(
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    jurisdiction_code: Annotated[str | None, Query(max_length=16)] = None,
    assigned_to: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> MatterListResponse:
    """List matters visible to the caller (paginated).

    ``q`` searches matter title and client (household) name; ``limit``/``offset``
    (defaults 20/0) take precedence over the legacy ``page``/``page_size`` pair.
    """
    effective_limit = limit if limit is not None else page_size
    effective_offset = offset if offset is not None else (page - 1) * page_size
    with db.transaction() as conn:
        return service.list_matters(
            conn,
            actor_sub=claims.sub,
            status=status,
            q=q,
            jurisdiction_code=jurisdiction_code,
            assigned_to=assigned_to,
            limit=effective_limit,
            offset=effective_offset,
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


@router.patch("/matters/{matter_id}/assign", response_model=MatterOut)
def assign_matter_attorney(
    matter_id: UUID,
    payload: MatterAssign,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> MatterOut:
    """Assign (or unassign) the attorney for a matter (firm staff+ only)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.assign_matter_attorney(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            attorney_user_id=payload.attorney_user_id,
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
