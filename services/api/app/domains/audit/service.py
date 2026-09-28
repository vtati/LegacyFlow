"""Read-only audit-event queries — firm_admin only, scoped to the caller's firm."""

from typing import Any
from uuid import UUID

from app.core import authz
from app.core.errors import AuthError
from app.domains.audit.schemas import (
    AuditEventFilters,
    AuditEventListOut,
    AuditEventOut,
)


def _caller_id(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def _row_to_out(r: Any) -> AuditEventOut:
    return AuditEventOut(
        id=UUID(str(r["id"])),
        firm_id=UUID(str(r["firm_id"])) if r["firm_id"] else None,
        matter_id=UUID(str(r["matter_id"])) if r["matter_id"] else None,
        actor_user_id=UUID(str(r["actor_user_id"])) if r["actor_user_id"] else None,
        actor_type=r["actor_type"],
        action=r["action"],
        resource_type=r["resource_type"],
        resource_id=UUID(str(r["resource_id"])) if r["resource_id"] else None,
        correlation_id=UUID(str(r["correlation_id"])) if r["correlation_id"] else None,
        before_summary=r["before_summary"],
        after_summary=r["after_summary"],
        metadata=r["metadata"] or {},
        created_at=r["created_at"],
    )


def list_events(conn: Any, *, actor_sub: str, filters: AuditEventFilters) -> AuditEventListOut:
    """List audit events for the caller's firm (firm_admin only), newest first."""
    actor_id = _caller_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin"})

    page = max(1, filters.page)
    page_size = min(200, max(1, filters.page_size))
    offset = (page - 1) * page_size

    where = ["firm_id = %s"]
    params: list[Any] = [str(firm_id)]
    if filters.action:
        where.append("action = %s")
        params.append(filters.action)
    if filters.resource_type:
        where.append("resource_type = %s")
        params.append(filters.resource_type)
    if filters.actor_type:
        where.append("actor_type = %s")
        params.append(filters.actor_type)
    if filters.matter_id:
        where.append("matter_id = %s")
        params.append(str(filters.matter_id))
    where_sql = " AND ".join(where)

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM audit_events WHERE {where_sql}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            f"""SELECT * FROM audit_events WHERE {where_sql}
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (*params, page_size, offset),
        )
        rows = cur.fetchall()
    return AuditEventListOut(
        items=[_row_to_out(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )
