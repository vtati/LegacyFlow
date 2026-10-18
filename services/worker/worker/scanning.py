"""File content scanner (Slice 4, Track B4).

Validates an upload's magic bytes against its claimed MIME type and records
the verdict on ``file_uploads.scan_status`` (``pending`` -> ``clean`` or
``quarantined``). Quarantine is fail-closed: anything the scanner cannot
positively identify stays out of the download path.

Layout mirrors ``worker/actors.py``: :func:`perform_file_scan` and
:func:`scan_pending_uploads` are plain-function cores (directly testable;
no dramatiq import at module level so the API test-suite can import this
module), and the ``scan_file`` dramatiq actor in ``actors.py`` is the thin
production trigger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import json
import urllib.request
import urllib.error

import psycopg
from psycopg.rows import dict_row

from worker.settings import get_worker_settings

logger = logging.getLogger(__name__)

# Magic-byte prefixes by (lowercased) MIME type. DOCX is an OOXML zip, so
# the local-file-header / empty-archive / spanned-archive signatures all
# count as a match.
MIME_MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    ),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}

_HEADER_READ_BYTES = 16


def _download_stored_header(stored_path: str) -> bytes:
    """Read only the first ``_HEADER_READ_BYTES`` of a stored file.

    The scanner checks magic bytes and nothing else, so it must never pull
    a whole upload into memory (worker-memory audit, 2026-10-17: full-file
    downloads made batch scans spike RAM by the total bytes scanned).
    Supports ``<bucket>/<key>`` Supabase paths (via Storage REST API) and
    legacy local-disk paths. Raises FileNotFoundError when missing.
    """
    settings = get_worker_settings()
    backend = str(getattr(settings, "STORAGE_BACKEND", "local") or "local").lower()

    if "/" in stored_path:
        bucket, _, key = stored_path.partition("/")
        if bucket in ("client-uploads", "plan-documents") and key:
            if backend == "supabase":
                supabase_url = str(getattr(settings, "SUPABASE_URL", "") or "").rstrip("/")
                service_key = str(getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or "")
                if not supabase_url or not service_key:
                    raise FileNotFoundError(f"Supabase storage not configured: {stored_path}")
                req = urllib.request.Request(
                    f"{supabase_url}/storage/v1/object/{bucket}/{key}",
                    headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        # Stream-capped read: at most the header bytes are
                        # pulled off the socket; the rest is never buffered.
                        return resp.read(_HEADER_READ_BYTES)
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        raise FileNotFoundError(f"File not found: {stored_path}")
                    raise
            # Local backend: <bucket>/<key> under the storage root.
            from pathlib import Path as _Path

            root = _Path(str(getattr(settings, "STORAGE_DIR", "/tmp/legacyflow-uploads") or "/tmp/legacyflow-uploads"))
            candidate = (root / bucket / key).resolve()
            if candidate.is_file():
                with candidate.open("rb") as fh:
                    return fh.read(_HEADER_READ_BYTES)
            raise FileNotFoundError(f"File not found: {stored_path}")

    # Legacy absolute local path.
    from pathlib import Path as _Path

    p = _Path(stored_path)
    if p.is_file():
        with p.open("rb") as fh:
            return fh.read(_HEADER_READ_BYTES)
    raise FileNotFoundError(f"File not found: {stored_path}")


@dataclass(frozen=True)
class ScanResult:
    """Verdict for one file: clean or quarantined, with a human-readable reason."""

    clean: bool
    detail: str


@runtime_checkable
class Scanner(Protocol):
    """Content scanner: validate bytes against a claimed MIME type."""

    def scan(self, path: Path, mime_type: str) -> ScanResult:
        """Return the verdict for the file at ``path``."""
        ...


class DevScanner:
    """Magic-byte scanner (development implementation of :class:`Scanner`).

    Reads the file header and checks it against the known prefix for the
    claimed MIME type. Unknown MIME types, unreadable files, and empty
    files all fail closed (quarantine).
    """

    def __init__(self, magic: dict[str, tuple[bytes, ...]] | None = None) -> None:
        self._magic = magic if magic is not None else MIME_MAGIC

    def scan(self, path: Path | bytes, mime_type: str) -> ScanResult:
        normalized = mime_type.strip().lower()
        expected = self._magic.get(normalized)
        if expected is None:
            return ScanResult(clean=False, detail=f"unsupported MIME type: {mime_type!r}")
        try:
            header = (path if isinstance(path, bytes) else path.read_bytes())[:_HEADER_READ_BYTES]
        except OSError as exc:
            return ScanResult(clean=False, detail=f"could not read file: {exc}")
        if not header:
            return ScanResult(clean=False, detail="file is empty")
        if any(header.startswith(prefix) for prefix in expected):
            return ScanResult(clean=True, detail="magic bytes match the claimed MIME type")
        return ScanResult(
            clean=False, detail=f"magic bytes do not match the claimed MIME type {mime_type!r}"
        )


def perform_file_scan(
    conn: Any, upload_id: UUID, *, scanner: Scanner | None = None
) -> dict[str, Any]:
    """Scan one upload and persist the verdict. Plain-function core.

    Takes an open connection (the caller owns the transaction). Idempotent:
    rows that are not ``pending`` are left untouched. Returns a small
    ``{"scanned": n, "status": ...}`` ledger.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM file_uploads WHERE id = %s", (str(upload_id),))
        row = cur.fetchone()
    if row is None:
        return {"scanned": 0, "status": "missing"}
    status = str(row["scan_status"])
    if status != "pending":
        return {"scanned": 0, "status": status}

    try:
        file_bytes = _download_stored_header(str(row["stored_path"]))
    except FileNotFoundError:
        return {"scanned": 0, "status": "missing"}
    result = (scanner or DevScanner()).scan(file_bytes, str(row["mime_type"] or ""))
    new_status = "clean" if result.clean else "quarantined"
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE file_uploads
            SET scan_status = %s, scan_detail = %s, updated_at = now()
            WHERE id = %s
            """,
            (new_status, result.detail, str(upload_id)),
        )
    logger.info("scan_file %s -> %s (%s)", upload_id, new_status, result.detail)
    return {"scanned": 1, "status": new_status}


def scan_pending_uploads(batch_size: int = 25, *, scanner: Scanner | None = None) -> dict[str, int]:
    """Claim pending uploads (``FOR UPDATE SKIP LOCKED``) and scan them.

    The periodic counterpart to the ``scan_file`` actor: deployments
    schedule this to drain the scan queue. Returns a small
    ``{"scanned": n, "clean": c, "quarantined": q}`` ledger.
    """
    settings = get_worker_settings()
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL is not set; scan_pending_uploads has nothing to scan.")
        return {"scanned": 0, "clean": 0, "quarantined": 0}

    active = scanner or DevScanner()
    scanned = 0
    clean = 0
    quarantined = 0
    with psycopg.connect(settings.DATABASE_URL, row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM file_uploads
                    WHERE scan_status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (batch_size,),
                )
                upload_ids = [UUID(str(row["id"])) for row in cur.fetchall()]

            for upload_id in upload_ids:
                try:
                    outcome = perform_file_scan(conn, upload_id, scanner=active)
                except Exception:  # a failing row must not kill the batch
                    logger.exception("scan_pending_uploads failed for upload %s", upload_id)
                    continue
                if outcome["scanned"] == 1:
                    scanned += 1
                    if outcome["status"] == "clean":
                        clean += 1
                    elif outcome["status"] == "quarantined":
                        quarantined += 1
    logger.info(
        "scan_pending_uploads processed batch: scanned=%d clean=%d quarantined=%d",
        scanned,
        clean,
        quarantined,
    )
    return {"scanned": scanned, "clean": clean, "quarantined": quarantined}
