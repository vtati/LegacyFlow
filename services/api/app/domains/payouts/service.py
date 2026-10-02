"""Service for attorney payouts and case requests (Sprint 5).

Payout tiers are configurable via environment — never hardcoded per matter:
- ``LF_PAYOUT_STANDARD_CENTS`` (default 15000 = $150)
- ``LF_PAYOUT_COMPLEX_CENTS`` (default 30000 = $300)

A matter is "complex" when its plan type indicates a trust or bundled plan.
"""

import os
from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import AuthorizationError, NotFoundError, ValidationAppError
from app.domains.payouts import repository as repo
from app.domains.payouts.schemas import (
    AttorneyAvailability,
    CaseRequestOut,
    PayoutOut,
    PayoutSummary,
)

# Plan types treated as complex for payout tiering.
COMPLEX_PLAN_TYPES = frozenset({"trust", "bundle", "trust_poa"})


def _actor_id(actor_sub: str) -> UUID:
    try:
        return UUID(actor_sub)
    except ValueError as exc:
        raise AuthorizationError("Invalid caller identity.") from exc


def payout_amount_for_tier(tier: str) -> int:
    """Return the configured payout amount in cents for a tier."""
    if tier == "complex":
        return int(os.environ.get("LF_PAYOUT_COMPLEX_CENTS", "30000"))
    return int(os.environ.get("LF_PAYOUT_STANDARD_CENTS", "15000"))


def tier_for_matter(plan_type: str | None) -> str:
    """Classify a matter's payout tier from its plan type."""
    if plan_type and plan_type.lower() in COMPLEX_PLAN_TYPES:
        return "complex"
    return "standard"


def _payout_out(row: dict[str, Any]) -> PayoutOut:
    return PayoutOut.model_validate(row)


def _request_out(row: dict[str, Any]) -> CaseRequestOut:
    return CaseRequestOut.model_validate(row)


def _require_attorney(conn: Any, actor_id: UUID, firm_id: UUID) -> None:
    """Caller must hold the attorney role (staff/admin may also act)."""
    role = authz.get_firm_role(conn, actor_id, firm_id)
    if role not in {"attorney", "firm_admin", "staff"}:
        raise AuthorizationError("This action requires the attorney firm role.")


def _require_admin(conn: Any, actor_id: UUID, firm_id: UUID) -> None:
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin", "staff"})


# ---------------------------------------------------------------------------
# Payout creation (called from the matters domain on PLAN_IMPLEMENTED)
# ---------------------------------------------------------------------------


def create_completion_payout(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    attorney_user_id: UUID,
    plan_type: str | None,
) -> PayoutOut | None:
    """Create the completion payout for a matter.

    Idempotent per matter (unique constraint). Returns ``None`` when there
    is no assigned attorney — nothing to pay.
    """
    tier = tier_for_matter(plan_type)
    amount_cents = payout_amount_for_tier(tier)
    row = repo.insert_payout(
        conn,
        firm_id=firm_id,
        attorney_user_id=attorney_user_id,
        matter_id=matter_id,
        amount_cents=amount_cents,
        tier=tier,
    )
    record_audit_event(
        conn,
        action="payouts.created",
        resource_type="payout",
        actor_user_id=None,
        actor_type="system",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        metadata={"tier": tier, "amount_cents": amount_cents},
    )
    return _payout_out(row)


# ---------------------------------------------------------------------------
# Attorney-facing reads
# ---------------------------------------------------------------------------


def list_my_payouts(conn: Any, *, actor_sub: str) -> list[PayoutOut]:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_attorney(conn, actor_id, firm_id)
    return [
        _payout_out(r)
        for r in repo.list_payouts_for_attorney(
            conn, firm_id=firm_id, attorney_user_id=actor_id
        )
    ]


def my_payout_summary(conn: Any, *, actor_sub: str) -> PayoutSummary:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_attorney(conn, actor_id, firm_id)
    data = repo.payout_summary_for_attorney(
        conn, firm_id=firm_id, attorney_user_id=actor_id
    )
    return PayoutSummary.model_validate(data)


def get_my_availability(conn: Any, *, actor_sub: str) -> AttorneyAvailability:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_attorney(conn, actor_id, firm_id)
    accepting = repo.get_attorney_availability(conn, firm_id=firm_id, user_id=actor_id)
    return AttorneyAvailability(accepting_cases=bool(accepting))


def set_my_availability(
    conn: Any, *, actor_sub: str, accepting_cases: bool
) -> AttorneyAvailability:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_attorney(conn, actor_id, firm_id)
    repo.set_attorney_availability(
        conn, firm_id=firm_id, user_id=actor_id, accepting_cases=accepting_cases
    )
    record_audit_event(
        conn,
        action="attorneys.availability_updated",
        resource_type="attorney",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=actor_id,
        metadata={"accepting_cases": accepting_cases},
    )
    return AttorneyAvailability(accepting_cases=accepting_cases)


# ---------------------------------------------------------------------------
# Admin payout management
# ---------------------------------------------------------------------------


