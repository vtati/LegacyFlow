"""Persistence for the feature-flags domain (``feature_flags`` table)."""

from __future__ import annotations

from typing import Any
from uuid import UUID


def get_flag(conn: Any, *, key: str, scope: str, scope_ref: str) -> dict[str, Any] | None:
    """Return the flag row for (key, scope, scope_ref), or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM feature_flags WHERE key = %s AND scope = %s AND scope_ref = %s",
            (key, scope, scope_ref),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_flag(
    conn: Any,
    *,
    key: str,
    scope: str,
    scope_ref: str,
    is_enabled: bool,
    created_by_user_id: UUID | None,
) -> dict[str, Any]:
    """Insert or update a flag row; return the resulting row."""
    actor_id_str = str(created_by_user_id) if created_by_user_id is not None else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_flags (key, scope, scope_ref, is_enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (key, scope, scope_ref) DO UPDATE
              SET is_enabled = EXCLUDED.is_enabled,
                  created_by_user_id = EXCLUDED.created_by_user_id
            RETURNING *
            """,
            (key, scope, scope_ref, is_enabled, actor_id_str),
        )
        return dict(cur.fetchone())
