"""Asset/liability endpoints (Slice 3).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.assets.router import router
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.assets import service
from app.domains.assets.schemas import (
    AlignmentEvaluationResponse,
    AssetCreate,
    AssetListResponse,
    AssetOut,
    AssetPatch,
    BeneficiaryDesignation,
    LiabilityCreate,
    LiabilityListResponse,
    LiabilityOut,
    LiabilityPatch,
)

router = APIRouter()


def _correlation_id(request: Request) -> UUID | None:
    return correlation_id_from_request_id(getattr(request.state, "request_id", None))


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


@router.post("/households/{household_id}/assets", status_code=201, response_model=AssetOut)
def create_asset(
    household_id: UUID,
    payload: AssetCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> AssetOut:
    """Create an asset for a household (firm staff+ or household writer)."""
    with db.transaction() as conn:
        return service.create_asset(
            conn,
            actor_sub=claims.sub,
            household_id=household_id,
            payload=payload,
            correlation_id=_correlation_id(request),
        )


@router.get("/households/{household_id}/assets", response_model=AssetListResponse)
def list_assets(
    household_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AssetListResponse:
    """List a household's assets (paginated)."""
    with db.transaction() as conn:
        return service.list_assets(
            conn, actor_sub=claims.sub, household_id=household_id, page=page, page_size=page_size
        )


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(
    asset_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> AssetOut:
    """Return one asset (firm staff+ or owning household)."""
    with db.transaction() as conn:
        return service.get_asset(conn, actor_sub=claims.sub, asset_id=asset_id)


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def update_asset(
    asset_id: UUID,
    payload: AssetPatch,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> AssetOut:
    """Patch an asset (firm staff+ or owning household)."""
    with db.transaction() as conn:
        return service.update_asset(
            conn,
            actor_sub=claims.sub,
            asset_id=asset_id,
            payload=payload,
            correlation_id=_correlation_id(request),
        )


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(
    asset_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> None:
    """Delete an asset and its flags (firm staff+ or owning household)."""
    with db.transaction() as conn:
        service.delete_asset(
            conn, actor_sub=claims.sub, asset_id=asset_id, correlation_id=_correlation_id(request)
        )


@router.patch("/assets/{asset_id}/beneficiary", response_model=AssetOut)
def update_beneficiary(
    asset_id: UUID,
    payload: BeneficiaryDesignation,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> AssetOut:
    """Replace an asset's beneficiary designation (firm staff+ or owning household)."""
    with db.transaction() as conn:
        return service.update_beneficiary(
            conn,
            actor_sub=claims.sub,
            asset_id=asset_id,
            payload=payload,
            correlation_id=_correlation_id(request),
        )


@router.post("/assets/{asset_id}/evaluate-alignment", response_model=AlignmentEvaluationResponse)
def evaluate_alignment(
    asset_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> AlignmentEvaluationResponse:
    """Regenerate an asset's alignment flags (firm staff+ or owning household)."""
    with db.transaction() as conn:
        return service.evaluate_alignment(
            conn, actor_sub=claims.sub, asset_id=asset_id, correlation_id=_correlation_id(request)
        )


# ---------------------------------------------------------------------------
# Liabilities
# ---------------------------------------------------------------------------


@router.post("/households/{household_id}/liabilities", status_code=201, response_model=LiabilityOut)
def create_liability(
    household_id: UUID,
    payload: LiabilityCreate,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> LiabilityOut:
    """Create a liability for a household (firm staff+ or household writer)."""
    with db.transaction() as conn:
        return service.create_liability(
            conn,
            actor_sub=claims.sub,
            household_id=household_id,
            payload=payload,
            correlation_id=_correlation_id(request),
        )


@router.get("/households/{household_id}/liabilities", response_model=LiabilityListResponse)
def list_liabilities(
    household_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> LiabilityListResponse:
    """List a household's liabilities (paginated)."""
    with db.transaction() as conn:
        return service.list_liabilities(
            conn, actor_sub=claims.sub, household_id=household_id, page=page, page_size=page_size
        )


@router.get("/liabilities/{liability_id}", response_model=LiabilityOut)
def get_liability(
    liability_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> LiabilityOut:
    """Return one liability (firm staff+ or owning household)."""
    with db.transaction() as conn:
        return service.get_liability(conn, actor_sub=claims.sub, liability_id=liability_id)


@router.patch("/liabilities/{liability_id}", response_model=LiabilityOut)
def update_liability(
    liability_id: UUID,
    payload: LiabilityPatch,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> LiabilityOut:
    """Patch a liability (firm staff+ or owning household)."""
    with db.transaction() as conn:
        return service.update_liability(
            conn,
            actor_sub=claims.sub,
            liability_id=liability_id,
            payload=payload,
            correlation_id=_correlation_id(request),
        )


@router.delete("/liabilities/{liability_id}", status_code=204)
def delete_liability(
    liability_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
    request: Request,
) -> None:
    """Delete a liability (firm staff+ or owning household)."""
    with db.transaction() as conn:
        service.delete_liability(
            conn,
            actor_sub=claims.sub,
            liability_id=liability_id,
            correlation_id=_correlation_id(request),
        )
