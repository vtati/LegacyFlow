"""Pydantic schemas for the grounded AI gateway (Slice 4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AIAskRequest(BaseModel):
    """Question for the grounded AI gateway (max 1000 characters)."""

    question: str = Field(min_length=1, max_length=1000)


class Citation(BaseModel):
    """One grounded source for a claim in the AI answer."""

    source: str = Field(description="Pack id the excerpt was retrieved from.")
    section: str | None = None
    excerpt: str


class AIAnswer(BaseModel):
    """Gateway response: grounded answer, refusal, or attorney escalation."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    trace_hash: str
    refused: bool
