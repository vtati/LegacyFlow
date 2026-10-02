"""Schemas for attorney applications (manual onboarding)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AttorneyApplicationCreate(BaseModel):
    firm_id: UUID
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    bar_number: str = Field(min_length=1, max_length=100)
    jurisdictions: list[str] = Field(default_factory=list, max_length=20)
    years_experience: int | None = Field(default=None, ge=0, le=80)
    practice_areas: str | None = Field(default=None, max_length=2000)
    document_urls: dict[str, Any] = Field(default_factory=dict)


class AttorneyApplicationOut(BaseModel):
    id: UUID
    firm_id: UUID
    user_id: UUID
    full_name: str
    email: str
    phone: str | None
    bar_number: str
    jurisdictions: list[str]
    years_experience: int | None
    practice_areas: str | None
    document_urls: dict[str, Any]
    status: str
    rejection_reason: str | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AttorneyApplicationReject(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
