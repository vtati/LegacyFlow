"""Pydantic schemas for the escalation domain (Slice 2, Track C)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class TriggeredRuleOut(BaseModel):
    """One escalation rule that fired during evaluation."""

    rule_id: str
    severity: str
    outcome: str
    user_message: str | None = None


class EvaluateOut(BaseModel):
    """Result of evaluating a matter's intake facts against pack rules."""

    triggered: list[TriggeredRuleOut]
    has_blocking: bool


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
