"""FUNC-009 + ERR-005: execution-profile resolution must honor the
matter's pinned pack version, fail closed for non-will plan types when
the pack is missing, and LOG every fallback trigger.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.domains.documents import service as documents_service
from app.domains.legal_content import pack_loader

MANIFEST = """pack:
  id: us-va
  country_code: US
  subdivision_code: VA
  display_name: Virginia
  semantic_version: {version}
  status: approved
capabilities:
  document_assembly: true
"""

PROFILES = """execution_profiles:
  - id: {profile_id}
    document_type: will
    requirements:
      witnesses:
        required_count: 2
    checklist_template:
      - key: sign
        label: Sign the document
"""

PLAYBOOKS = """playbooks:
  - id: bank-tod-podb
    asset_types: [bank]
    title: Review TOD designation
    priority: high
    evidence_type: beneficiary_confirmation
    attorney_review_recommended: false
"""


def _write_pack(root: Path, dirname: str, version: str, profile_id: str) -> None:
    pack_dir = root / "us-va" / dirname
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(MANIFEST.format(version=version))
    (pack_dir / "execution-profiles.yaml").write_text(
        PROFILES.format(profile_id=profile_id)
    )
    (pack_dir / "funding-playbooks.yaml").write_text(PLAYBOOKS)


@pytest.fixture()
def packs_dir(tmp_path, monkeypatch):
    root = tmp_path / "content-packs"
    _write_pack(root, "v0", "0.1.0", "VA-WILL-V0")
    _write_pack(root, "v2", "2.0.0", "VA-WILL-V2")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    pack_loader.clear_pack_cache()
    yield root
    pack_loader.clear_pack_cache()


def _matter(version: str, plan_type: str = "will") -> dict:
    return {
        "plan_type": plan_type,
        "jurisdiction_snapshot": {
            "pack_id": "us-va",
            "jurisdiction_code": "US-VA",
            "semantic_version": version,
        },
    }


def test_pinned_v0_profile_wins_even_when_v2_exists(packs_dir):
    profile = documents_service._load_execution_profile(None, _matter("0.1.0"))
    assert profile.profile_id == "VA-WILL-V0"
    assert profile.funding_playbooks[0]["playbook_id"] == "bank-tod-podb"


def test_pinned_v2_profile_resolves(packs_dir):
    profile = documents_service._load_execution_profile(None, _matter("2.0.0"))
    assert profile.profile_id == "VA-WILL-V2"


def test_missing_pack_non_will_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "empty-packs"
    root.mkdir()
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    pack_loader.clear_pack_cache()
    try:
        with pytest.raises(AppError) as excinfo:
            documents_service._load_execution_profile(None, _matter("0.1.0", "trust"))
        assert "trust" in str(excinfo.value.detail)
    finally:
        pack_loader.clear_pack_cache()


def test_missing_pack_will_uses_starter_fallback(tmp_path, monkeypatch):
    root = tmp_path / "empty-packs"
    root.mkdir()
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    pack_loader.clear_pack_cache()
    try:
        profile = documents_service._load_execution_profile(None, _matter("0.1.0", "will"))
        assert profile.profile_id == "VA-WILL-V0"
        assert profile.document_type == "will"
    finally:
        pack_loader.clear_pack_cache()


def test_corrupt_pack_logs_warning_and_falls_back(tmp_path, monkeypatch, caplog):
    root = tmp_path / "content-packs"
    pack_dir = root / "us-va" / "v0"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text("pack: [unclosed\n  bad: : :")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    pack_loader.clear_pack_cache()
    try:
        with caplog.at_level(logging.WARNING):
            profile = documents_service._load_execution_profile(
                None, _matter("0.1.0", "will")
            )
        assert profile.profile_id == "VA-WILL-V0"  # fallback still works
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "fallback trigger must be logged (ERR-005)"
        assert any("us-va" in r.getMessage() or "US-VA" in r.getMessage() for r in warnings)
    finally:
        pack_loader.clear_pack_cache()


def _write_manifest_only_pack(root: Path) -> None:
    """A pack that LOADS but declares no execution profiles (the
    us-dc/us-md/us-nc v0 packs are manifest-only yet declare
    document_assembly: true)."""
    pack_dir = root / "us-va" / "v0"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(MANIFEST.format(version="0.1.0"))


def test_zero_profile_pack_will_uses_starter_fallback(tmp_path, monkeypatch, caplog):
    """Batch C1 I-2: a loaded pack with execution_profiles == [] must
    be treated exactly like a missing pack — not silently resolved to
    an empty {} profile stamped VA-WILL-V0."""
    root = tmp_path / "content-packs"
    _write_manifest_only_pack(root)
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    pack_loader.clear_pack_cache()
    try:
        with caplog.at_level(logging.WARNING):
            profile = documents_service._load_execution_profile(
                None, _matter("0.1.0", "will")
            )
        assert profile.profile_id == "VA-WILL-V0"
        assert profile.document_type == "will"
        # The starter fallback carries the real starter checklist, not
        # the empty checklist an {} profile would produce.
        assert profile.checklist, "starter fallback checklist must not be empty"
        assert any(
            r.levelno >= logging.WARNING for r in caplog.records
        ), "zero-profile fallback must be logged"
    finally:
        pack_loader.clear_pack_cache()


def test_zero_profile_pack_non_will_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "content-packs"
    _write_manifest_only_pack(root)
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    pack_loader.clear_pack_cache()
    try:
        with pytest.raises(AppError) as excinfo:
            documents_service._load_execution_profile(None, _matter("0.1.0", "trust"))
        assert "trust" in str(excinfo.value.detail)
    finally:
        pack_loader.clear_pack_cache()


def test_content_pack_files_missing_pack_root_logs(tmp_path, monkeypatch, caplog):
    """Batch C1 M-6(a): pack root missing/not a directory must warn."""
    root = tmp_path / "content-packs"
    root.mkdir()
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    with caplog.at_level(logging.WARNING):
        assert documents_service._content_pack_files("us-va") is None
    assert any("us-va" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def test_content_pack_files_unmatched_version_logs_and_uses_newest(
    packs_dir, caplog
):
    """Batch C1 M-6(c): requested version with no manifest match falls
    back to the newest directory — loudly, naming requested vs used."""
    with caplog.at_level(logging.WARNING):
        loaded = documents_service._content_pack_files("us-va", "9.9.9")
    assert loaded is not None
    profiles_doc, _playbooks = loaded
    assert profiles_doc["execution_profiles"][0]["id"] == "VA-WILL-V2"
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("9.9.9" in m and "v2" in m for m in messages), messages


def test_content_pack_files_yaml_import_error_logs(tmp_path, monkeypatch, caplog):
    """Batch C1 M-6(b): a yaml ImportError must warn, not silently
    return None."""
    import sys

    root = tmp_path / "content-packs"
    _write_pack(root, "v0", "0.1.0", "VA-WILL-V0")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    monkeypatch.setitem(sys.modules, "yaml", None)
    with caplog.at_level(logging.WARNING):
        assert documents_service._content_pack_files("us-va") is None
    assert any(
        "yaml" in r.getMessage().lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


def test_content_pack_files_numeric_version_sort(tmp_path, monkeypatch):
    """Directory scanning (frozen-artifact path) must order v2 < v10."""
    root = tmp_path / "content-packs"
    for dirname, version, profile_id in (
        ("v2", "2.0.0", "P-V2"),
        ("v10", "10.0.0", "P-V10"),
    ):
        pack_dir = root / "us-va" / dirname
        pack_dir.mkdir(parents=True)
        (pack_dir / "manifest.yaml").write_text(MANIFEST.format(version=version))
        (pack_dir / "execution-profiles.yaml").write_text(
            "execution_profiles:\n  - id: %s\n    document_type: will\n" % profile_id
        )
        (pack_dir / "funding-playbooks.yaml").write_text("playbooks: []\n")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(root))
    loaded = documents_service._content_pack_files("us-va")
    assert loaded is not None
    profiles_doc, _playbooks = loaded
    assert profiles_doc["execution_profiles"][0]["id"] == "P-V10"
