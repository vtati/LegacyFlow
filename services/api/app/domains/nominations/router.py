"""Nomination endpoints (Slice 2).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.nominations.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.nominations import service
from app.domains.nominations.schemas import NominationCreate, NominationOut

router = APIRouter()


@router.post("/matters/{matter_id}/nominations", status_code=201, response_model=NominationOut)
def create_nomination(
    matter_id: UUID,
    payload: NominationCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> NominationOut:
    """Create a nomination on a matter."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_nomination(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            payload=payload,
            correlation_id=correlation_id,
        )


@router.get("/matters/{matter_id}/nominations", response_model=list[NominationOut])
def list_nominations(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    type: Annotated[str | None, Query()] = None,
) -> list[NominationOut]:
    """List a matter's nominations ordered by type, then priority."""
    with db.transaction() as conn:
        return service.list_nominations(
            conn, actor_sub=claims.sub, matter_id=matter_id, nomination_type=type
        )


@router.delete(
    "/matters/{matter_id}/nominations/{nomination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_nomination(
    matter_id: UUID,
    nomination_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> Response:
    """Delete a nomination (firm staff+ or primary_client)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        service.delete_nomination(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            nomination_id=nomination_id,
            correlation_id=correlation_id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
