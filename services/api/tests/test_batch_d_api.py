"""Batch D API tests: ERR-016 (pack severity validation + isolated
escalation inserts), ERR-017 (PII in logs), ERR-006 (audit default=str),
ERR-018 (JSON formatter extras), ERR-007 (broker-down 501), ERR-010 /
ERR-015 / ERR-023 (log hygiene), ERR-019/020/025 (attorney reviewer),
PERF-023/ERR-024 (bounded executor + request-id context).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest


# ---------------------------------------------------------------------------
# ERR-016a: pack escalation-rule severities validated at load
# ---------------------------------------------------------------------------


def _write_pack(root: Any, severity: str) -> None:
    version = root / "us-va" / "v9"
    version.mkdir(parents=True)
    (version / "manifest.yaml").write_text(
        "pack:\n"
        "  id: us-va\n"
        "  country_code: US\n"
        "  subdivision_code: VA\n"
        "  display_name: Virginia\n"
        "  semantic_version: '9.0.0'\n"
        "  status: draft\n"
        "capabilities: {}\nrequirements: {}\nsources: []\n",
        encoding="utf-8",
    )
    (version / "escalation-rules.yaml").write_text(
        "rules:\n"
        "  - id: r1\n"
        "    version: '1'\n"
        "    when: 'has_child == true'\n"
        f"    severity: {severity}\n"
        "    outcome: attorney_review\n",
        encoding="utf-8",
    )


def test_pack_with_invalid_severity_fails_to_load(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.errors import ConfigurationError
    from app.domains.legal_content import pack_loader

    _write_pack(tmp_path, "urgent")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(tmp_path))
    pack_loader.clear_pack_cache()
    try:
        with pytest.raises(ConfigurationError):
            pack_loader.get_pack("US-VA", "9.0.0")
    finally:
        pack_loader.clear_pack_cache()


def test_pack_with_valid_severities_loads(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domains.legal_content import pack_loader

    _write_pack(tmp_path, "attention")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(tmp_path))
    pack_loader.clear_pack_cache()
    try:
        pack = pack_loader.get_pack("US-VA", "9.0.0")
        assert pack.escalation_rules[0].severity == "attention"
    finally:
        pack_loader.clear_pack_cache()


# ---------------------------------------------------------------------------
# ERR-016b: isolated escalation insert helper + evaluate loop degradation
# ---------------------------------------------------------------------------


class _FakeConn:
    """Minimal conn: transaction() is a no-op savepoint context."""

    def __init__(self) -> None:
        self.savepoints = 0

    @contextmanager
    def transaction(self) -> Any:
        self.savepoints += 1
        yield


def test_insert_escalation_isolated_swallows_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.domains.escalation import repository as repo

    def boom(conn: Any, **kwargs: Any) -> Any:
        import psycopg.errors

        raise psycopg.errors.CheckViolation("synthetic check violation")

    monkeypatch.setattr(repo, "insert_escalation", boom)
    conn = _FakeConn()
    with caplog.at_level(logging.ERROR, logger="app.domains.escalation.repository"):
        result = repo.insert_escalation_isolated(
            conn,
            matter_id=uuid4(),
            rule_id="r-bad",
            severity="review",
            outcome="attorney_review",
            facts_snapshot={},
        )
    assert result is None
    assert conn.savepoints == 1
    assert any(r.levelno >= logging.ERROR and r.exc_info for r in caplog.records)


def test_evaluate_loop_degrades_on_one_bad_insert(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A stubbed failing insert for one triggered rule degrades to a
    skipped escalation + log while its sibling persists."""
    from types import SimpleNamespace

    from app.domains.escalation import repository as escalation_repo
    from app.domains.escalation import service as escalation_service
    from app.domains.intake import repository as intake_repo
    from app.domains.legal_content.pack_loader import EscalationRule
    from app.domains.matters import service as matters_service

    matter_id = uuid4()
    actor = uuid4()
    matter = {
        "id": str(matter_id),
        "status": "INTAKE_IN_PROGRESS",
        "firm_id": str(uuid4()),
        "completeness_pct": 0.5,
    }
    scope = SimpleNamespace(matter=matter)
    monkeypatch.setattr(
        matters_service, "require_matter_write_scope", lambda conn, mid, aid: scope
    )
    rules = [
        EscalationRule(id="r-good", version="1", when="true", severity="review",
                       outcome="attorney_review"),
        EscalationRule(id="r-bad", version="1", when="true", severity="review",
                       outcome="attorney_review"),
        EscalationRule(id="r-good2", version="1", when="true", severity="info",
                       outcome="attorney_review"),
    ]
    pack = SimpleNamespace(question_sets={}, escalation_rules=rules)
    monkeypatch.setattr(
        matters_service, "load_matter_pack", lambda conn, mid: (pack, "US-VA", "9.0.0")
    )
    monkeypatch.setattr(intake_repo, "list_answers", lambda conn, mid: [])
    monkeypatch.setattr(escalation_repo, "open_escalation_exists", lambda c, m, r: False)
    monkeypatch.setattr(escalation_service, "record_audit_event", lambda *a, **k: uuid4())

    persisted: list[str] = []
    real_isolated = escalation_repo.insert_escalation_isolated

    def flaky_insert(conn: Any, **kwargs: Any) -> Any:
        if kwargs["rule_id"] == "r-bad":
            import psycopg.errors

            raise psycopg.errors.CheckViolation("synthetic check violation")
        persisted.append(kwargs["rule_id"])
        return {"id": str(uuid4())}

    monkeypatch.setattr(escalation_repo, "insert_escalation", flaky_insert)
    assert real_isolated is not None  # helper exists (unit-tested above)

    with caplog.at_level(logging.ERROR, logger="app.domains.escalation.repository"):
        out = escalation_service.evaluate_intake(
            _FakeConn(), actor_sub=str(actor), matter_id=matter_id
        )
    assert persisted == ["r-good", "r-good2"]
    assert [t.rule_id for t in out.triggered] == ["r-good", "r-bad", "r-good2"]
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# ERR-017: materialization logs must not contain person names
# ---------------------------------------------------------------------------


