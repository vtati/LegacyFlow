"""Household / persons / relationships endpoints (Slice 2).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.household.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.household import service
from app.domains.household.schemas import (
    HouseholdCreate,
    HouseholdDetailResponse,
    HouseholdMemberCreate,
    HouseholdMemberOut,
    HouseholdOut,
    PersonCreate,
    PersonOut,
    PersonPatch,
    RelationshipCreate,
    RelationshipOut,
)

router = APIRouter()


@router.post("/households", status_code=201, response_model=HouseholdOut)
def create_household(
    payload: HouseholdCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> HouseholdOut:
    """Create a household for the caller's firm (firm staff+ only)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_household(
            conn, actor_sub=claims.sub, payload=payload, correlation_id=correlation_id
        )


@router.get("/households", response_model=list[HouseholdOut])
def list_households(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[HouseholdOut]:
    """List the caller's firm households (firm staff+ only)."""
    with db.transaction() as conn:
        return service.list_households(conn, actor_sub=claims.sub)


@router.get("/matters/{matter_id}/household", response_model=HouseholdDetailResponse)
def get_matter_household(
    matter_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> HouseholdDetailResponse:
    """Return a matter's household with members, persons, relationships."""
    with db.transaction() as conn:
        return service.get_household_detail(conn, actor_sub=claims.sub, matter_id=matter_id)


@router.post("/matters/{matter_id}/persons", status_code=201, response_model=PersonOut)
def create_person(
    matter_id: UUID,
    payload: PersonCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> PersonOut:
    """Add a person to a matter's household."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_person(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            payload=payload,
            correlation_id=correlation_id,
        )


@router.patch("/matters/{matter_id}/persons/{person_id}", response_model=PersonOut)
def patch_person(
    matter_id: UUID,
    person_id: UUID,
    payload: PersonPatch,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> PersonOut:
    """Patch a person in a matter's household."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.patch_person(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            person_id=person_id,
            payload=payload,
            correlation_id=correlation_id,
        )


@router.post("/matters/{matter_id}/relationships", status_code=201, response_model=RelationshipOut)
def create_relationship(
    matter_id: UUID,
    payload: RelationshipCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> RelationshipOut:
    """Relate two persons in a matter's household."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.create_relationship(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            payload=payload,
            correlation_id=correlation_id,
        )


@router.post(
    "/matters/{matter_id}/household/members",
    status_code=201,
    response_model=HouseholdMemberOut,
)
def add_household_member(
    matter_id: UUID,
    payload: HouseholdMemberCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> HouseholdMemberOut:
    """Add a member row to a matter's household (firm staff+ only)."""
    correlation_id = correlation_id_from_request_id(getattr(request.state, "request_id", None))
    with db.transaction() as conn:
        return service.add_household_member(
            conn,
            actor_sub=claims.sub,
            matter_id=matter_id,
            payload=payload,
            correlation_id=correlation_id,
        )
