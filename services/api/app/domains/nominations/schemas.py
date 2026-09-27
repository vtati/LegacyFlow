"""Pydantic schemas for the nominations domain (Slice 2)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NominationCreate(BaseModel):
    """Create a nomination on a matter."""

    nomination_type: str = Field(min_length=1, max_length=50)
    person_id: UUID
    priority: int = Field(default=1, ge=1)
    notes: str | None = Field(default=None, max_length=2000)


class NominationOut(BaseModel):
    """Public nomination shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    matter_id: UUID
    nomination_type: str
    person_id: UUID
    priority: int
    notes: str | None = None
    status: str = "proposed"
    created_at: datetime
