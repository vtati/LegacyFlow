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


# ---------------------------------------------------------------------------
# ERR-007: broker enqueue failure (Redis down) -> 501, not raw 500
# ---------------------------------------------------------------------------


def test_dispatch_broker_connection_error_becomes_501(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import authz
    from app.domains.privacy import service as privacy_service

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    monkeypatch.setattr(authz, "get_user_firm_id", lambda conn, uid: uuid4())
    monkeypatch.setattr(authz, "require_firm_role", lambda *a, **k: None)

    def _down(redis_url: str) -> None:
        raise ConnectionError("redis unreachable (synthetic)")

    monkeypatch.setattr(privacy_service, "enqueue_reminder_dispatch", _down)
    with pytest.raises(privacy_service.WorkerBrokerUnavailableError) as excinfo:
        privacy_service.dispatch_due_reminders(object(), actor_sub=str(uuid4()))
    assert excinfo.value.status == 501


def test_enqueue_reminder_dispatch_translates_broker_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatch module itself converts a broker ConnectionError
    into the 501 error (not a raw exception escaping as a 500)."""
    import sys
    import types

    from app.domains.privacy import dispatch

    class _Broker:
        def __init__(self, url: str) -> None:
            pass

        def enqueue(self, message: Any) -> None:
            raise ConnectionError("redis unreachable (synthetic)")

    fake_dramatiq = types.ModuleType("dramatiq")
    fake_dramatiq.Message = lambda **kw: kw  # type: ignore[attr-defined]
    fake_brokers = types.ModuleType("dramatiq.brokers")
    fake_redis = types.ModuleType("dramatiq.brokers.redis")
    fake_redis.RedisBroker = _Broker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dramatiq", fake_dramatiq)
    monkeypatch.setitem(sys.modules, "dramatiq.brokers", fake_brokers)
    monkeypatch.setitem(sys.modules, "dramatiq.brokers.redis", fake_redis)

    with pytest.raises(dispatch.WorkerBrokerUnavailableError) as excinfo:
        dispatch.enqueue_reminder_dispatch("redis://localhost:6399/0")
    assert excinfo.value.status == 501


# ---------------------------------------------------------------------------
# ERR-010: text_extract must not log raw upload filenames
# ---------------------------------------------------------------------------


def test_text_extract_logs_extension_not_filename(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.domains.interview import text_extract

    monkeypatch.setattr(
        text_extract, "_extract_pdf", lambda data: [text_extract.ExtractedPage(1, "")]
    )

    def _no_ocr(data: bytes, count: int) -> Any:
        raise RuntimeError("tesseract binary not found")

    monkeypatch.setattr(text_extract, "_ocr_pdf_pages", _no_ocr)
    with caplog.at_level(logging.INFO, logger="app.domains.interview.text_extract"):
        result = text_extract.extract_text(b"%PDF-fake", "Medical-Records-Jane-Public.pdf")
    assert result.ocr_unavailable is True
    for record in caplog.records:
        assert "Jane-Public" not in record.getMessage()
        assert "Medical-Records" not in record.getMessage()


# ---------------------------------------------------------------------------
# ERR-015: JWKS fetch failure logs at ERROR with a distinct message
# ---------------------------------------------------------------------------


def test_jwks_fetch_failure_logs_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import httpx

    from app.core import security
    from app.core.errors import AuthError

    security._jwks_cache.clear()

    def _down(url: str, timeout: float = 0) -> Any:
        raise httpx.ConnectError("connection refused (synthetic)")

    monkeypatch.setattr(httpx, "get", _down)
    with caplog.at_level(logging.ERROR, logger="app.core.security"):
        with pytest.raises(AuthError):
            security._fetch_jwks("https://example.invalid/auth/v1/.well-known/jwks.json")
    assert any(
        r.levelno >= logging.ERROR and "JWKS fetch failed" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# ERR-023: vault extraction failure must be exception-logged
# ---------------------------------------------------------------------------


def test_complete_upload_logs_extraction_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from types import SimpleNamespace

    from app.domains.files import extraction as file_extraction
    from app.domains.files import repository as files_repo
    from app.domains.files import service as files_service

    upload_id = uuid4()
    row = {
        "id": str(upload_id),
        "firm_id": str(uuid4()),
        "household_id": str(uuid4()),
        "matter_id": None,
        "uploader_user_id": str(uuid4()),
        "original_filename": "doc.pdf",
        "stored_path": "client-uploads/doc.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 4,
        "scan_status": "pending",
        "scan_detail": None,
    }
    monkeypatch.setattr(
        files_service,
        "get_upload_scope",
        lambda conn, uid, aid: SimpleNamespace(upload=row),
    )
    monkeypatch.setattr(files_service, "_require_upload_write", lambda scope: None)
    monkeypatch.setattr(
        files_service,
        "_storage_backend",
        lambda: SimpleNamespace(upload=lambda *a, **k: None),
    )

    def _boom(content: bytes, mime: str) -> Any:
        raise RuntimeError("parser exploded (synthetic)")

    monkeypatch.setattr(file_extraction, "extract_text_with_status", _boom)
    from datetime import UTC, datetime

    updated = dict(
        row,
        extraction_status="failed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        files_repo, "update_extraction", lambda conn, uid, **kw: updated
    )
    monkeypatch.setattr(files_service, "record_audit_event", lambda *a, **k: uuid4())

    with caplog.at_level(logging.ERROR, logger="app.domains.files.service"):
        out = files_service.complete_upload(
            object(), actor_sub=str(uuid4()), upload_id=upload_id, content=b"%PDF"
        )
    assert out.extraction_status == "failed"
    assert any(
        r.levelno >= logging.ERROR and r.exc_info and str(upload_id) in r.getMessage()
        for r in caplog.records
    ), "extraction failure must be exception-logged with the upload id"


# ---------------------------------------------------------------------------
# ERR-019 / ERR-020 / ERR-025: attorney reviewer persistence + visibility
# ---------------------------------------------------------------------------


def _prepared_review() -> Any:
    from datetime import date

    from app.domains.attorney_review.service import _PreparedReview

    matter = {
        "id": str(uuid4()),
        "firm_id": str(uuid4()),
        "household_id": str(uuid4()),
        "jurisdiction_snapshot": {
            "pack_id": "us-va",
            "jurisdiction_code": "US-VA",
            "semantic_version": "2.0.0",
        },
    }
    return _PreparedReview(
        matter=matter,
        actor_id=uuid4(),
        fingerprint="fp-1",
        snapshots=[],
        source={},
        profiles={},
        review_date=date(2026, 10, 29),
    )


def _finding(check: str) -> Any:
    from app.domains.attorney_review.checks import Finding

    return Finding(
        layer="deterministic",
        check=check,
        severity="should_fix",
        document="will",
        field=None,
        excerpt=None,
        explanation=f"Finding {check}.",
        suggested_fix=None,
        package_id=None,
    )


def test_persist_review_skips_poisoned_finding(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ERR-019: one finding whose insert raises a DB error is skipped
    (exception-logged, counted on the run) while siblings persist."""
    import psycopg.errors

    from app.domains.attorney_review import repository as ar_repo
    from app.domains.attorney_review import service as ar_service

    prepared = _prepared_review()
    run_id = uuid4()
    inserted: list[str] = []
    completed_kwargs: dict[str, Any] = {}

    monkeypatch.setattr(
        ar_repo, "insert_run", lambda conn, **kw: {"id": str(run_id)}
    )
    monkeypatch.setattr(ar_repo, "latest_completed_run", lambda conn, **kw: None)

    def flaky_insert(conn: Any, **fields: Any) -> Any:
        if fields["check_name"] == "poison_check":
            raise psycopg.errors.CheckViolation("synthetic finding failure")
        inserted.append(fields["check_name"])
        return {"id": str(uuid4())}

    monkeypatch.setattr(ar_repo, "insert_finding", flaky_insert)

    def fake_complete(conn: Any, rid: UUID, *, model_id: Any, **kw: Any) -> Any:
        completed_kwargs.update(kw)
        from datetime import UTC, datetime

        return {
            "id": str(rid), "status": "completed", "model_id": model_id,
            "prompt_version": "v1", "source_fingerprint": "fp-1",
            "created_at": datetime.now(UTC), "completed_at": datetime.now(UTC),
            "degraded": kw.get("degraded", False),
            "skipped_findings": kw.get("skipped_findings", 0),
        }

    monkeypatch.setattr(ar_repo, "complete_run", fake_complete)

    def fake_rows(conn: Any, rid: UUID) -> list[dict[str, Any]]:
        from datetime import UTC, datetime

        return [
            {
                "id": str(uuid4()), "layer": "deterministic", "check_name": name,
                "severity": "should_fix",
                "location": {"document": "will", "field": None, "excerpt": None},
                "explanation": f"Finding {name}.", "suggested_fix": None,
                "disposition": "open", "acknowledgment_note": None,
                "acknowledged_by_user_id": None, "acknowledged_at": None,
                "created_at": datetime.now(UTC),
            }
            for name in inserted
        ]

    monkeypatch.setattr(ar_repo, "findings_for_run", fake_rows)
    monkeypatch.setattr(ar_service, "record_audit_event", lambda *a, **k: uuid4())

    findings = [_finding("good_one"), _finding("poison_check"), _finding("good_two")]
    with caplog.at_level(logging.ERROR, logger="app.domains.attorney_review.service"):
        resp = ar_service._persist_review(
            _FakeConn(),
            prepared=prepared,
            findings=findings,
            model_id="deterministic_only",
            usage=None,
        )
    assert inserted == ["good_one", "good_two"]
    assert completed_kwargs.get("skipped_findings") == 1
    assert resp.run.skipped_findings == 1
    assert len(resp.findings) == 2
    assert any(r.levelno >= logging.ERROR and r.exc_info for r in caplog.records)


def _stub_openai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()


def test_compute_findings_degraded_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch, test_settings_env: None
) -> None:
    from app.domains.attorney_review import model_review
    from app.domains.attorney_review import service as ar_service
    from app.core.config import get_settings

    _stub_openai_settings(monkeypatch)
    try:
        monkeypatch.setattr(
            ar_service, "run_deterministic_checks", lambda **kw: []
        )

        def _down(**kw: Any) -> Any:
            raise RuntimeError("provider down (synthetic)")

        monkeypatch.setattr(model_review, "review_via_model", _down)
        findings, model_id, usage, degraded = ar_service._compute_findings(
            _prepared_review(), full=True
        )
        assert degraded is True
        assert model_id == "deterministic_only"
    finally:
        get_settings.cache_clear()


