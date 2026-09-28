"""Pydantic v2 schemas for the feature-flags domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FeatureFlagKey(StrEnum):
    """Known feature-flag keys. Unknown keys fail closed at resolution."""

    DOCUMENT_ASSEMBLY = "document_assembly"
    AI_ASSISTANCE = "ai_assistance"
    FUNDING_REMINDERS = "funding_reminders"
    AI_INTERVIEWER = "ai_interviewer"


class FeatureFlagScope(StrEnum):
    """Flag scopes, from narrowest to broadest applicability."""

    ENVIRONMENT = "environment"
    FIRM = "firm"
    JURISDICTION = "jurisdiction"


class FeatureFlagOut(BaseModel):
    """A stored feature-flag row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    scope: str
    scope_ref: str
    is_enabled: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class EffectiveFlagOut(BaseModel):
    """A flag resolved to its effective value for a firm (and pack)."""

    key: str
    is_enabled: bool
    source: str  # env | firm | jurisdiction | environment | default


class FeatureFlagSetIn(BaseModel):
    """Payload for setting a firm-scope flag."""

    is_enabled: bool
