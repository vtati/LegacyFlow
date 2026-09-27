"""Pydantic schemas for the planning-decisions domain (Slice 2, Track C)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DecisionIn(BaseModel):
    """Record or replace one planning decision."""

    key: str = Field(min_length=1, max_length=200)
    value: Any


class DecisionOut(BaseModel):
    """A stored planning decision."""

    key: str
    value: Any
    decided_by: UUID | None = None
    updated_at: datetime | None = None
