"""Pydantic schemas for the privacy domain."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationPreferencesOut(BaseModel):
    """The caller's notification preference row."""

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email_enabled: bool
    reminder_enabled: bool
    unsubscribed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationPreferencesUpdate(BaseModel):
    """Partial update to notification preferences (at least one field)."""

    email_enabled: bool | None = None
    reminder_enabled: bool | None = None


class DeletionRequestCreate(BaseModel):
    """Payload for filing a data deletion request."""

    notes: str | None = Field(default=None, max_length=2000)


class DeletionRequestOut(BaseModel):
    """A data deletion request row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    firm_id: UUID | None
    status: str
    requested_at: datetime
    processed_at: datetime | None
    notes: str | None
    created_at: datetime


class ExportHouseholdMember(BaseModel):
    """Household membership as seen in an export.

    Other users' contact details (e.g. profile emails) are never included —
    only the membership structure the caller is already entitled to see.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    relationship_role: str
    access_status: str
    created_at: datetime


class ExportHousehold(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    display_name: str
    created_at: datetime
    members: list[ExportHouseholdMember] = []


class ExportMatter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    household_id: UUID
    plan_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class ExportPerson(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    full_name: str
    date_of_birth: date | None = None
    email: str | None = None
    kind: str | None = None
    created_at: datetime


class ExportDecision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    matter_id: UUID
    decision_key: str
    value: dict[str, object]
    decided_by: UUID | None
    created_at: datetime
    updated_at: datetime


class ExportAsset(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    matter_id: UUID | None
    asset_type: str
    label: str
    estimated_value_cents: int | None
    ownership: str | None
    beneficiary_designation: dict[str, object]
    notes: str | None
    created_at: datetime
    updated_at: datetime


class DataExportOut(BaseModel):
    """The caller's own data: profile, households, matters, persons,
    decisions, assets. ``truncated`` is true when any collection hit the
    per-collection cap."""

    exported_at: datetime
    truncated: bool
    profile: dict[str, object]
    households: list[ExportHousehold]
    matters: list[ExportMatter]
    persons: list[ExportPerson]
    decisions: list[ExportDecision]
    assets: list[ExportAsset]


class ReminderDispatchOut(BaseModel):
    """Result of ``POST /v1/admin/reminders/dispatch``."""

    dispatched: bool
    actor: str
    schedule: str
