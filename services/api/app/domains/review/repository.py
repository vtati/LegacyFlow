"""Persistence for the attorney-review domain (psycopg, Slice 3).

Repository functions take an open connection; services own transactions.
Covers approval_gates, review_packets, comments, clarification_requests,
notification_outbox, and jurisdiction-pack registration.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------


def insert_gate(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    gate_type: str,
    notes: str | None,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Create a pending approval gate; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approval_gates
              (firm_id, matter_id, gate_type, status, notes)
            VALUES (%s, %s, %s, 'pending', %s)
            RETURNING *
            """,
            (str(firm_id), str(matter_id), gate_type, notes),
        )
        return dict(cur.fetchone())


def get_gate(conn: Any, gate_id: UUID) -> dict[str, Any] | None:
    """Return an approval gate row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM approval_gates WHERE id = %s", (str(gate_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_gates(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's approval gates, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM approval_gates WHERE matter_id = %s ORDER BY created_at DESC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def latest_gate_by_status(
    conn: Any, matter_id: UUID, gate_type: str, status: str
) -> dict[str, Any] | None:
    """Return the most recent gate of ``gate_type``/``status`` for a matter."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM approval_gates
            WHERE matter_id = %s AND gate_type = %s AND status = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(matter_id), gate_type, status),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def decide_gate(
    conn: Any,
    gate_id: UUID,
    *,
    decision: str,
    decided_by_user_id: UUID,
    notes: str | None,
) -> dict[str, Any]:
    """Record an approve/reject decision on a pending gate; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE approval_gates
            SET status = %s,
                decided_by_user_id = %s,
                decided_at = now(),
                notes = COALESCE(%s, notes),
                updated_at = now()
            WHERE id = %s AND status = 'pending'
            RETURNING *
            """,
            (decision, str(decided_by_user_id), notes, str(gate_id)),
        )
        row = cur.fetchone()
    # ``service`` guards on pending status first; a missing row here means a
    # concurrent decision raced us — treat it as gone rather than inventing
    # a second decision.
    if row is None:
        raise LookupError(f"Gate {gate_id} is no longer pending.")
    return dict(row)


# ---------------------------------------------------------------------------
# Review packets
# ---------------------------------------------------------------------------


def next_packet_version(conn: Any, matter_id: UUID) -> int:
    """Return the next snapshot version number for a matter (1-based)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_version"
            " FROM review_packets WHERE matter_id = %s",
            (str(matter_id),),
        )
        return int(cur.fetchone()["max_version"]) + 1


def insert_packet(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    version: int,
    snapshot: dict[str, Any],
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Store a review packet snapshot; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO review_packets
              (firm_id, matter_id, version, snapshot, created_by_user_id)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING *
            """,
            (
                str(firm_id),
                str(matter_id),
                version,
                json.dumps(snapshot, default=str),
                str(created_by_user_id),
            ),
        )
        return dict(cur.fetchone())


def list_packets(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's packets, newest version first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM review_packets WHERE matter_id = %s ORDER BY version DESC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_packet(conn: Any, packet_id: UUID) -> dict[str, Any] | None:
    """Return a review packet row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM review_packets WHERE id = %s", (str(packet_id),))
        row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def insert_comment(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    author_user_id: UUID,
    body: str,
) -> dict[str, Any]:
    """Insert a matter comment; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO comments (firm_id, matter_id, author_user_id, body)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (str(firm_id), str(matter_id), str(author_user_id), body),
        )
        return dict(cur.fetchone())


