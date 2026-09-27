"""Pydantic schemas for the assets domain (Slice 3).

Assets and liabilities are household-scoped financial records. An asset
carries an optional beneficiary designation shaped as
``{"primary": [{"name", "relationship"}], "contingent": [...]}``; the
deterministic alignment engine (``service.evaluate_alignment_flags``)
derives review flags from that designation and the household's persons.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

AssetType = Literal[
    "bank_account",
    "investment",
    "retirement",
    "real_estate",
    "business",
    "insurance",
    "vehicle",
    "other",
]
"""Asset types — matches the DB check constraint in
``supabase/migrations/20260927020000_assets_review_slice3.sql`` (Track A3)."""

Ownership = Literal[
    "sole",
    "joint",
    "tenants_in_common",
    "community_property",
    "trust_owned",
    "other",
]

LiabilityType = Literal["mortgage", "loan", "credit_card", "other"]
"""Liability types — matches the DB check constraint (Track A3 migration)."""

FlagType = Literal[
    "unknown_beneficiary",
    "non_household_beneficiary",
    "missing_contingent",
]

FlagSeverity = Literal["review", "info"]


class BeneficiaryEntry(BaseModel):
    """One named beneficiary inside a designation."""

    name: str = Field(min_length=1, max_length=200)
    relationship: str | None = Field(default=None, max_length=100)


class BeneficiaryDesignation(BaseModel):
    """Beneficiary designation: primary and contingent beneficiary lists."""

    primary: list[BeneficiaryEntry] = Field(default_factory=list)
    contingent: list[BeneficiaryEntry] = Field(default_factory=list)


class AssetCreate(BaseModel):
    """Create an asset (``POST /v1/households/{household_id}/assets``)."""

    matter_id: UUID | None = None
    asset_type: AssetType
    label: str = Field(min_length=1, max_length=200)
    estimated_value_cents: int = Field(ge=0)
    ownership: Ownership = "other"
    beneficiary_designation: BeneficiaryDesignation | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AssetPatch(BaseModel):
    """Patch an asset (``PATCH /v1/assets/{asset_id}``).

    Beneficiary designation has its own endpoint
    (``PATCH /v1/assets/{asset_id}/beneficiary``).
    """

    matter_id: UUID | None = None
    asset_type: AssetType | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    estimated_value_cents: int | None = Field(default=None, ge=0)
    ownership: Ownership | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AssetOut(BaseModel):
    """Public asset shape."""

    id: UUID
    firm_id: UUID
    household_id: UUID
    matter_id: UUID | None = None
    asset_type: str
    label: str
    estimated_value_cents: int | None = None
    ownership: str | None = None
    beneficiary_designation: BeneficiaryDesignation = Field(default_factory=BeneficiaryDesignation)
    notes: str | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AssetListResponse(BaseModel):
    """Paginated asset list."""

    items: list[AssetOut]
    page: int
    page_size: int
    total: int


class LiabilityCreate(BaseModel):
    """Create a liability (``POST /v1/households/{household_id}/liabilities``)."""

    matter_id: UUID | None = None
    liability_type: LiabilityType
    label: str = Field(min_length=1, max_length=200)
    balance_cents: int = Field(ge=0)
    monthly_payment_cents: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class LiabilityPatch(BaseModel):
    """Patch a liability (``PATCH /v1/liabilities/{liability_id}``)."""

    matter_id: UUID | None = None
    liability_type: LiabilityType | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    balance_cents: int | None = Field(default=None, ge=0)
    monthly_payment_cents: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class LiabilityOut(BaseModel):
    """Public liability shape."""

    id: UUID
    firm_id: UUID
    household_id: UUID
    matter_id: UUID | None = None
    liability_type: str
    label: str
    balance_cents: int
    monthly_payment_cents: int | None = None
    notes: str | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LiabilityListResponse(BaseModel):
    """Paginated liability list."""

    items: list[LiabilityOut]
    page: int
    page_size: int
    total: int


class AssetFlagOut(BaseModel):
    """Public alignment-flag shape."""

    id: UUID
    firm_id: UUID
    asset_id: UUID
    flag_type: str
    severity: str
    message: str
    resolved_at: datetime | None = None
    created_at: datetime


class AlignmentEvaluationResponse(BaseModel):
    """Result of ``POST /v1/assets/{asset_id}/evaluate-alignment``."""

    asset_id: UUID
    flags: list[AssetFlagOut]
    flag_counts: dict[str, int]


def designation_to_json(designation: BeneficiaryDesignation | None) -> dict[str, Any]:
    """Serialize a designation for the ``beneficiary_designation`` jsonb column.

    The column is NOT NULL (default ``'{}'``): a missing designation is
    stored as an empty designation.
    """
    if designation is None:
        return {}
    return designation.model_dump()
