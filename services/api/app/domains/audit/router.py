"""Audit-event read endpoints — firm_admin only, scoped to the caller's firm."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.audit import service
from app.domains.audit.schemas import AuditEventFilters, AuditEventListOut

router = APIRouter(tags=["audit"])


@router.get("/admin/audit-events", response_model=AuditEventListOut)
def list_audit_events(
    claims: Annotated[Claims, Depends(get_current_user)],
    action: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    actor_type: Annotated[str | None, Query()] = None,
    matter_id: Annotated[UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditEventListOut:
    """List the caller's firm audit events, newest first (firm_admin only)."""
    filters = AuditEventFilters(
        action=action,
        resource_type=resource_type,
        actor_type=actor_type,
        matter_id=matter_id,
        page=page,
        page_size=page_size,
    )
    with db.transaction() as conn:
        return service.list_events(conn, actor_sub=claims.sub, filters=filters)
