"""Pydantic v2 schemas for the Attorney Reviewer domain."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


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
    acknowledged_by_actor: str | None = None
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
    # ERR-020: True when the model layer did not complete for this run
    # (provider failure -> deterministic-only, or unparseable output),
    # so clients can show "the judgment pass hasn't run" instead of
    # rendering a degraded run identically to a clean full review.
    degraded: bool = False
    # ERR-019: findings skipped by per-finding savepoint isolation.
    skipped_findings: int = 0


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
    # ERR-025: the latest FAILED run for the current fingerprint
    # (status + timestamp only — RunOut carries no raw error), so a
    # repeatedly failing fingerprint doesn't look never-attempted.
    last_failed_run: RunOut | None = None


class RunIn(BaseModel):
    """Explicit run request. ``force`` re-runs despite an existing
    completed run for the current fingerprint; honored for firm-side
    actors only (a family caller cannot burn repeated model calls)."""

    force: bool = False


class AcknowledgeIn(BaseModel):
    """An acknowledgment is an accountability record: the note is the
    override's justification, so it must be real text (review L1) —
    non-empty after trimming and bounded."""

    note: str

    @field_validator("note")
    @classmethod
    def _note_must_be_substantive(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("An acknowledgment note is required.")
        if len(trimmed) > 2000:
            raise ValueError("The acknowledgment note is too long (2000 characters max).")
        return trimmed
