"""Persistence access for the identity domain (Postgres via psycopg)."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class ProfileRepository:
    """Read access to the ``profiles`` table (artifact §7.2).

    Runs on a caller-supplied connection from the core pool
    (``app.core.db``) — PERF-022: this repository previously opened a
    fresh ``psycopg.connect`` per ``/v1/me`` call, paying a full
    connection handshake on a hot single-row lookup and bypassing the
    pooled infrastructure every other domain uses. The repository
    boundary (services never touch SQL) is unchanged.

    Caller requirement: the connection MUST yield dict rows
    (``row_factory=dict_row``), as the core pool's connections do —
    :meth:`get_profile` returns ``fetchone()`` verbatim and its result
    is consumed as a mapping, so a tuple-row connection would break
    callers.
    """

    def get_profile(self, conn: Any, user_id: UUID) -> dict[str, Any] | None:
        """Return the profile row for ``user_id``, or ``None`` when absent."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, display_name, first_name, last_name, created_at"
                " FROM profiles WHERE id = %s",
                (str(user_id),),
            )
            return cur.fetchone()
