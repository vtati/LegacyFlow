"""Pydantic schemas for the audit domain."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    """Public audit-event shape (firm-scoped read)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID | None
    matter_id: UUID | None
    actor_user_id: UUID | None
    actor_type: str
    action: str
    resource_type: str
    resource_id: UUID | None
    correlation_id: UUID | None
    before_summary: dict[str, Any] | None
    after_summary: dict[str, Any] | None
    metadata: dict[str, Any]
    created_at: datetime


class AuditEventFilters(BaseModel):
    """Query filters for the audit-event list."""

    model_config = ConfigDict(from_attributes=True)

    action: str | None = None
    resource_type: str | None = None
    actor_type: str | None = None
    matter_id: UUID | None = None
    page: int = 1
    page_size: int = 50


class AuditEventListOut(BaseModel):
    """Paginated audit-event list."""

    items: list[AuditEventOut]
    page: int
    page_size: int
    total: int