def test_compute_findings_unparseable_marks_degraded(
    monkeypatch: pytest.MonkeyPatch, test_settings_env: None
) -> None:
    from app.domains.attorney_review import model_review
    from app.domains.attorney_review import service as ar_service
    from app.core.config import get_settings

    _stub_openai_settings(monkeypatch)
    try:
        monkeypatch.setattr(
            ar_service, "run_deterministic_checks", lambda **kw: []
        )
        monkeypatch.setattr(
            model_review,
            "review_via_model",
            lambda **kw: (
                [],
                {"prompt_tokens": 1, model_review.UNPARSEABLE_MARKER: True},
            ),
        )
        findings, model_id, usage, degraded = ar_service._compute_findings(
            _prepared_review(), full=True
        )
        assert degraded is True
        assert usage is not None and model_review.UNPARSEABLE_MARKER not in usage
    finally:
        get_settings.cache_clear()


def test_compute_findings_clean_full_run_not_degraded(
    monkeypatch: pytest.MonkeyPatch, test_settings_env: None
) -> None:
    from app.domains.attorney_review import model_review
    from app.domains.attorney_review import service as ar_service
    from app.core.config import get_settings

    _stub_openai_settings(monkeypatch)
    try:
        monkeypatch.setattr(
            ar_service, "run_deterministic_checks", lambda **kw: []
        )
        monkeypatch.setattr(
            model_review,
            "review_via_model",
            lambda **kw: ([], {"prompt_tokens": 1, "completion_tokens": 1}),
        )
        findings, model_id, usage, degraded = ar_service._compute_findings(
            _prepared_review(), full=True
        )
        assert degraded is False
        assert model_id != "deterministic_only"
    finally:
        get_settings.cache_clear()


