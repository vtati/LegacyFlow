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
    # Phase 4 (P4): the user parked this item ("don't know yet"). A
    # parked item is displayed distinctly — never counted answered,
    # never counted missing — and still gates document generation.
    parked: bool = False


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
    # Phase 4 (P4): required items currently parked, in pack order.
    parked_required_keys: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Parked items (Phase 4, "don't know yet")
# ---------------------------------------------------------------------------


class ParkItemCreate(BaseModel):
    """Park one question ("don't know yet — ask me later")."""

    question_key: str = Field(min_length=1, max_length=120)


class ParkedItemOut(BaseModel):
    question_key: str
    label: str
    required: bool
    parked_at: datetime


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
    # The router's auto-confirm reads/updates this; without the field it
    # raised AttributeError (swallowed) and never ran (found 2026-10-24).
    status: str = "proposed"


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
    # Phase 4 (P4): required items parked after this turn, in pack order.
    parked_required_keys: list[str] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Document extraction (Phase 2)
# ---------------------------------------------------------------------------


class ExtractionProposalOut(BaseModel):
    proposal_id: UUID
    question_key: str
    value: Any
    confidence: float
    source_page: int | None = None
    source_snippet: str


class ExtractionOut(BaseModel):
    extraction_id: UUID
    filename: str
    status: str
    proposals: list[ExtractionProposalOut]
    skipped_existing: list[str]
    discarded: int
    truncated: bool
    ocr_used: bool = False
    # OCR visibility (F7): the upload needed OCR / OCR could not run,
    # so the client can show an explicit "couldn't read this scan" state.
    ocr_attempted: bool = False
    ocr_unavailable: bool = False


class ExtractionSummaryOut(BaseModel):
    id: UUID
    filename: str
    status: str
    page_count: int
    proposals_created: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Consistency review (Phase 3)
# ---------------------------------------------------------------------------


class ConsistencyFindingOut(BaseModel):
    question: str
    fields: list[str]
    severity: str


class ConsistencyReviewOut(BaseModel):
    review_id: UUID
    findings: list[ConsistencyFindingOut]
    escalations_created: int
    facts_considered: int
