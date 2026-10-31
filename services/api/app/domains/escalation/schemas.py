"""Pydantic schemas for the escalation domain (Slice 2, Track C)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.domains.documents.schemas import PlannedDocumentOut


class TriggeredRuleOut(BaseModel):
    """One escalation rule that fired during evaluation."""

    rule_id: str
    severity: str
    outcome: str
    user_message: str | None = None


class MissingAnswerOut(BaseModel):
    """One applicable required question the family has not answered —
    with everything the UI needs to send them back to it."""

    key: str
    label: str
    section_title: str
    set_id: str
    section_id: str


class CompletenessOut(BaseModel):
    """Answer completeness from the server's own partition (the same
    applicable/pending/pruned rules as progress and the submit gate)."""

    required_total: int
    required_answered: int
    missing: list[MissingAnswerOut]


class EvaluateOut(BaseModel):
    """Result of evaluating a matter's intake facts against pack rules.

    Since 2026-10-31 the response also answers the family's actual
    question — "am I complete, and what will my plan produce?" — via
    ``completeness`` (same partition as progress/submit) and
    ``documents`` (the projected draft set), stamped with
    ``checked_at``. The rule triage above stays the attorney-routing
    mechanism; these fields are its family-facing report.
    """

    triggered: list[TriggeredRuleOut]
    has_blocking: bool
    checked_at: datetime
    completeness: CompletenessOut
    documents: list[PlannedDocumentOut]


class EscalationOut(BaseModel):
    """A recorded escalation for a matter."""

    id: UUID
    matter_id: UUID
    rule_id: str
    severity: str
    outcome: str
    facts_snapshot: dict[str, Any]
    status: str
    created_at: datetime
