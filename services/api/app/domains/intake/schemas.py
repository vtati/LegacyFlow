"""Pydantic schemas for the guided-intake domain (Slice 2, Track C)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    completeness_pct: float
    overall: QuestionSetProgress