def list_payouts(
    conn: Any, *, actor_sub: str, status: str | None = None
) -> list[PayoutOut]:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_admin(conn, actor_id, firm_id)
    if status is not None and status not in ("pending", "approved", "paid", "disputed"):
        raise ValidationAppError("Unknown payout status filter.")
    return [_payout_out(r) for r in repo.list_payouts_for_firm(conn, firm_id=firm_id, status=status)]


def approve_payout(conn: Any, *, actor_sub: str, payout_id: UUID) -> PayoutOut:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_admin(conn, actor_id, firm_id)
    payout = repo.get_payout(conn, payout_id=payout_id)
    if payout is None or UUID(str(payout["firm_id"])) != firm_id:
        raise NotFoundError("Payout not found.")
    if payout["status"] != "pending":
        raise ValidationAppError("Only pending payouts can be approved.")
    row = repo.set_payout_status(
        conn, payout_id=payout_id, status="approved", approved_by_user_id=actor_id
    )
    record_audit_event(
        conn,
        action="payouts.approved",
        resource_type="payout",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=UUID(str(row["matter_id"])),
        resource_id=payout_id,
        before_summary={"status": "pending"},
        after_summary={"status": "approved"},
    )
    return _payout_out(row)


def pay_payout(
    conn: Any, *, actor_sub: str, payout_id: UUID, reference: str | None
) -> PayoutOut:
    """Mark a payout paid (manual bank transfer in v1)."""
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_admin(conn, actor_id, firm_id)
    payout = repo.get_payout(conn, payout_id=payout_id)
    if payout is None or UUID(str(payout["firm_id"])) != firm_id:
        raise NotFoundError("Payout not found.")
    if payout["status"] != "approved":
        raise ValidationAppError("Only approved payouts can be marked paid.")
    row = repo.set_payout_status(conn, payout_id=payout_id, status="paid")
    record_audit_event(
        conn,
        action="payouts.paid",
        resource_type="payout",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=UUID(str(row["matter_id"])),
        resource_id=payout_id,
        before_summary={"status": "approved"},
        after_summary={"status": "paid"},
        metadata={"reference": reference} if reference else None,
    )
    return _payout_out(row)


def dispute_payout(conn: Any, *, actor_sub: str, payout_id: UUID, reason: str) -> PayoutOut:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_admin(conn, actor_id, firm_id)
    payout = repo.get_payout(conn, payout_id=payout_id)
    if payout is None or UUID(str(payout["firm_id"])) != firm_id:
        raise NotFoundError("Payout not found.")
    if payout["status"] == "paid":
        raise ValidationAppError("Paid payouts cannot be disputed; issue a correction instead.")
    row = repo.set_payout_status(
        conn, payout_id=payout_id, status="disputed", dispute_reason=reason
    )
    record_audit_event(
        conn,
        action="payouts.disputed",
        resource_type="payout",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=UUID(str(row["matter_id"])),
        resource_id=payout_id,
        after_summary={"status": "disputed"},
        metadata={"reason": reason},
    )
    return _payout_out(row)


# ---------------------------------------------------------------------------
# Case requests (attorney asks for an unassigned matter; admin decides)
# ---------------------------------------------------------------------------


def request_case(conn: Any, *, actor_sub: str, matter_id: UUID) -> CaseRequestOut:
    """Attorney requests an unassigned matter. Admin assigns manually."""
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_attorney(conn, actor_id, firm_id)
    # Verify the matter exists in this firm and is unassigned.
    matter = _get_firm_matter(conn, firm_id, matter_id)
    if matter.get("assigned_attorney_id"):
        raise ValidationAppError("This matter is already assigned to an attorney.")
    row = repo.insert_case_request(
        conn, firm_id=firm_id, attorney_user_id=actor_id, matter_id=matter_id
    )
    record_audit_event(
        conn,
        action="case_requests.created",
        resource_type="case_request",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
    )
    return _request_out(row)


def list_case_requests(
    conn: Any, *, actor_sub: str, status: str | None = None
) -> list[CaseRequestOut]:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_admin(conn, actor_id, firm_id)
    return [
        _request_out(r)
        for r in repo.list_case_requests_for_firm(conn, firm_id=firm_id, status=status)
    ]


def decide_case_request(
    conn: Any, *, actor_sub: str, request_id: UUID, decision: str
) -> CaseRequestOut:
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    _require_admin(conn, actor_id, firm_id)
    req = repo.get_case_request(conn, request_id=request_id)
    if req is None or UUID(str(req["firm_id"])) != firm_id:
        raise NotFoundError("Case request not found.")
    if req["status"] != "pending":
        raise ValidationAppError("Only pending requests can be decided.")
    row = repo.set_case_request_status(
        conn, request_id=request_id, status=decision, decided_by_user_id=actor_id
    )
    record_audit_event(
        conn,
        action="case_requests.decided",
        resource_type="case_request",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=UUID(str(row["matter_id"])),
        resource_id=request_id,
        after_summary={"status": decision},
    )
    return _request_out(row)


def _get_firm_matter(conn: Any, firm_id: UUID, matter_id: UUID) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, firm_id, assigned_attorney_id, plan_type FROM matters WHERE id = %s",
            (str(matter_id),),
        )
        row = cur.fetchone()
    if row is None or UUID(str(row["firm_id"])) != firm_id:
        raise NotFoundError("Matter not found.")
    return dict(row)
