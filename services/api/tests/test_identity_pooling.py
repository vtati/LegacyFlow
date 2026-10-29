"""PERF-022: the identity repository must use the core connection pool.

Pre-fix, ``ProfileRepository`` opened a fresh ``psycopg.connect`` per
``/v1/me`` call — a new TCP+auth handshake on a hot single-row lookup,
outside the pooled infrastructure every other domain uses. The
repository now runs its query on a caller-supplied pooled connection
and never opens its own connection.
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.domains.identity.repository import ProfileRepository


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: object = None) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict:
        return {"id": "x"}


class _FakeConn:
    def __init__(self) -> None:
        self.cur = _FakeCursor()

    def cursor(self, **kwargs: object) -> _FakeCursor:
        return self.cur


def test_get_profile_uses_supplied_connection_not_psycopg_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ProfileRepository opened its own connection (PERF-022)")

    monkeypatch.setattr(psycopg, "connect", _boom)
    conn = _FakeConn()
    user_id = uuid4()
    row = ProfileRepository().get_profile(conn, user_id)  # type: ignore[arg-type]
    assert row == {"id": "x"}
    assert len(conn.cur.executed) == 1
    query, params = conn.cur.executed[0]
    assert "FROM profiles WHERE id = %s" in query
    assert params == (str(user_id),)
