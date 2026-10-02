"""Storage backend abstraction for LegacyFlow file storage.

Two backends:
- ``LocalStorageBackend``: files on local disk (dev/test default).
- ``SupabaseStorageBackend``: Supabase Storage via REST API (production).

The ``stored_path`` column in ``file_uploads`` holds a storage key in the
form ``<bucket>/<key>`` (e.g. ``client-uploads/abc123.pdf``). For backward
compatibility with rows written before the migration, a bare local path
(without a ``/`` separating a known bucket) is treated as a local-disk path
by ``LocalStorageBackend``.

Selection: ``STORAGE_BACKEND`` env var (``local`` | ``supabase``), defaults
to ``local`` so existing dev/test setups keep working.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.config import get_settings

# Buckets used by LegacyFlow.
CLIENT_UPLOADS_BUCKET = "client-uploads"
PLAN_DOCUMENTS_BUCKET = "plan-documents"
_KNOWN_BUCKETS = frozenset({CLIENT_UPLOADS_BUCKET, PLAN_DOCUMENTS_BUCKET})


def split_stored_path(stored_path: str) -> tuple[str, str]:
    """Split a ``<bucket>/<key>`` stored path into (bucket, key).

    Legacy local-disk paths (no known bucket prefix) return ``("", path)``
    so the local backend can handle them as before.
    """
    if "/" in stored_path:
        bucket, _, key = stored_path.partition("/")
        if bucket in _KNOWN_BUCKETS and key:
            return bucket, key
    return "", stored_path


class StorageBackend(ABC):
    """Abstract file storage."""

    @abstractmethod
    def upload(self, bucket: str, key: str, data: bytes, mime_type: str) -> str:
        """Store bytes; return the stored path (``<bucket>/<key>``)."""

    @abstractmethod
    def download(self, stored_path: str) -> bytes:
        """Return the bytes for a stored path. Raises FileNotFoundError."""

    @abstractmethod
    def delete(self, stored_path: str) -> None:
        """Delete a stored path; missing files are ignored."""

    @abstractmethod
    def exists(self, stored_path: str) -> bool:
        """Return True when the stored path exists."""

    def signed_url(self, stored_path: str, expires_in: int = 900) -> str | None:
        """Return a time-limited download URL, or None if unsupported."""
        return None


class LocalStorageBackend(StorageBackend):
    """Local-disk storage (dev/test)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, stored_path: str) -> Path:
        bucket, key = split_stored_path(stored_path)
        if bucket:
            # New-style: <bucket>/<key> under the root.
            candidate = (self._root / bucket / key).resolve()
        else:
            # Legacy: absolute or root-relative local path.
            p = Path(stored_path)
            candidate = p.resolve() if p.is_absolute() else (self._root / p).resolve()
        root = self._root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("Stored path escapes the storage directory.")
        return candidate

    def upload(self, bucket: str, key: str, data: bytes, mime_type: str) -> str:
        path = self._resolve(f"{bucket}/{key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{bucket}/{key}"

    def download(self, stored_path: str) -> bytes:
        path = self._resolve(stored_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {stored_path}")
        return path.read_bytes()

    def delete(self, stored_path: str) -> None:
        try:
            self._resolve(stored_path).unlink(missing_ok=True)
        except ValueError:
            pass

    def exists(self, stored_path: str) -> bool:
        try:
            return self._resolve(stored_path).is_file()
        except ValueError:
            return False


class SupabaseStorageBackend(StorageBackend):
    """Supabase Storage via REST API (production).

    Uses the service_role key (bypasses RLS). Buckets must exist and be
    private — access control is enforced by the API before signed URLs
    are minted.
    """

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        if not supabase_url or not service_role_key:
            raise ValueError(
                "SupabaseStorageBackend requires SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY."
            )
        self._base = supabase_url.rstrip("/")
        self._key = service_role_key

    def _request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"{self._base}/storage/v1{path}",
            data=data,
            method=method,
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def upload(self, bucket: str, key: str, data: bytes, mime_type: str) -> str:
        status, body = self._request(
            "POST",
            f"/object/{bucket}/{key}",
            data=data,
            headers={"Content-Type": mime_type, "x-upsert": "true"},
        )
        if status not in (200, 201):
            raise IOError(f"Supabase Storage upload failed ({status}): {body[:200]!r}")
        return f"{bucket}/{key}"

    def download(self, stored_path: str) -> bytes:
        bucket, key = split_stored_path(stored_path)
        if not bucket:
            raise FileNotFoundError(f"Not a Supabase stored path: {stored_path}")
        status, body = self._request("GET", f"/object/{bucket}/{key}")
        if status == 404:
            raise FileNotFoundError(f"File not found: {stored_path}")
        if status != 200:
            raise IOError(f"Supabase Storage download failed ({status})")
        return body

    def delete(self, stored_path: str) -> None:
        bucket, key = split_stored_path(stored_path)
        if not bucket:
            return
        self._request(
            "DELETE",
            f"/object/{bucket}",
            data=json.dumps({"prefixes": [key]}).encode(),
            headers={"Content-Type": "application/json"},
        )

    def exists(self, stored_path: str) -> bool:
        bucket, key = split_stored_path(stored_path)
        if not bucket:
            return False
        # Use a signed URL with 1s expiry as an existence probe (HEAD not supported).
        status, _ = self._request(
            "POST",
            f"/object/sign/{bucket}/{key}",
            data=json.dumps({"expiresIn": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        return status == 200

    def signed_url(self, stored_path: str, expires_in: int = 900) -> str | None:
        bucket, key = split_stored_path(stored_path)
        if not bucket:
            return None
        status, body = self._request(
            "POST",
            f"/object/sign/{bucket}/{key}",
            data=json.dumps({"expiresIn": expires_in}).encode(),
            headers={"Content-Type": "application/json"},
        )
        if status != 200:
            return None
        try:
            signed_path = json.loads(body).get("signedURL")
        except (ValueError, AttributeError):
            return None
        if not signed_path:
            return None
        return f"{self._base}{signed_path}"


def get_storage_backend(settings: Any | None = None) -> StorageBackend:
    """Return the configured storage backend.

    ``STORAGE_BACKEND=supabase`` selects Supabase Storage (requires
    ``SUPABASE_URL`` and ``SUPABASE_SERVICE_ROLE_KEY``). Anything else
    (including unset) selects local disk.
    """
    cfg = settings or get_settings()
    backend_name = str(getattr(cfg, "STORAGE_BACKEND", "local") or "local").lower()
    if backend_name == "supabase":
        return SupabaseStorageBackend(
            supabase_url=str(cfg.SUPABASE_URL or ""),
            service_role_key=str(cfg.SUPABASE_SERVICE_ROLE_KEY or ""),
        )
    return LocalStorageBackend(root=cfg.storage_dir)
