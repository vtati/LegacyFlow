"""Pydantic schemas for the guided-intake domain (Slice 2, Track C)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domains.documents.schemas import PlannedDocumentOut

__all__ = [
    "AnswersIn",
    "AnswersOut",
    "IntakeProgressOut",
    "PlannedDocumentOut",
    "QuestionSetProgress",
    "QuestionSetsOut",
]


class QuestionSetsOut(BaseModel):
    """Question sets served for a matter, bound to its snapshotted pack."""

    jurisdiction_code: str
    pack_version: str
    question_sets: dict[str, dict[str, Any]]


class AnswersIn(BaseModel):
    """Autosave payload: question key -> answer value."""

    answers: dict[str, Any] = Field(default_factory=dict)


class AnswersOut(BaseModel):
    """Stored answers for a matter."""

    answers: dict[str, Any]
    updated_at: datetime | None = None


class QuestionSetProgress(BaseModel):
    """Completeness of one question set."""

    total_questions: int
    answered: int
    required_total: int
    required_answered: int
    percent_complete: float


class IntakeProgressOut(BaseModel):
    """Per-set progress plus the overall required-question completeness."""

    sets: dict[str, QuestionSetProgress]
    # Per-section breakdown ({set_id: {section_id: progress}}) with the
    # same semantics as the per-set rows; the wizard's section nav reads
    # this so each section shows its own answered share.
    sections: dict[str, dict[str, QuestionSetProgress]] = {}
    completeness_pct: float
    overall: QuestionSetProgress
    # The document set the draft step will produce from the stored
    # answers (2026-10-31) — the wizard shows it from the first paint
    # ("Based on your answers so far, your plan will produce: …"), so
    # it rides the payload the wizard already polls.
    documents: list[PlannedDocumentOut] = []
