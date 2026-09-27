"""Persistence for guided-intake answers (Postgres via psycopg, Slice 2).

Repository functions take an open connection; services own transactions.
``intake_answers`` is keyed by ``(matter_id, question_key)``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def list_answers(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """Return every stored answer row for a matter."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT question_key, answer, updated_at FROM intake_answers WHERE matter_id = %s",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def upsert_answers(conn: Any, matter_id: UUID, answers: dict[str, Any]) -> None:
    """Insert or replace answers keyed by ``(matter_id, question_key)``."""
    if not answers:
        return
    rows = [(str(matter_id), key, json.dumps(value)) for key, value in answers.items()]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO intake_answers (matter_id, question_key, answer)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (matter_id, question_key)
            DO UPDATE SET answer = EXCLUDED.answer, updated_at = now()
            """,
            rows,
        )
