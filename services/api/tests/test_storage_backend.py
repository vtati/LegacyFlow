"""Unit tests for the storage backend abstraction (no network, no DB)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domains.files.storage_backend import (
    CLIENT_UPLOADS_BUCKET,
    LocalStorageBackend,
    SupabaseStorageBackend,
    get_storage_backend,
    split_stored_path,
)


class TestSplitStoredPath:
    def test_bucket_key(self):
        assert split_stored_path("client-uploads/abc123.pdf") == ("client-uploads", "abc123.pdf")

    def test_plan_documents(self):
        assert split_stored_path("plan-documents/x-y.pdf") == ("plan-documents", "x-y.pdf")

    def test_legacy_absolute(self):
        assert split_stored_path("/tmp/legacyflow-uploads/abc.pdf") == ("", "/tmp/legacyflow-uploads/abc.pdf")

    def test_unknown_bucket(self):
        # Unknown prefix is treated as a legacy path, not a bucket.
        assert split_stored_path("other/file.pdf") == ("", "other/file.pdf")


class TestLocalStorageBackend:
    @pytest.fixture
    def backend(self, tmp_path: Path) -> LocalStorageBackend:
        return LocalStorageBackend(root=tmp_path / "store")

    def test_roundtrip(self, backend: LocalStorageBackend):
        stored = backend.upload(CLIENT_UPLOADS_BUCKET, "abc.pdf", b"%PDF-1.4", "application/pdf")
        assert stored == "client-uploads/abc.pdf"
        assert backend.exists(stored)
        assert backend.download(stored) == b"%PDF-1.4"

    def test_missing_raises(self, backend: LocalStorageBackend):
        with pytest.raises(FileNotFoundError):
            backend.download("client-uploads/nope.pdf")
        assert not backend.exists("client-uploads/nope.pdf")

    def test_delete_missing_ok(self, backend: LocalStorageBackend):
        backend.delete("client-uploads/nope.pdf")  # no error

    def test_legacy_path(self, backend: LocalStorageBackend, tmp_path: Path):
        # Legacy absolute paths still resolve under the root guard.
        legacy = tmp_path / "store" / "legacy.pdf"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(b"data")
        assert backend.download(str(legacy)) == b"data"

    def test_path_escape_blocked(self, backend: LocalStorageBackend):
        with pytest.raises((ValueError, FileNotFoundError)):
            backend.download("client-uploads/../../etc/passwd")

    def test_signed_url_none(self, backend: LocalStorageBackend):
        assert backend.signed_url("client-uploads/abc.pdf") is None


class TestSupabaseBackendConfig:
    def test_missing_keys_raise(self):
        with pytest.raises(ValueError, match="SUPABASE_URL"):
            SupabaseStorageBackend(supabase_url="", service_role_key="")

    def test_split_non_bucket_path(self):
        backend = SupabaseStorageBackend(
            supabase_url="https://x.supabase.co", service_role_key="k"
        )
        with pytest.raises(FileNotFoundError):
            backend.download("/tmp/local.pdf")


class TestGetStorageBackend:
    def test_defaults_to_local(self, tmp_path: Path):
        cfg = SimpleNamespace(STORAGE_BACKEND=None, storage_dir=tmp_path)
        assert isinstance(get_storage_backend(cfg), LocalStorageBackend)

    def test_explicit_local(self, tmp_path: Path):
        cfg = SimpleNamespace(STORAGE_BACKEND="local", storage_dir=tmp_path)
        assert isinstance(get_storage_backend(cfg), LocalStorageBackend)

    def test_supabase_missing_keys(self):
        cfg = SimpleNamespace(
            STORAGE_BACKEND="supabase", SUPABASE_URL=None, SUPABASE_SERVICE_ROLE_KEY=None
        )
        with pytest.raises(ValueError):
            get_storage_backend(cfg)

    def test_supabase_ok(self):
        cfg = SimpleNamespace(
            STORAGE_BACKEND="supabase",
            SUPABASE_URL="https://x.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="key",
        )
        backend = get_storage_backend(cfg)
        assert isinstance(backend, SupabaseStorageBackend)


class TestStoredPathFormat:
    def test_new_paths_use_bucket_prefix(self):
        # Mirrors files/service._new_stored_path format.
        uid = uuid4()
        stored = f"{CLIENT_UPLOADS_BUCKET}/{uid.hex}.pdf"
        bucket, key = split_stored_path(stored)
        assert bucket == CLIENT_UPLOADS_BUCKET
        assert key == f"{uid.hex}.pdf"
