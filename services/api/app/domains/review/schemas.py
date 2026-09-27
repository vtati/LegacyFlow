"""Pydantic schemas for the attorney-review domain (Slice 3).

Covers review packets, matter comments, clarification requests,
approval gates, content-pack management, and notification payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

GateType = Literal["intake_approval", "document_release"]
GateStatus = Literal["pending", "approved", "rejected"]
GateDecision = Literal["approved", "rejected"]
ClarificationStatus = Literal["open", "answered"]


# ---------------------------------------------------------------------------
# Review packets
# ---------------------------------------------------------------------------


class ReviewPacketCreate(BaseModel):
    """Generate a review packet (``POST /v1/matters/{id}/review-packets``).

    The snapshot is generated server-side from the matter's current data;
    ``notes`` is an optional human annotation stored alongside the packet.
    """

    notes: str | None = Field(default=None, max_length=1000)


class ReviewPacketOut(BaseModel):
    """A generated review packet (list view — snapshot not embedded)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    matter_id: UUID
    version: int
    created_by_user_id: UUID
    created_at: datetime


class ReviewPacketDetailOut(ReviewPacketOut):
    """A review packet with its full point-in-time snapshot."""

    snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class CommentCreate(BaseModel):
    """Post a matter comment (``POST /v1/matters/{id}/comments``)."""

    body: str = Field(min_length=1, max_length=5000)


class CommentOut(BaseModel):
    """A matter comment."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    matter_id: UUID
    author_user_id: UUID
    body: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Clarification requests
# ---------------------------------------------------------------------------


class ClarificationCreate(BaseModel):
    """Ask the household a clarification question (attorney only)."""

    question: str = Field(min_length=1, max_length=2000)


class ClarificationRespond(BaseModel):
    """Answer a clarification request (household member)."""

    response: str = Field(min_length=1, max_length=5000)


class ClarificationOut(BaseModel):
    """A clarification request."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    matter_id: UUID
    question: str
    status: ClarificationStatus
    created_by_user_id: UUID
    answered_by_user_id: UUID | None = None
    answered_at: datetime | None = None
    response: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------


class GateCreate(BaseModel):
    """Create an approval gate (``POST /v1/matters/{id}/gates``)."""

    gate_type: GateType
    notes: str | None = Field(default=None, max_length=1000)


class GateDecide(BaseModel):
    """Decide an approval gate (attorney/firm_admin only)."""

    decision: GateDecision
    notes: str | None = Field(default=None, max_length=1000)


class GateOut(BaseModel):
    """An approval gate."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    matter_id: UUID
    gate_type: GateType
    status: GateStatus
    decided_by_user_id: UUID | None = None
    decided_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Content packs
# ---------------------------------------------------------------------------


class PackImportResponse(BaseModel):
    """Result of a content-pack import."""

    pack_id: str
    jurisdiction_code: str
    version: str
    status: str
    source_bundle_hash: str
    jurisdiction_id: UUID


class PackOut(BaseModel):
    """An available jurisdiction content pack."""

    model_config = ConfigDict(from_attributes=True)

    jurisdiction_code: str
    pack_id: str
    display_name: str
    version: str
    status: str
    effective_from: str | None = None