def test_model_review_unparseable_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import json as _json

    from app.domains.attorney_review import model_review

    class _Stub:
        def complete_json(self, **kw: Any) -> Any:
            return "not json at all", {"prompt_tokens": 3, "completion_tokens": 2}

    monkeypatch.setattr(
        model_review, "get_json_provider", lambda model=None: _Stub()
    )
    snapshots = [{
        "package_id": "pkg", "template_type": "will", "body_text": "body",
        "context": {},
    }]
    with caplog.at_level(logging.WARNING, logger="app.domains.attorney_review.model_review"):
        findings, usage = model_review.review_via_model(
            snapshots=snapshots, source={}, model="gpt-x"
        )
    assert findings == []
    assert usage.get(model_review.UNPARSEABLE_MARKER) is True
    assert any(
        r.levelno >= logging.WARNING and "unparseable" in r.getMessage().lower()
        for r in caplog.records
    )


def test_run_out_degraded_flag_from_model_id_row() -> None:
    """ERR-020 schema: RunOut carries degraded + skipped_findings."""
    from datetime import UTC, datetime

    from app.domains.attorney_review import service as ar_service

    row = {
        "id": str(uuid4()), "status": "completed",
        "model_id": "deterministic_only", "prompt_version": "v1",
        "source_fingerprint": "fp", "created_at": datetime.now(UTC),
        "completed_at": datetime.now(UTC), "degraded": True,
        "skipped_findings": 2,
    }
    out = ar_service._run_out(row)
    assert out.degraded is True
    assert out.skipped_findings == 2


