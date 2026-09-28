"""Pydantic v2 schemas for the AI interviewer (Phase 1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations (mirror the DB check constraints)
# ---------------------------------------------------------------------------


class ProposalSource(str):
    USER_STATED = "user_stated"
    DOCUMENT_EXTRACTED = "document_extracted"
    AI_INFERRED = "ai_inferred"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    """Start (or resume) an interview session. Idempotent per matter+set."""

    question_set_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, max_length=256)


class SessionStatusUpdate(BaseModel):
    """Pause / resume / abandon — transitions validated server-side."""

    status: str = Field(pattern="^(paused|in_progress|abandoned|completed)$")


class ChecklistItemOut(BaseModel):
    question_key: str
    required: bool
    answered: bool
    has_open_proposal: bool


class SessionOut(BaseModel):
    id: UUID
    matter_id: UUID
    question_set_id: str
    pack_version: str
    status: str
    completeness_pct: float
    missing_required_keys: list[str]
    checklist: list[ChecklistItemOut]
    open_proposal_count: int
    turn_count: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


class TurnCreate(BaseModel):
    """One user message. The assistant reply comes back synchronously."""

    message: str = Field(min_length=1, max_length=4000)
    idempotency_key: str | None = Field(default=None, max_length=256)


class CitationOut(BaseModel):
    source: str
    section: str | None = None
    excerpt: str


class ProposalDraftOut(BaseModel):
    """A proposal emitted by this turn, awaiting user verification."""

    id: UUID
    question_key: str
    proposed_value: Any
    source: str
    confidence: float | None
    source_snippet: str | None


class TurnOut(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    reply: str
    citations: list[CitationOut] = Field(default_factory=list)
    proposals: list[ProposalDraftOut] = Field(default_factory=list)
    refused: bool = False
    escalated: bool = False
    trace_hash: str
    completeness_pct: float
    missing_required_keys: list[str]
    replayed: bool = False


class TurnSummaryOut(BaseModel):
    id: UUID
    role: str
    summary: str
    created_at: datetime


class TurnDetailOut(BaseModel):
    """Full turn text for the session owner (matter read scope required)."""

    id: UUID
    role: str
    text: str
    citations: list[CitationOut] = Field(default_factory=list)
    created_at: datetime


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


class ProposalOut(BaseModel):
    id: UUID
    session_id: UUID
    question_key: str
    proposed_value: Any
    corrected_value: Any | None = None
    source: str
    confidence: float | None
    source_snippet: str | None
    source_page: int | None = None
    model_id: str
    prompt_version: str
    pack_version: str
    status: str
    created_at: datetime


class ProposalCorrect(BaseModel):
    """Confirm with an edited value (stores proposed + corrected)."""

    value: Any


# ---------------------------------------------------------------------------
# Dictation
# ---------------------------------------------------------------------------


class DictationOut(BaseModel):
    """Transcript only — never auto-sent. The user reviews before sending."""

    transcript: str
    model: str
