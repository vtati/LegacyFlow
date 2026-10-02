"""Payout and case-request endpoints (Sprint 5)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.payouts import schemas, service

router = APIRouter(tags=["payouts"])


# ---------------------------------------------------------------------------
# Attorney-facing
# ---------------------------------------------------------------------------


@router.get("/attorney/payouts", response_model=list[schemas.PayoutOut])
def list_my_payouts(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> list[schemas.PayoutOut]:
    """List the caller's payouts (attorney role)."""
    with db.transaction() as conn:
        return service.list_my_payouts(conn, actor_sub=claims.sub)


@router.get("/attorney/payouts/summary", response_model=schemas.PayoutSummary)
def my_payout_summary(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.PayoutSummary:
    """Earnings summary for the caller (attorney role)."""
    with db.transaction() as conn:
        return service.my_payout_summary(conn, actor_sub=claims.sub)


@router.get("/attorney/availability", response_model=schemas.AttorneyAvailability)
def get_availability(
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.AttorneyAvailability:
    """Whether the caller is accepting new cases (attorney role)."""
    with db.transaction() as conn:
        return service.get_my_availability(conn, actor_sub=claims.sub)


@router.put("/attorney/availability", response_model=schemas.AttorneyAvailability)
def set_availability(
    payload: schemas.AttorneyAvailability,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.AttorneyAvailability:
    """Set whether the caller is accepting new cases (attorney role)."""
    with db.transaction() as conn:
        return service.set_my_availability(
            conn, actor_sub=claims.sub, accepting_cases=payload.accepting_cases
        )


@router.post(
    "/attorney/case-requests", status_code=201, response_model=schemas.CaseRequestOut
)
def request_case(
    matter_id: Annotated[UUID, Query()],
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.CaseRequestOut:
    """Request an unassigned matter (attorney role). Admin assigns manually."""
    with db.transaction() as conn:
        return service.request_case(conn, actor_sub=claims.sub, matter_id=matter_id)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@router.get("/admin/payouts", response_model=list[schemas.PayoutOut])
def list_payouts(
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[schemas.PayoutOut]:
    """List payouts for the caller's firm (firm admin/staff)."""
    with db.transaction() as conn:
        return service.list_payouts(conn, actor_sub=claims.sub, status=status)


@router.post("/admin/payouts/{payout_id}/approve", response_model=schemas.PayoutOut)
def approve_payout(
    payout_id: UUID,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.PayoutOut:
    """Approve a pending payout (firm admin/staff)."""
    with db.transaction() as conn:
        return service.approve_payout(conn, actor_sub=claims.sub, payout_id=payout_id)


@router.post("/admin/payouts/{payout_id}/pay", response_model=schemas.PayoutOut)
def pay_payout(
    payout_id: UUID,
    payload: schemas.PayoutPay,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.PayoutOut:
    """Mark an approved payout paid via manual transfer (firm admin/staff)."""
    with db.transaction() as conn:
        return service.pay_payout(
            conn, actor_sub=claims.sub, payout_id=payout_id, reference=payload.reference
        )


@router.post("/admin/payouts/{payout_id}/dispute", response_model=schemas.PayoutOut)
def dispute_payout(
    payout_id: UUID,
    payload: schemas.PayoutDispute,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.PayoutOut:
    """Flag a payout as disputed (firm admin/staff)."""
    with db.transaction() as conn:
        return service.dispute_payout(
            conn, actor_sub=claims.sub, payout_id=payout_id, reason=payload.reason
        )


@router.get("/admin/case-requests", response_model=list[schemas.CaseRequestOut])
def list_case_requests(
    claims: Annotated[Claims, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[schemas.CaseRequestOut]:
    """List attorney case requests (firm admin/staff)."""
    with db.transaction() as conn:
        return service.list_case_requests(conn, actor_sub=claims.sub, status=status)


@router.post(
    "/admin/case-requests/{request_id}/decide", response_model=schemas.CaseRequestOut
)
def decide_case_request(
    request_id: UUID,
    payload: schemas.CaseRequestDecide,
    claims: Annotated[Claims, Depends(get_current_user)],
) -> schemas.CaseRequestOut:
    """Accept or decline an attorney's case request (firm admin/staff)."""
    with db.transaction() as conn:
        return service.decide_case_request(
            conn, actor_sub=claims.sub, request_id=request_id, decision=payload.decision
        )