def test_get_findings_includes_last_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ERR-025: with no completed run, the latest FAILED run (status +
    timestamp, no raw error) is visible instead of 'never attempted'."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.domains.attorney_review import repository as ar_repo
    from app.domains.attorney_review import service as ar_service
    from app.domains.feature_flags import service as feature_flags
    from app.domains.matters import service as matters_service

    matter_id = uuid4()
    matter = {
        "id": str(matter_id), "firm_id": str(uuid4()),
        "household_id": str(uuid4()),
        "jurisdiction_snapshot": {
            "pack_id": "us-va", "jurisdiction_code": "US-VA",
            "semantic_version": "2.0.0",
        },
    }
    monkeypatch.setattr(
        matters_service,
        "require_matter_read_scope",
        lambda conn, mid, aid: SimpleNamespace(matter=matter),
    )
    monkeypatch.setattr(feature_flags, "is_enabled", lambda *a, **k: True)
    monkeypatch.setattr(ar_service, "_current_fingerprint", lambda conn, m: "fp-1")
    monkeypatch.setattr(ar_repo, "latest_completed_run", lambda conn, **kw: None)
    failed_row = {
        "id": str(uuid4()), "status": "failed", "model_id": None,
        "prompt_version": "v1", "source_fingerprint": "fp-1",
        "created_at": datetime.now(UTC), "completed_at": datetime.now(UTC),
        "error": "raw provider stack detail that must not surface",
        "degraded": False, "skipped_findings": 0,
    }
    monkeypatch.setattr(ar_repo, "latest_failed_run", lambda conn, **kw: failed_row)

    resp = ar_service.get_findings(
        object(), actor_sub=str(uuid4()), matter_id=matter_id
    )
    assert resp.run is None
    assert resp.last_failed_run is not None
    assert resp.last_failed_run.status == "failed"
    assert not hasattr(resp.last_failed_run, "error")
