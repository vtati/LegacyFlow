"""SEC-017: get_user_firm_id must resolve deterministically (oldest first)."""
from __future__ import annotations

from uuid import UUID

from app.core.authz import get_user_firm_id

OLDEST = "11111111-1111-1111-1111-111111111111"
NEWER = "22222222-2222-2222-2222-222222222222"
USER = UUID("33333333-3333-3333-3333-333333333333")


class _Cursor:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sink.append(sql)

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    def cursor(self):
        return _Cursor(self._rows, self.queries)


def test_get_user_firm_id_orders_by_created_at_asc():
    # Rows returned in DB order; the query itself must ORDER BY created_at ASC
    # so the oldest active membership wins deterministically.
    conn = _Conn([{"firm_id": OLDEST}, {"firm_id": NEWER}])
    result = get_user_firm_id(conn, USER)
    assert result == UUID(OLDEST)
    assert conn.queries, "expected a query"
    normalized = " ".join(conn.queries[0].split()).lower()
    # L-5: deterministic secondary key — identical created_at values
    # (same-transaction inserts) must still resolve in a stable order.
    assert "order by created_at asc, id asc" in normalized