def test_minor_materialization_log_contains_no_name(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from contextlib import contextmanager

    from app.domains.household import repository as household_repo
    from app.domains.intake import service as intake_service

    class _Cur:
        def execute(self, *_a: Any, **_k: Any) -> None:
            pass

        def fetchone(self) -> None:
            return None

    class _Conn:
        @contextmanager
        def cursor(self) -> Any:
            yield _Cur()

    monkeypatch.setattr(
        household_repo, "list_persons_for_household", lambda c, hid: []
    )
    monkeypatch.setattr(
        household_repo,
        "insert_person",
        lambda c, **kw: {"id": str(uuid4())},
    )
    answers = {
        "has_minor_children": True,
        "minor_children_details": "Aarav Sharma (12), Diya Sharma (9)",
    }
    with caplog.at_level(logging.INFO, logger="app.domains.intake.service"):
        intake_service._sync_minors_from_answers(
            _Conn(), household_id=uuid4(), answers=answers
        )
    assert caplog.records, "expected materialization log lines"
    for record in caplog.records:
        message = record.getMessage()
        assert "Aarav" not in message and "Diya" not in message, message
        assert "Sharma" not in message, message


# ---------------------------------------------------------------------------
# ERR-006: audit json.dumps must tolerate datetime/UUID/Decimal
# ---------------------------------------------------------------------------


def test_record_audit_event_serializes_non_json_scalars() -> None:
    import json as _json
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.core.audit import record_audit_event

    captured: dict[str, Any] = {}

    class _Cur:
        def __enter__(self) -> "_Cur":
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

        def execute(self, sql: str, params: tuple = ()) -> None:
            captured["params"] = params

    class _Conn:
        def cursor(self) -> _Cur:
            return _Cur()

    event_id = record_audit_event(
        _Conn(),
        action="test.action",
        resource_type="matter",
        actor_user_id=uuid4(),
        actor_type="user",
        after_summary={
            "when": datetime(2026, 10, 29, tzinfo=UTC),
            "ref": uuid4(),
            "amount": Decimal("12.50"),
        },
    )
    assert event_id is not None
    json_params = [p for p in captured["params"] if isinstance(p, str) and p.startswith("{")]
    assert json_params, "expected JSON summary params"
    parsed = _json.loads(json_params[0])
    assert parsed["amount"] == "12.50"
    assert "2026-10-29" in parsed["when"]


# ---------------------------------------------------------------------------
# ERR-018: JSONFormatter must merge extra={} fields
# ---------------------------------------------------------------------------


def test_json_formatter_merges_extra_fields() -> None:
    import json as _json

    from app.core.logging import JSONFormatter

    record = logging.LogRecord(
        name="app.main", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="unhandled", args=(), exc_info=None,
    )
    record.path = "/v1/matters"
    record.rule_id = "r1"
    payload = _json.loads(JSONFormatter().format(record))
    assert payload["path"] == "/v1/matters"
    assert payload["rule_id"] == "r1"
    assert payload["level"] == "ERROR"
    assert payload["message"] == "unhandled"
    assert payload["service"] == "legacyflow-api"
