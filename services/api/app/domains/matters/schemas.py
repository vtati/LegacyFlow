"""Pydantic schemas for the matters domain (artifact §7.2, Slice 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MatterCreate(BaseModel):
    """Create a matter (``POST /v1/matters``)."""

    household_id: UUID
    jurisdiction_code: str = Field(min_length=2, max_length=16)
    title: str | None = Field(default=None, max_length=200)
    plan_type: str = Field(default="will", min_length=1, max_length=50)


class MatterHouseholdSummary(BaseModel):
    """Household summary embedded in matter detail."""

    id: UUID
    name: str
    display_name: str


class MatterOut(BaseModel):
    """Public matter shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    household_id: UUID
    primary_jurisdiction_id: UUID
    jurisdiction_pack_id: UUID
    plan_type: str
    status: str
    status_version: int
    jurisdiction_confirmation_status: str
    assigned_attorney_id: UUID | None = None
    title: str
    jurisdiction_snapshot: dict[str, Any] | None = None
    completeness_pct: int = 0
    submitted_at: datetime | None = None
    current_step: str | None = None
    created_at: datetime
    updated_at: datetime
    jurisdiction_code: str | None = None
    household_name: str | None = None
    household: MatterHouseholdSummary | None = None


class MatterTransition(BaseModel):
    """Request a matter status transition."""

    to_status: str = Field(min_length=1, max_length=50)
    reason: str | None = Field(default=None, max_length=500)


class MatterListResponse(BaseModel):
    """Paginated matter list."""

    items: list[MatterOut]
    page: int
    page_size: int
    total: int


class MatterProgressSummary(BaseModel):
    """Progress summary embedded in matter detail."""

    completeness_pct: int
    status: str


class MatterDetailResponse(BaseModel):
    """Matter detail: matter + household summary + progress summary."""

    matter: MatterOut
    household: MatterHouseholdSummary
    progress: MatterProgressSummary