def list_comments(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's comments in chronological order."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM comments WHERE matter_id = %s ORDER BY created_at ASC, id ASC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Clarification requests
# ---------------------------------------------------------------------------


def insert_clarification(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    question: str,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Create an open clarification request; return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clarification_requests
              (firm_id, matter_id, question, status, created_by_user_id)
            VALUES (%s, %s, %s, 'open', %s)
            RETURNING *
            """,
            (str(firm_id), str(matter_id), question, str(created_by_user_id)),
        )
        return dict(cur.fetchone())


def get_clarification(conn: Any, clarification_id: UUID) -> dict[str, Any] | None:
    """Return a clarification row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM clarification_requests WHERE id = %s",
            (str(clarification_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def list_clarifications(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's clarification requests, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM clarification_requests"
            " WHERE matter_id = %s ORDER BY created_at ASC, id ASC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def answer_clarification(
    conn: Any,
    clarification_id: UUID,
    *,
    response: str,
    answered_by_user_id: UUID,
) -> dict[str, Any] | None:
    """Record a response on an open clarification; return the row.

    Returns ``None`` when the clarification is not open (already answered
    or raced) — the service turns that into a 409.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE clarification_requests
            SET status = 'answered',
                response = %s,
                answered_by_user_id = %s,
                answered_at = now(),
                updated_at = now()
            WHERE id = %s AND status = 'open'
            RETURNING *
            """,
            (response, str(answered_by_user_id), str(clarification_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Notification outbox
# ---------------------------------------------------------------------------


def enqueue_notification(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID | None,
    template_key: str,
    payload: dict[str, Any],
    recipient_user_id: UUID | None = None,
    recipient_email: str | None = None,
) -> dict[str, Any]:
    """Queue one notification row; return it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO notification_outbox
              (firm_id, matter_id, recipient_user_id, recipient_email,
               template_key, payload, status, attempts)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending', 0)
            RETURNING *
            """,
            (
                str(firm_id),
                str(matter_id) if matter_id else None,
                str(recipient_user_id) if recipient_user_id else None,
                recipient_email,
                template_key,
                json.dumps(payload, default=str),
            ),
        )
        return dict(cur.fetchone())


def dequeue_pending(conn: Any, limit: int) -> list[dict[str, Any]]:
    """Lock and return up to ``limit`` pending outbox rows (oldest first)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM notification_outbox
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def mark_outbox_sent(conn: Any, outbox_id: UUID) -> None:
    """Mark a row sent (attempts incremented once more for the ledger)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE notification_outbox
            SET status = 'sent', attempts = attempts + 1, sent_at = now()
            WHERE id = %s
            """,
            (str(outbox_id),),
        )


def mark_outbox_failed(conn: Any, outbox_id: UUID, *, max_attempts: int) -> None:
    """Bump attempts; give up (status='failed') once ``max_attempts`` is hit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE notification_outbox
            SET attempts = attempts + 1,
                status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END
            WHERE id = %s
            """,
            (max_attempts, str(outbox_id)),
        )


# ---------------------------------------------------------------------------
# Notification recipient resolution
# ---------------------------------------------------------------------------


def firm_staff_recipients(conn: Any, firm_id: UUID) -> list[dict[str, Any]]:
    """Active firm staff (firm_admin/attorney/staff) with profile emails."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fm.user_id, p.email
            FROM firm_memberships fm
            JOIN profiles p ON p.id = fm.user_id
            WHERE fm.firm_id = %s
              AND fm.status = 'active'
              AND fm.role IN ('firm_admin', 'attorney', 'staff')
              AND p.email IS NOT NULL
            """,
            (str(firm_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def household_member_recipients(
    conn: Any, household_id: UUID, roles: frozenset[str] | set[str]
) -> list[dict[str, Any]]:
    """Active household members in ``roles`` with profile emails."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT hm.user_id, p.email
            FROM household_members hm
            JOIN profiles p ON p.id = hm.user_id
            WHERE hm.household_id = %s
              AND hm.access_status = 'active'
              AND hm.relationship_role = ANY(%s)
              AND p.email IS NOT NULL
            """,
            (str(household_id), sorted(roles)),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Jurisdiction-pack registration
# ---------------------------------------------------------------------------


def find_jurisdiction(conn: Any, country_code: str, subdivision_code: str) -> dict[str, Any] | None:
    """Return a jurisdiction row for the code pair, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM jurisdictions WHERE country_code = %s AND subdivision_code = %s",
            (country_code, subdivision_code),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def insert_jurisdiction(
    conn: Any,
    *,
    country_code: str,
    subdivision_code: str,
    display_name: str,
    jurisdiction_type: str,
) -> dict[str, Any]:
    """Insert a jurisdiction row; return it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jurisdictions
              (country_code, subdivision_code, display_name, jurisdiction_type, is_enabled)
            VALUES (%s, %s, %s, %s, false)
            RETURNING *
            """,
            (country_code, subdivision_code, display_name, jurisdiction_type),
        )
        return dict(cur.fetchone())


def insert_jurisdiction_pack(
    conn: Any,
    *,
    jurisdiction_id: UUID,
    version: str,
    status: str,
    effective_from: str,
    manifest: dict[str, Any],
    source_bundle_hash: str,
) -> dict[str, Any]:
    """Register a pack version; return the row.

    Raises ``psycopg.errors.UniqueViolation`` when the
    (jurisdiction, version) pair already exists — the service maps that to
    a 409.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jurisdiction_packs
              (jurisdiction_id, version, status, effective_from, manifest, source_bundle_hash)
            VALUES (%s, %s, %s, %s::date, %s::jsonb, %s)
            RETURNING *
            """,
            (
                str(jurisdiction_id),
                version,
                status,
                effective_from,
                # default=str: YAML manifests can carry date/datetime
                # objects (unquoted effective_from) — JSON has no date
                # type, so serialize them as their ISO-ish string form
                # instead of raising TypeError (M-1). Sibling dumps in
                # this module already use default=str.
                json.dumps(manifest, default=str),
                source_bundle_hash,
            ),
        )
        return dict(cur.fetchone())


def list_packs(conn: Any) -> list[dict[str, Any]]:
    """List registered pack versions with their jurisdiction codes."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT jp.id, jp.version, jp.status, jp.effective_from, jp.created_at,
                   j.country_code, j.subdivision_code, j.display_name,
                   (jp.manifest -> 'pack' ->> 'id') AS pack_id
            FROM jurisdiction_packs jp
            JOIN jurisdictions j ON j.id = jp.jurisdiction_id
            ORDER BY j.country_code, j.subdivision_code, jp.created_at DESC
            """,
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Jurisdiction-pack lifecycle (UPDATE path)
# ---------------------------------------------------------------------------


def get_jurisdiction_pack(conn: Any, pack_id: UUID) -> dict[str, Any] | None:
    """Return one jurisdiction_pack row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM jurisdiction_packs WHERE id = %s", (str(pack_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_jurisdiction_pack_by_ref(conn: Any, ref: str) -> dict[str, Any] | None:
    """Resolve a pack by UUID id or by manifest pack id (e.g. ``us-va``).

    When several versions share a manifest pack id, the most recently
    created version wins.
    """
    try:
        row = get_jurisdiction_pack(conn, UUID(ref))
    except ValueError:
        row = None
    if row is not None:
        return row
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM jurisdiction_packs
            WHERE manifest -> 'pack' ->> 'id' = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (ref,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_jurisdiction_pack_status(
    conn: Any,
    pack_id: UUID,
    *,
    status: str,
    approved_by_user_id: UUID | None = None,
) -> dict[str, Any]:
    """UPDATE path for the pack lifecycle; return the updated row.

    Transitions into ``approved`` record the approver and timestamp in
    ``approved_by_user_id`` / ``approved_at``.
    """
    with conn.cursor() as cur:
        if status == "approved":
            cur.execute(
                """
                UPDATE jurisdiction_packs
                SET status = %s,
                    approved_by_user_id = %s,
                    approved_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (
                    status,
                    str(approved_by_user_id) if approved_by_user_id is not None else None,
                    str(pack_id),
                ),
            )
        else:
            cur.execute(
                "UPDATE jurisdiction_packs SET status = %s WHERE id = %s RETURNING *",
                (status, str(pack_id)),
            )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"jurisdiction pack {pack_id} not found")
    return dict(row)
