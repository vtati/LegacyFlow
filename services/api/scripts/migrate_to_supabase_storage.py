"""Migrate existing local-disk files to Supabase Storage.

Lists ``file_uploads`` rows, uploads each file's bytes to the appropriate
Supabase bucket, and updates ``stored_path`` to the new ``<bucket>/<key>``
format. Idempotent: rows already in ``<bucket>/<key>`` format are skipped.

Usage:
    STORAGE_BACKEND=supabase SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \\
        python scripts/migrate_to_supabase_storage.py [--dry-run]

Requires: psycopg (in the API venv).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from psycopg.rows import dict_row

from app.domains.files.storage_backend import (
    CLIENT_UPLOADS_BUCKET,
    PLAN_DOCUMENTS_BUCKET,
    get_storage_backend,
    split_stored_path,
)

_EXT_BY_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


def _bucket_for_row(row: dict) -> str:
    """Heuristic: plan-documents for server-generated, else client-uploads."""
    # Server-generated documents have scan_status='clean' with a detail
    # mentioning render; user uploads go through the scan worker.
    detail = str(row.get("scan_detail") or "")
    if "render" in detail.lower() or "demo" in detail.lower():
        return PLAN_DOCUMENTS_BUCKET
    return CLIENT_UPLOADS_BUCKET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List actions without executing.")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 1

    backend = get_storage_backend()
    backend_name = type(backend).__name__
    print(f"Storage backend: {backend_name}")
    if backend_name == "LocalStorageBackend":
        print("Refusing to migrate: STORAGE_BACKEND is 'local'. Set STORAGE_BACKEND=supabase.")
        return 1

    migrated = skipped = missing = failed = 0
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stored_path, mime_type, scan_detail FROM file_uploads"
            )
            rows = cur.fetchall()

        print(f"Found {len(rows)} file_uploads rows.")
        for row in rows:
            upload_id = UUID(str(row["id"]))
            stored_path = str(row["stored_path"])
            bucket, _ = split_stored_path(stored_path)
            if bucket:
                skipped += 1
                continue

            # Legacy local path.
            local_path = Path(stored_path)
            if not local_path.is_file():
                print(f"  MISSING: {upload_id} ({stored_path})")
                missing += 1
                continue

            target_bucket = _bucket_for_row(row)
            suffix = _EXT_BY_MIME.get(str(row.get("mime_type") or ""), "")
            key = f"{upload_id.hex}{suffix}"
            new_path = f"{target_bucket}/{key}"

            if args.dry_run:
                print(f"  WOULD MIGRATE: {upload_id} -> {new_path}")
                migrated += 1
                continue

            try:
                data = local_path.read_bytes()
                backend.upload(
                    target_bucket, key, data, str(row.get("mime_type") or "application/octet-stream")
                )
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  FAILED upload {upload_id}: {exc}")
                failed += 1
                continue

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE file_uploads SET stored_path = %s, updated_at = now() WHERE id = %s",
                    (new_path, str(upload_id)),
                )
            conn.commit()
            print(f"  MIGRATED: {upload_id} -> {new_path}")
            migrated += 1

    print(f"\nDone: {migrated} migrated, {skipped} already migrated, {missing} missing, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
