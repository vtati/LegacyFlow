"""Planning-decision application services (Slice 2, Track C).

Decisions record household planning choices (e.g. fiduciary picks) keyed
by a stable decision key. Writes need intake write access; the audit trail
records the decision key only — never the value (contract rule 9).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.audit import record_audit_event
from app.domains.decisions import repository as repo
from app.domains.decisions.schemas import DecisionOut
from app.domains.matters import service as matters_service


def _decision_out(row: dict[str, Any]) -> DecisionOut:
    decided_by = row.get("decided_by")
    return DecisionOut(
        key=str(row["decision_key"]),
        value=row["value"],
        decided_by=UUID(str(decided_by)) if decided_by else None,
        updated_at=row.get("updated_at"),
    )


def save_decision(
    conn: Any, *, actor_sub: str, matter_id: UUID, key: str, value: Any
) -> DecisionOut:
    """Record or replace a planning decision (write access required)."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    row = repo.upsert_decision(conn, matter_id=matter_id, key=key, value=value, decided_by=actor_id)
    record_audit_event(
        conn,
        action="decisions.saved",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=matter_id,
        after_summary={"decision_key": key},
    )
    return _decision_out(row)


def list_decisions(conn: Any, *, actor_sub: str, matter_id: UUID) -> list[DecisionOut]:
    """List a matter's planning decisions (read access required)."""
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    return [_decision_out(row) for row in repo.list_decisions(conn, matter_id)]
