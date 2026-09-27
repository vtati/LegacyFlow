"""Persistence for AI gateway interactions (Postgres via psycopg, Slice 4).

Repository functions take an open connection; services own transactions.
Only summaries are ever stored: raw questions and answers never reach
this layer.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def insert_ai_interaction(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    user_id: UUID,
    question_summary: str,
    answer_summary: str,
    citations: list[dict[str, Any]],
    trace_hash: str,
) -> dict[str, Any]:
    """Record one gateway interaction (summaries only); return the row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_interactions
              (firm_id, matter_id, user_id, question_summary, answer_summary,
               citations, trace_hash)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING *
            """,
            (
                str(firm_id),
                str(matter_id),
                str(user_id),
                question_summary,
                answer_summary,
                json.dumps(citations),
                trace_hash,
            ),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else {}
