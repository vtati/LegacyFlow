"""Append-only audit event writer (FR-AUD-001, §11.3).

``audit_events`` is append-only: no UPDATE/DELETE policies exist and this
module only ever INSERTs. Payloads must be summaries — never raw plan
content, documents, credentials, or AI prompts (engineering contract rule 9).
Callers are responsible for redacting before calling.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import json


def record_audit_event(
    conn: Any,
    *,
    action: str,
    resource_type: str,
    actor_user_id: UUID | None,
    actor_type: str,
    firm_id: UUID | None = None,
    matter_id: UUID | None = None,
    resource_id: UUID | None = None,
    correlation_id: UUID | None = None,
    before_summary: dict[str, Any] | None = None,
    after_summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> UUID:
    """Insert an audit event and return its id.

    ``actor_type`` is one of: user, system, worker, support.
    """
    event_id = uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_events
              (id, firm_id, matter_id, actor_user_id, actor_type, action,
               resource_type, resource_id, correlation_id,
               before_summary, after_summary, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            """,
            (
                str(event_id),
                str(firm_id) if firm_id else None,
                str(matter_id) if matter_id else None,
                str(actor_user_id) if actor_user_id else None,
                actor_type,
                action,
                resource_type,
                str(resource_id) if resource_id else None,
                str(correlation_id) if correlation_id else None,
                json.dumps(before_summary) if before_summary is not None else None,
                json.dumps(after_summary) if after_summary is not None else None,
                json.dumps(metadata or {}),
            ),
        )
    return event_id
