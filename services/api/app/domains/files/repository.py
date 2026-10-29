"""Persistence access for the files domain (psycopg, Slice 4, Track B4).

Repository functions take an open connection; services own transactions.
Table DDL is owned by Track A4
(``supabase/migrations/20260927030000_files_documents_slice4.sql``);
this layer follows it exactly:

* ``file_uploads``: id, firm_id, household_id, matter_id (nullable),
  uploader_user_id, original_filename, stored_path, mime_type (nullable),
  size_bytes (nullable), scan_status ('pending' | 'clean' | 'quarantined'),
  scan_detail (nullable), created_at, updated_at
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID


def insert_file_upload(
    conn: Any,
    *,
    upload_id: UUID,
    firm_id: UUID,
    household_id: UUID,
    matter_id: UUID | None,
    uploader_user_id: UUID,
    original_filename: str,
    stored_path: str,
    mime_type: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Insert a file_uploads row with ``scan_status='pending'``; return it.

    ``upload_id`` is caller-generated so the service can derive the
    UUID-based storage path before the insert.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO file_uploads
              (id, firm_id, household_id, matter_id, uploader_user_id,
               original_filename, stored_path, mime_type, size_bytes,
               scan_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            RETURNING *
            """,
            (
                str(upload_id),
                str(firm_id),
                str(household_id),
                str(matter_id) if matter_id else None,
                str(uploader_user_id),
                original_filename,
                stored_path,
                mime_type,
                size_bytes,
            ),
        )
        return dict(cur.fetchone())


def get_file_upload(conn: Any, upload_id: UUID) -> dict[str, Any] | None:
    """Return a file_uploads row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM file_uploads WHERE id = %s", (str(upload_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_file_uploads_for_matter(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's uploads, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM file_uploads WHERE matter_id = %s ORDER BY created_at DESC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def update_scan_status(
    conn: Any, upload_id: UUID, *, scan_status: str, scan_detail: str | None
) -> dict[str, Any]:
    """Record a scanner verdict; return the updated row.

    ``scan_status`` must satisfy the table check constraint
    ('pending' | 'clean' | 'quarantined'). The worker is the only writer
    of non-'pending' values; the API keeps rows 'pending' until scanned.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE file_uploads
            SET scan_status = %s, scan_detail = %s, updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (scan_status, scan_detail, str(upload_id)),
        )
        return dict(cur.fetchone())


def update_extraction(
    conn: Any,
    upload_id: UUID,
    *,
    extracted_text: str | None,
    extraction_status: str,
    sha256: str | None = None,
) -> dict[str, Any]:
    """Store extracted text and its status; return the updated row.

    ``sha256`` (PERF-003): when given, also persist the hex digest of
    the uploaded bytes — computed by the caller from the bytes already
    in hand at completion, so list paths never download to re-derive it.
    """
    with conn.cursor() as cur:
        if sha256 is not None:
            cur.execute(
                """
                UPDATE file_uploads
                SET extracted_text = %s, extraction_status = %s, sha256 = %s,
                    updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (extracted_text, extraction_status, sha256, str(upload_id)),
            )
        else:
            cur.execute(
                """
                UPDATE file_uploads
                SET extracted_text = %s, extraction_status = %s, updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (extracted_text, extraction_status, str(upload_id)),
            )
        return dict(cur.fetchone())


def update_analysis(
    conn: Any,
    upload_id: UUID,
    *,
    document_type: str,
    ai_summary: str,
    key_facts: list[dict[str, str]],
    suggested_tags: list[str],
    confidence: float,
    analysis_source: str,
) -> dict[str, Any]:
    """Persist AI analysis; merge suggested tags into the tag list.

    Provenance (F3): ``analysis_source`` ('model' | 'rules') records
    which path produced this analysis, and the suggested tags are also
    stored verbatim in ``ai_suggested_tags`` so a merged tag's origin
    (analysis vs user-added) stays recoverable.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT tags FROM file_uploads WHERE id = %s", (str(upload_id),))
        row = cur.fetchone()
        existing = list(row["tags"]) if row and row["tags"] else []
        merged = list(existing)
        for tag in suggested_tags:
            if tag not in merged:
                merged.append(tag)
        cur.execute(
            """
            UPDATE file_uploads
            SET document_type = %s, ai_summary = %s, key_facts = %s,
                tags = %s, analysis_confidence = %s, analyzed_at = now(),
                analysis_source = %s, ai_suggested_tags = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (
                document_type,
                ai_summary,
                json.dumps(key_facts),
                json.dumps(merged),
                confidence,
                analysis_source,
                json.dumps(suggested_tags),
                str(upload_id),
            ),
        )
        return dict(cur.fetchone())


def add_tags(conn: Any, upload_id: UUID, *, tags: list[str]) -> dict[str, Any]:
    """Add user tags (deduplicated, normalized); return the updated row."""
    normalized: list[str] = []
    for tag in tags:
        t = re.sub(r"[^a-z0-9\-]", "", str(tag).strip().lower().replace(" ", "-"))[:40]
        if t and t not in normalized:
            normalized.append(t)
    with conn.cursor() as cur:
        cur.execute("SELECT tags FROM file_uploads WHERE id = %s", (str(upload_id),))
        row = cur.fetchone()
        existing = list(row["tags"]) if row and row["tags"] else []
        merged = list(existing)
        for tag in normalized:
            if tag not in merged:
                merged.append(tag)
        cur.execute(
            """
            UPDATE file_uploads
            SET tags = %s, updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (json.dumps(merged), str(upload_id)),
        )
        return dict(cur.fetchone())
