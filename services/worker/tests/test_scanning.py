"""Tests for the file scanner (Slice 4, Track B4).

Covers ``worker.scanning`` (Scanner protocol / DevScanner magic-byte
validation, ``perform_file_scan`` verdict persistence) and the ``scan_file``
actor registration. Runs against a local pgserver instance with a minimal
``file_uploads`` table — the real Track A4 migration is exercised by the
API-side suite (``services/api/tests/test_slice4_files.py``).
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

FILE_UPLOADS_DDL = """
CREATE TABLE IF NOT EXISTS file_uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id uuid NOT NULL,
  household_id uuid NOT NULL,
  matter_id uuid,
  uploader_user_id uuid NOT NULL,
  original_filename text NOT NULL,
  stored_path text NOT NULL,
  mime_type text,
  size_bytes bigint,
  scan_status text NOT NULL DEFAULT 'pending'
    CHECK (scan_status IN ('pending', 'clean', 'quarantined')),
  scan_detail text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
"""

PDF_BYTES = b"%PDF-1.4\n%synthetic\n%%EOF"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


@pytest.fixture(scope="module")
def uploads_dsn() -> Iterator[str]:
    """Throwaway pgserver instance with the file_uploads table applied."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    tmpdir = tempfile.mkdtemp(prefix="lf-worker-scan-")
    try:
        with pgserver.get_server(tmpdir) as server:  # type: ignore[attr-defined]
            dsn = str(server.get_uri())
            import psycopg

            with psycopg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(FILE_UPLOADS_DDL)
            yield dsn
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _insert_upload(dsn: str, stored_path: str, mime_type: str, content: bytes) -> uuid.UUID:
    import psycopg

    upload_id = uuid.uuid4()
    Path(stored_path).write_bytes(content)
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO file_uploads
                  (id, firm_id, household_id, uploader_user_id,
                   original_filename, stored_path, mime_type, size_bytes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(upload_id),
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                    "synthetic.bin",
                    stored_path,
                    mime_type,
                    len(content),
                ),
            )
    return upload_id


def _fetch_status(dsn: str, upload_id: uuid.UUID) -> tuple[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scan_status, scan_detail FROM file_uploads WHERE id = %s",
                (str(upload_id),),
            )
            row = cur.fetchone()
            assert row is not None
            return str(row["scan_status"]), row["scan_detail"]


def test_scan_actor_registered() -> None:
    from worker import actors

    assert actors.scan_file.actor_name == "scan_file"


def test_dev_scanner_protocol(tmp_path: Path) -> None:
    from worker.scanning import DevScanner, Scanner

    scanner = DevScanner()
    assert isinstance(scanner, Scanner)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(PDF_BYTES)
    result = scanner.scan(pdf, "application/pdf")
    assert result.clean is True

    png = tmp_path / "img.png"
    png.write_bytes(PNG_BYTES)
    assert scanner.scan(png, "image/png").clean is True
    # PDF bytes claimed as PNG -> quarantine
    assert scanner.scan(pdf, "image/png").clean is False


def test_perform_file_scan_clean_and_quarantine(uploads_dsn: str, tmp_path: Path) -> None:
    import psycopg
    from psycopg.rows import dict_row

    from worker.scanning import perform_file_scan

    clean_id = _insert_upload(
        uploads_dsn, str(tmp_path / "clean.pdf"), "application/pdf", PDF_BYTES
    )
    bad_id = _insert_upload(uploads_dsn, str(tmp_path / "bad.png"), "image/png", PDF_BYTES)

    with psycopg.connect(uploads_dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            assert perform_file_scan(conn, clean_id) == {"scanned": 1, "status": "clean"}
            assert perform_file_scan(conn, bad_id) == {"scanned": 1, "status": "quarantined"}
            # idempotent: already-decided rows are not rescanned
            assert perform_file_scan(conn, clean_id) == {"scanned": 0, "status": "clean"}

    status, detail = _fetch_status(uploads_dsn, clean_id)
    assert status == "clean"
    status, detail = _fetch_status(uploads_dsn, bad_id)
    assert status == "quarantined"
    assert detail is not None and "magic bytes" in detail


def test_scan_pending_uploads_batch(uploads_dsn: str, tmp_path: Path) -> None:
    from worker.scanning import scan_pending_uploads
    from worker.settings import get_worker_settings

    _insert_upload(uploads_dsn, str(tmp_path / "a.pdf"), "application/pdf", PDF_BYTES)
    _insert_upload(uploads_dsn, str(tmp_path / "b.pdf"), "application/pdf", PDF_BYTES)
    _insert_upload(uploads_dsn, str(tmp_path / "c.png"), "image/png", PDF_BYTES)

    settings = get_worker_settings()
    original = settings.DATABASE_URL
    try:
        settings.DATABASE_URL = uploads_dsn
        ledger = scan_pending_uploads(batch_size=25)
    finally:
        settings.DATABASE_URL = original
    assert ledger == {"scanned": 3, "clean": 2, "quarantined": 1}
