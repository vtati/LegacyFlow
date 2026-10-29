"""Batch D review-Minor regressions: pack-import severity validation
(M-2), ai escalation isolation (M-3), attorney auto-run future callback
(M-4), broker-unavailable detail accuracy (M-6), asset-label log
hygiene (ERR-017 note)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# M-2: import_pack must validate escalation-rule severities, not just
# the manifest — an invalid severity must reject the import with 422
# instead of registering a pack that only fails later at load (501).
# ---------------------------------------------------------------------------


def _write_import_pack(pack_dir: Any, severity: str) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.yaml").write_text(
        "pack:\n"
        "  id: us-zz-import\n"
        "  country_code: US\n"
        "  subdivision_code: ZZ\n"
        "  display_name: Zedland (synthetic)\n"
        "  semantic_version: 1.0.0\n"
        "  status: draft\n"
        "  effective_from: '2026-09-26'\n"
        "capabilities: {}\nrequirements: {}\nsources: []\n",
        encoding="utf-8",
    )
    (pack_dir / "escalation-rules.yaml").write_text(
        "rules:\n"
        "  - id: r1\n"
        "    version: '1'\n"
        "    when: 'has_child == true'\n"
        f"    severity: {severity}\n"
        "    outcome: attorney_review\n",
        encoding="utf-8",
    )


def _stub_pack_registration(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from app.domains.review import service as review_service

    registered: list[Any] = []
    monkeypatch.setattr(
        review_service.repo,
        "find_jurisdiction",
        lambda conn, country, subdivision: {"id": str(uuid4())},
    )
    monkeypatch.setattr(
        review_service.repo,
        "insert_jurisdiction_pack",
        lambda conn, **kwargs: registered.append(kwargs) or {"id": str(uuid4())},
    )
    monkeypatch.setattr(review_service, "record_audit_event", lambda *a, **k: None)
    return registered


def test_import_pack_rejects_invalid_escalation_severity_with_422(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.errors import ValidationAppError
    from app.domains.review import service as review_service

    registered = _stub_pack_registration(monkeypatch)
    pack_dir = tmp_path / "bad-severity-pack"
    _write_import_pack(pack_dir, "urgent")

    with pytest.raises(ValidationAppError) as excinfo:
        review_service._import_pack_dir(
            object(), uuid4(), pack_dir, expected_bundle_hash=None
        )
    assert excinfo.value.status == 422
    assert "urgent" in excinfo.value.detail
    assert registered == []  # the pack must NOT be registered


def test_import_pack_with_valid_escalation_severity_still_imports(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domains.review import service as review_service

    registered = _stub_pack_registration(monkeypatch)
    pack_dir = tmp_path / "good-severity-pack"
    _write_import_pack(pack_dir, "attention")

    result = review_service._import_pack_dir(
        object(), uuid4(), pack_dir, expected_bundle_hash=None
    )
    assert result.pack_id == "us-zz-import"
    assert len(registered) == 1


# ---------------------------------------------------------------------------
# M-3: ai/service._ensure_open_escalation must use the savepoint-isolated
# insert like every other escalation writer, not the plain insert.
# ---------------------------------------------------------------------------


def test_ai_ensure_open_escalation_uses_isolated_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.ai import service as ai_service

    calls: list[str] = []
    monkeypatch.setattr(
        ai_service.escalation_repo, "open_escalation_exists", lambda *a, **k: False
    )
    monkeypatch.setattr(
        ai_service.escalation_repo,
        "insert_escalation",
        lambda *a, **k: calls.append("plain"),
    )
    monkeypatch.setattr(
        ai_service.escalation_repo,
        "insert_escalation_isolated",
        lambda *a, **k: calls.append("isolated"),
    )
    ai_service._ensure_open_escalation(
        object(),
        matter_id=uuid4(),
        rule_id="ai_refusal",
        severity="review",
        outcome="attorney_review",
    )
    assert calls == ["isolated"]


# ---------------------------------------------------------------------------
# M-4: attorney auto-run futures must not be silently discarded — a
# done-callback logs any exception escaping the worker target.
# ---------------------------------------------------------------------------


def test_auto_run_future_callback_logs_escaping_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from app.domains.documents import router as documents_router

    def _raising_worker(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic escape from auto-run worker")

    monkeypatch.setattr(
        documents_router, "_auto_run_attorney_review_worker", _raising_worker
    )
    with caplog.at_level(logging.ERROR, logger="app.domains.documents.router"):
        future = documents_router._auto_run_attorney_review(uuid4(), str(uuid4()))
        with pytest.raises(RuntimeError):
            future.result(timeout=10)
        # The done-callback runs on the completing thread; poll briefly
        # for its log record before asserting.
        import time

        for _ in range(50):
            if any(r.levelno >= logging.ERROR for r in caplog.records):
                break
            time.sleep(0.1)
    assert any(
        r.levelno >= logging.ERROR and r.exc_info for r in caplog.records
    ), "done-callback must logger.exception the escaped exception"


def test_auto_run_done_callback_ignores_success() -> None:
    import concurrent.futures

    from app.domains.documents import router as documents_router

    callback = documents_router._log_auto_run_future_failure
    future: concurrent.futures.Future[None] = concurrent.futures.Future()
    future.set_result(None)
    callback(future)  # must not raise
