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
