"""Persistence for the AI interviewer (Postgres via psycopg, Phase 1).

Repository functions take an open connection; services own transactions.
Raw turn text never reaches this layer — bodies are encrypted by the service
(ADR-002) before insert.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def get_active_session(
    conn: Any, matter_id: UUID, question_set_id: str
) -> dict[str, Any] | None:
    """Return the active (draft/in_progress/paused) session, if any."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM interview_sessions
            WHERE matter_id = %s AND question_set_id = %s
              AND status IN ('draft', 'in_progress', 'paused')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (str(matter_id), question_set_id),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def get_session(conn: Any, session_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM interview_sessions WHERE id = %s", (str(session_id),))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def insert_session(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    question_set_id: str,
    pack_version: str,
    created_by_user_id: UUID,
    status: str = "draft",
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interview_sessions
              (firm_id, matter_id, question_set_id, pack_version, status, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(firm_id),
                str(matter_id),
                question_set_id,
                pack_version,
                status,
                str(created_by_user_id),
            ),
        )
        row = cur.fetchone()
        return dict(row)


def update_session_status(
    conn: Any, session_id: UUID, status: str, *, completed_at: bool = False
) -> dict[str, Any]:
    with conn.cursor() as cur:
        if completed_at:
            cur.execute(
                """
                UPDATE interview_sessions
                SET status = %s, completed_at = now(), updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (status, str(session_id)),
            )
        else:
            cur.execute(
                """
                UPDATE interview_sessions
                SET status = %s, updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (status, str(session_id)),
            )
        row = cur.fetchone()
        return dict(row)


def count_turns(conn: Any, session_id: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM interview_turns WHERE session_id = %s",
            (str(session_id),),
        )
        row = cur.fetchone()
        return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


def find_turn_by_idempotency(
    conn: Any, session_id: UUID, idempotency_key: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM interview_turns
            WHERE session_id = %s AND idempotency_key = %s
            """,
            (str(session_id), idempotency_key),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def insert_turn(
    conn: Any,
    *,
    session_id: UUID,
    firm_id: UUID,
    matter_id: UUID,
    role: str,
    summary: str,
    citations: list[dict[str, Any]],
    proposals_emitted: int = 0,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interview_turns
              (session_id, firm_id, matter_id, role, summary, citations,
               proposals_emitted, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING *
            """,
            (
                str(session_id),
                str(firm_id),
                str(matter_id),
                role,
                summary,
                json.dumps(citations),
                proposals_emitted,
                idempotency_key,
            ),
        )
        return dict(cur.fetchone())


def insert_turn_body(
    conn: Any,
    *,
    turn_id: UUID,
    session_id: UUID,
    firm_id: UUID,
    matter_id: UUID,
    ciphertext: str,
    key_version: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interview_turn_bodies
              (turn_id, session_id, firm_id, matter_id, ciphertext, key_version)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(turn_id),
                str(session_id),
                str(firm_id),
                str(matter_id),
                ciphertext,
                key_version,
            ),
        )


def list_turn_summaries(conn: Any, session_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
    """Recent turn summaries (no raw bodies) for history views."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, role, summary, created_at FROM interview_turns
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (str(session_id), limit),
        )
        return [dict(row) for row in cur.fetchall()]


def get_turn_body_ciphertext(conn: Any, turn_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ciphertext, key_version FROM interview_turn_bodies WHERE turn_id = %s",
            (str(turn_id),),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def insert_proposal(
    conn: Any,
    *,
    session_id: UUID,
    turn_id: UUID | None,
    firm_id: UUID,
    matter_id: UUID,
    question_key: str,
    proposed_value: Any,
    source: str,
    confidence: float | None,
    source_snippet: str | None,
    source_document_id: UUID | None = None,
    source_page: int | None = None,
    model_id: str,
    prompt_version: str,
    pack_version: str,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO field_proposals
              (session_id, turn_id, firm_id, matter_id, question_key,
               proposed_value, source, confidence, source_snippet,
               source_document_id, source_page, model_id, prompt_version, pack_version)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(session_id),
                str(turn_id) if turn_id else None,
                str(firm_id),
                str(matter_id),
                question_key,
                json.dumps(proposed_value),
                source,
                confidence,
                source_snippet,
                str(source_document_id) if source_document_id else None,
                source_page,
                model_id,
                prompt_version,
                pack_version,
            ),
        )
        return dict(cur.fetchone())


def get_proposal(conn: Any, proposal_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM field_proposals WHERE id = %s", (str(proposal_id),))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def decide_proposal(
    conn: Any,
    proposal_id: UUID,
    *,
    status: str,
    corrected_value: Any | None,
    decided_by_user_id: UUID,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE field_proposals
            SET status = %s,
                corrected_value = %s::jsonb,
                confirmed_by_user_id = %s,
                decided_at = now(),
                updated_at = now()
            WHERE id = %s AND status = 'proposed'
            RETURNING *
            """,
            (
                status,
                json.dumps(corrected_value) if corrected_value is not None else None,
                str(decided_by_user_id),
                str(proposal_id),
            ),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else {}


def list_open_proposals(conn: Any, session_id: UUID) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM field_proposals
            WHERE session_id = %s AND status = 'proposed'
            ORDER BY created_at ASC
            """,
            (str(session_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def count_open_proposals(conn: Any, session_id: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM field_proposals WHERE session_id = %s AND status = 'proposed'",
            (str(session_id),),
        )
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def open_proposal_keys(conn: Any, session_id: UUID) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT question_key FROM field_proposals WHERE session_id = %s AND status = 'proposed'",
            (str(session_id),),
        )
        return {str(row["question_key"]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# AI traces
# ---------------------------------------------------------------------------


def insert_ai_trace(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    turn_id: UUID | None,
    provider: str,
    model_id: str,
    model_version: str | None,
    prompt_template: str,
    prompt_version: str,
    pack_version: str,
    tokens_in: int | None,
    tokens_out: int | None,
    latency_ms: int | None,
    retrieved_doc_ids: list[str],
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_traces
              (firm_id, matter_id, turn_id, provider, model_id, model_version,
               prompt_template, prompt_version, pack_version,
               tokens_in, tokens_out, latency_ms, retrieved_doc_ids)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                str(firm_id),
                str(matter_id),
                str(turn_id) if turn_id else None,
                provider,
                model_id,
                model_version,
                prompt_template,
                prompt_version,
                pack_version,
                tokens_in,
                tokens_out,
                latency_ms,
                json.dumps(retrieved_doc_ids),
            ),
        )
        return dict(cur.fetchone())


def list_recent_bodies(
    conn: Any, session_id: UUID, limit: int = 10
) -> list[dict[str, Any]]:
    """Recent turn bodies (ciphertext) with roles, oldest first — for history."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.role, b.ciphertext
            FROM interview_turns t
            JOIN interview_turn_bodies b ON b.turn_id = t.id
            WHERE t.session_id = %s
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            (str(session_id), limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
        rows.reverse()
        return rows


def list_proposals_by_turn(conn: Any, turn_id: UUID) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM field_proposals WHERE turn_id = %s ORDER BY created_at ASC",
            (str(turn_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_turn(conn: Any, turn_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM interview_turns WHERE id = %s", (str(turn_id),))
        row = cur.fetchone()
        return dict(row) if row is not None else None
