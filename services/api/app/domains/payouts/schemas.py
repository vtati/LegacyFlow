"""Schemas for attorney payouts and case requests."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PayoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    attorney_user_id: UUID
    attorney_name: str | None = None
    matter_id: UUID
    matter_title: str | None = None
    amount_cents: int
    tier: str
    status: str
    stripe_payout_id: str | None = None
    dispute_reason: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    approved_by_user_id: UUID | None = None
    paid_at: datetime | None = None


class PayoutApprove(BaseModel):
    """Body for approving a payout (no fields; approval is the action)."""

    model_config = ConfigDict(extra="forbid")


class PayoutPay(BaseModel):
    """Body for marking a payout paid. ``reference`` is the bank-transfer memo."""

    model_config = ConfigDict(extra="forbid")

    reference: str | None = Field(default=None, max_length=200)


class PayoutDispute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class PayoutSummary(BaseModel):
    """Earnings summary for an attorney."""

    total_earned_cents: int
    pending_cents: int
    approved_cents: int
    paid_cents: int
    payout_count: int


class CaseRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    attorney_user_id: UUID
    attorney_name: str | None = None
    matter_id: UUID
    matter_title: str | None = None
    jurisdiction_code: str | None = None
    plan_type: str | None = None
    status: str
    created_at: datetime
    decided_at: datetime | None = None


class CaseRequestDecide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(accepted|declined)$")


class AttorneyAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepting_cases: bool
