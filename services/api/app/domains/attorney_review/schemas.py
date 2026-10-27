"""Pydantic v2 schemas for the Attorney Reviewer domain."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FindingLocationOut(BaseModel):
    document: str | None = None
    field: str | None = None
    excerpt: str | None = None


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    layer: str
    check: str
    severity: str
    location: FindingLocationOut
    explanation: str
    suggested_fix: str | None = None
    disposition: str
    acknowledgment_note: str | None = None
    acknowledged_by_user_id: UUID | None = None
    acknowledged_at: datetime | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    model_id: str | None = None
    prompt_version: str | None = None
    source_fingerprint: str
    created_at: datetime
    completed_at: datetime | None = None


class RunResponseOut(BaseModel):
    run: RunOut
    findings: list[FindingOut]


class FindingsResponseOut(BaseModel):
    """The current findings read shared by the family + firm surfaces.

    ``enabled`` mirrors the firm flag so a firm without the reviewer
    renders nothing at all (the panel is absent, not errored).
    """

    enabled: bool
    run: RunOut | None = None
    findings: list[FindingOut] = []


class AcknowledgeIn(BaseModel):
    note: str
