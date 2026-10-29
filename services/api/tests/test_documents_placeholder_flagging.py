"""FUNC-021: unfilled placeholders must be flagged BEFORE blanking.

The render path blanks unknown ``{{tokens}}`` to ``___``; the attorney
escalation (``document_unmapped_placeholder:*``) must be created from the
PRE-blank token set, and digit/uppercase/dot-bearing tokens (e.g. the
codicil's ``{{witness1_name}}``) must blank too — never leak verbatim.

Unit tests with an in-memory fake connection (no DB needed).
"""
from __future__ import annotations

from uuid import uuid4

from app.domains.documents import service as documents_service


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self._row = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        if "SELECT 1 FROM escalations" in sql:
            matter_id, rule_id = params
            self._row = (
                (1,)
                if any(
                    e["matter_id"] == matter_id and e["rule_id"] == rule_id
                    for e in self._conn.escalations
                )
                else None
            )
        elif "INSERT INTO escalations" in sql:
            matter_id, rule_id, severity, outcome, _facts = params
            self._conn.escalations.append(
                {
                    "matter_id": matter_id,
                    "rule_id": rule_id,
                    "severity": severity,
                    "outcome": outcome,
                }
            )
            self._row = {"id": str(uuid4())}
        else:  # pragma: no cover - guard against unexpected SQL
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self) -> None:
        self.escalations: list[dict] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)


def _render_and_flag(conn: _FakeConn, template_body: str, context: dict[str, str]):
    """Mirror the production render paths: merge, capture pre-blank tokens,
    then flag from that captured set."""
    body, unfilled = documents_service.render_template_content_with_unfilled(
        template_body, context
    )
    matter = {"id": str(uuid4())}
    documents_service._flag_unfilled_placeholders(
        conn, matter=matter, body=body, template_name="tmpl", unfilled=unfilled
    )
    return body, matter


def test_unknown_token_blanked_and_escalation_created() -> None:
    conn = _FakeConn()
    body, matter = _render_and_flag(conn, "Keep {{unknown_field}}.", {})
    assert body == "Keep ___."
    assert "{{" not in body
    rule_ids = [e["rule_id"] for e in conn.escalations]
    assert rule_ids == ["document_unmapped_placeholder:tmpl:unknown_field"]


def test_digit_bearing_token_blanked_and_escalation_created() -> None:
    conn = _FakeConn()
    body, _matter = _render_and_flag(
        conn,
        "Witness: {{witness1_name}} of {{witness2_address}}.",
        {},
    )
    assert body == "Witness: ___ of ___."
    assert "{{witness1_name}}" not in body
    rule_ids = sorted(e["rule_id"] for e in conn.escalations)
    assert rule_ids == [
        "document_unmapped_placeholder:tmpl:witness1_name",
        "document_unmapped_placeholder:tmpl:witness2_address",
    ]


def test_uppercase_and_dot_tokens_blanked() -> None:
    body, unfilled = documents_service.render_template_content_with_unfilled(
        "A {{Spouse.Name}} B {{FIELD_X}}.", {}
    )
    assert body == "A ___ B ___."
    assert unfilled == ["FIELD_X", "Spouse.Name"]


def test_known_tokens_still_substitute_and_do_not_flag() -> None:
    conn = _FakeConn()
    body, _matter = _render_and_flag(
        conn, "Hello {{household_name}}.", {"household_name": "Smiths"}
    )
    assert body == "Hello Smiths."
    assert conn.escalations == []
