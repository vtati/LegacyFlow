"""Pydantic schemas for the identity domain."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProfileOut(BaseModel):
    """Public profile shape returned by ``GET /v1/me``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    created_at: datetime
