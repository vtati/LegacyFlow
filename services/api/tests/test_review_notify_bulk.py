"""PERF-011: ``notify_recipients`` must bulk-insert outbox rows.

The pre-fix implementation issued one INSERT per recipient (N round
trips on gate/clarification paths). The bulk implementation must issue
exactly one multi-row INSERT while preserving the observable semantics:
dedupe by user id (else email), first occurrence wins, insertion order
preserved, and the return value is the number of rows enqueued.
"""

from __future__ import annotations

from uuid import uuid4

from app.domains.review import service


class _FakeCursor:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: object = None) -> None:
        self._calls.append((query, params))


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.calls)


def test_notify_recipients_issues_one_bulk_insert() -> None:
    conn = _FakeConn()
    firm_id, matter_id = uuid4(), uuid4()
    u1, u2 = uuid4(), uuid4()
    recipients = [
        {"user_id": u1, "email": "one@example.com"},
        {"user_id": u2, "email": "two@example.com"},
        {"user_id": u1, "email": "one@example.com"},  # duplicate — collapsed
        {"user_id": None, "email": "three@example.com"},
    ]
    count = service.notify_recipients(
        conn,
        firm_id=firm_id,
        matter_id=matter_id,
        template_key="gate.decided",
        payload={"gate": "x"},
        recipients=recipients,
    )
    assert count == 3
    inserts = [(q, p) for q, p in conn.calls if "INSERT INTO notification_outbox" in q]
    assert len(inserts) == 1, f"expected exactly one INSERT, got {len(inserts)}"
    query, params = inserts[0]
    # One VALUES group per deduped recipient, in first-occurrence order.
    assert query.count("(%s, %s, %s, %s, %s, %s::jsonb, 'pending', 0)") == 3
    flat = [str(p) for p in params]  # type: ignore[union-attr]
    assert flat.index(str(u1)) < flat.index(str(u2)) < flat.index("three@example.com")


def test_notify_recipients_empty_issues_no_insert() -> None:
    conn = _FakeConn()
    count = service.notify_recipients(
        conn,
        firm_id=uuid4(),
        matter_id=None,
        template_key="gate.decided",
        payload={},
        recipients=[],
    )
    assert count == 0
    assert conn.calls == []
