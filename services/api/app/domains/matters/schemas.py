"""Pydantic schemas for the matters domain (artifact §7.2).

``status`` stays an opaque string in Slice 1; the server-side state machine
with audited transitions arrives in Slice 2 (artifact §15.5).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MatterOut(BaseModel):
    """Public matter shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    household_id: UUID
    primary_jurisdiction_id: UUID
    jurisdiction_pack_id: UUID
    plan_type: str
    status: str
    status_version: int
    jurisdiction_confirmation_status: str
    assigned_attorney_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
