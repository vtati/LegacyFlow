"""Persistence access for the identity domain (Postgres via psycopg)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class ProfileRepository:
    """Read access to the ``profiles`` table (artifact §7.2).

    Slice-1 minimal implementation: opens a short-lived connection per call.
    Connection pooling and shared data-access infrastructure arrive with a
    later slice; the repository boundary (so services never touch SQL) is
    established now per §6.4 modular-monolith rules.
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_profile(self, user_id: UUID) -> dict[str, Any] | None:
        """Return the profile row for ``user_id``, or ``None`` when absent."""
        with psycopg.connect(self._database_url) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT id, email, display_name, first_name, last_name, created_at"
                    " FROM profiles WHERE id = %s",
                    (str(user_id),),
                )
                return cur.fetchone()
