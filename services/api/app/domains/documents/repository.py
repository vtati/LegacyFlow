"""Persistence access for the documents domain (psycopg, Slice 4).

Repository functions take an open connection; services own transactions.
Table DDL is owned by Track A4 (``supabase/migrations/
20260927030000_files_documents_slice4.sql``); this layer follows it
exactly:

* ``file_uploads``: id, firm_id, household_id, matter_id, uploader_user_id,
  original_filename, stored_path, mime_type, size_bytes, scan_status,
  scan_detail, created_at, updated_at
* ``document_templates``: id, firm_id, name, template_type, pack_id,
  version (text), content (jsonb ``{"body": ...}``), created_by_user_id,
  created_at, updated_at
* ``document_packages``: id, firm_id, matter_id, template_id (not null),
  status ('draft' | 'demo_released'), created_by_user_id, created_at,
  updated_at
* ``document_versions``: id, package_id, version (integer), file_upload_id,
  watermark_text, created_by_user_id, created_at; unique (package_id,
  version). Immutable once created.
* ``signing_checklists``: id, firm_id, matter_id, checklist (jsonb:
  execution profile snapshot + items + attestations), completed_at,
  completed_by_user_id, created_at, updated_at
* ``funding_tasks``: id, firm_id, matter_id, asset_id, task_type (the
  playbook id), title, status ('pending' | 'in_progress' | 'completed'),
  evidence_file_id, due_date, created_at, updated_at
* ``vault_shares``: id, firm_id, matter_id, token_hash (unique), label,
  expires_at (not null), revoked_at, created_by_user_id, created_at
* ``reminders``: id, firm_id, matter_id (nullable), user_id, reminder_type,
  scheduled_for, sent_at, payload (jsonb), created_at
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

_JSONB_COLUMNS = {"content", "checklist", "payload"}


def _insert(
    conn: Any, table: str, fields: dict[str, Any], *, conflict: str | None = None
) -> dict[str, Any] | None:
    """Insert a row; with ``conflict`` use ``ON CONFLICT ... DO NOTHING``.

    Returns the inserted row, or ``None`` when the insert was skipped by
    the conflict guard.
    """
    columns = list(fields.keys())
    placeholders = ", ".join("%s::jsonb" if key in _JSONB_COLUMNS else "%s" for key in columns)
    params = [
        json.dumps(fields[key], default=str) if key in _JSONB_COLUMNS else fields[key]
        for key in columns
    ]
    clause = f" ON CONFLICT {conflict} DO NOTHING" if conflict else ""
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            f"{clause} RETURNING *",
            params,
        )
        row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# file_uploads
# ---------------------------------------------------------------------------


def insert_file_upload(
    conn: Any,
    *,
    firm_id: UUID,
    household_id: UUID,
    matter_id: UUID | None,
    uploader_user_id: UUID,
    original_filename: str,
    stored_path: str,
    mime_type: str,
    size_bytes: int,
    scan_status: str = "clean",
    scan_detail: str | None = None,
) -> dict[str, Any]:
    """Insert a file_uploads row; return it."""
    row = _insert(
        conn,
        "file_uploads",
        {
            "firm_id": str(firm_id),
            "household_id": str(household_id),
            "matter_id": str(matter_id) if matter_id else None,
            "uploader_user_id": str(uploader_user_id),
            "original_filename": original_filename,
            "stored_path": stored_path,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "scan_status": scan_status,
            "scan_detail": scan_detail,
        },
    )
    assert row is not None
    return row


def get_file_upload(conn: Any, upload_id: UUID) -> dict[str, Any] | None:
    """Return a file_uploads row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM file_uploads WHERE id = %s", (str(upload_id),))
        row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Document templates
# ---------------------------------------------------------------------------


def insert_template(
    conn: Any,
    *,
    firm_id: UUID,
    name: str,
    template_type: str,
    pack_id: str | None,
    version: str,
    content: dict[str, Any],
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Insert a document template; return the row."""
    row = _insert(
        conn,
        "document_templates",
        {
            "firm_id": str(firm_id),
            "name": name,
            "template_type": template_type,
            "pack_id": pack_id,
            "version": version,
            "content": content,
            "created_by_user_id": str(created_by_user_id),
        },
    )
    assert row is not None
    return row


def list_templates_for_firm(conn: Any, firm_id: UUID) -> list[dict[str, Any]]:
    """List a firm's templates, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_templates WHERE firm_id = %s ORDER BY created_at DESC",
            (str(firm_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_template(conn: Any, template_id: UUID) -> dict[str, Any] | None:
    """Return a template row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM document_templates WHERE id = %s", (str(template_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def count_packages_for_template(conn: Any, template_id: UUID) -> int:
    """Return the number of document packages referencing a template."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM document_packages WHERE template_id = %s",
            (str(template_id),),
        )
        row = cur.fetchone()
    return int(row["n"]) if row else 0


def delete_template(conn: Any, template_id: UUID) -> None:
    """Delete a document template row."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM document_templates WHERE id = %s", (str(template_id),))


def latest_template_by_type(conn: Any, firm_id: UUID, template_type: str) -> dict[str, Any] | None:
    """Return the firm's most recently created template of a type."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_templates"
            " WHERE firm_id = %s AND template_type = %s"
            " ORDER BY created_at DESC LIMIT 1",
            (str(firm_id), template_type),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Document packages
# ---------------------------------------------------------------------------


def insert_package(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    template_id: UUID,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Insert a document package (starts in ``draft``); return the row."""
    row = _insert(
        conn,
        "document_packages",
        {
            "firm_id": str(firm_id),
            "matter_id": str(matter_id),
            "template_id": str(template_id),
            "created_by_user_id": str(created_by_user_id),
        },
    )
    assert row is not None
    return row


def get_package(conn: Any, package_id: UUID) -> dict[str, Any] | None:
    """Return a package row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM document_packages WHERE id = %s", (str(package_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_packages_for_matter(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's packages, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_packages WHERE matter_id = %s ORDER BY created_at DESC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def find_package_for_template(
    conn: Any, matter_id: UUID, template_id: UUID
) -> dict[str, Any] | None:
    """Return the newest package binding a matter to a template, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_packages WHERE matter_id = %s AND template_id = %s"
            " ORDER BY created_at DESC LIMIT 1",
            (str(matter_id), str(template_id)),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_package_status(conn: Any, package_id: UUID, status: str) -> dict[str, Any]:
    """Set a package's status; return the updated row."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE document_packages SET status = %s, updated_at = now()"
            " WHERE id = %s RETURNING *",
            (status, str(package_id)),
        )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


# ---------------------------------------------------------------------------
# Document versions
# ---------------------------------------------------------------------------


def next_version_number(conn: Any, package_id: UUID) -> int:
    """Return the next version number for a package (1-based)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_version"
            " FROM document_versions WHERE package_id = %s",
            (str(package_id),),
        )
        row = cur.fetchone()
    return int(row["max_version"]) + 1 if row else 1


def insert_version(
    conn: Any,
    *,
    package_id: UUID,
    version: int,
    file_upload_id: UUID,
    watermark_text: str | None,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Append an immutable document version row; return it."""
    row = _insert(
        conn,
        "document_versions",
        {
            "package_id": str(package_id),
            "version": version,
            "file_upload_id": str(file_upload_id),
            "watermark_text": watermark_text,
            "created_by_user_id": str(created_by_user_id),
        },
    )
    assert row is not None
    return row


def get_version(conn: Any, version_id: UUID) -> dict[str, Any] | None:
    """Return a version row with its upload + package scope, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.*, p.matter_id AS package_matter_id, p.firm_id AS package_firm_id,
                   u.original_filename, u.stored_path, u.mime_type, u.size_bytes,
                   u.household_id AS upload_household_id
            FROM document_versions v
            JOIN document_packages p ON p.id = v.package_id
            JOIN file_uploads u ON u.id = v.file_upload_id
            WHERE v.id = %s
            """,
            (str(version_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def list_versions_for_package(conn: Any, package_id: UUID) -> list[dict[str, Any]]:
    """List a package's versions with upload metadata, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.*, u.original_filename, u.stored_path, u.mime_type, u.size_bytes
            FROM document_versions v
            JOIN file_uploads u ON u.id = v.file_upload_id
            WHERE v.package_id = %s
            ORDER BY v.version DESC
            """,
            (str(package_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def list_versions_for_matter(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List all document versions for a matter's packages, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.*, u.original_filename, u.stored_path, u.mime_type, u.size_bytes
            FROM document_versions v
            JOIN file_uploads u ON u.id = v.file_upload_id
            JOIN document_packages p ON p.id = v.package_id
            WHERE p.matter_id = %s
            ORDER BY v.created_at DESC
            """,
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Signing checklists
# ---------------------------------------------------------------------------


def insert_checklist(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    checklist: dict[str, Any],
) -> dict[str, Any]:
    """Insert a signing checklist; return the row."""
    row = _insert(
        conn,
        "signing_checklists",
        {
            "firm_id": str(firm_id),
            "matter_id": str(matter_id),
            "checklist": checklist,
        },
    )
    assert row is not None
    return row


def get_checklist(conn: Any, checklist_id: UUID) -> dict[str, Any] | None:
    """Return a checklist row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM signing_checklists WHERE id = %s", (str(checklist_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def latest_checklist_for_matter(conn: Any, matter_id: UUID) -> dict[str, Any] | None:
    """Return the newest signing checklist for a matter, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM signing_checklists WHERE matter_id = %s"
            " ORDER BY created_at DESC LIMIT 1",
            (str(matter_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_checklist(conn: Any, checklist_id: UUID, checklist: dict[str, Any]) -> dict[str, Any]:
    """Replace a checklist's jsonb payload (e.g. recording attestations)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signing_checklists SET checklist = %s::jsonb, updated_at = now()"
            " WHERE id = %s RETURNING *",
            (json.dumps(checklist, default=str), str(checklist_id)),
        )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


# ---------------------------------------------------------------------------
# Funding tasks
# ---------------------------------------------------------------------------


def insert_funding_task(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    asset_id: UUID | None,
    task_type: str,
    title: str,
) -> dict[str, Any]:
    """Insert a funding task; return the row."""
    row = _insert(
        conn,
        "funding_tasks",
        {
            "firm_id": str(firm_id),
            "matter_id": str(matter_id),
            "asset_id": str(asset_id) if asset_id else None,
            "task_type": task_type,
            "title": title,
        },
    )
    assert row is not None
    return row


def funding_task_exists(conn: Any, matter_id: UUID, asset_id: UUID | None, task_type: str) -> bool:
    """True when an identical (matter, asset, task_type) task already exists."""
    with conn.cursor() as cur:
        if asset_id is None:
            cur.execute(
                "SELECT 1 FROM funding_tasks"
                " WHERE matter_id = %s AND asset_id IS NULL AND task_type = %s LIMIT 1",
                (str(matter_id), task_type),
            )
        else:
            cur.execute(
                "SELECT 1 FROM funding_tasks"
                " WHERE matter_id = %s AND asset_id = %s AND task_type = %s LIMIT 1",
                (str(matter_id), str(asset_id), task_type),
            )
        return cur.fetchone() is not None


def list_funding_tasks_for_matter(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's funding tasks, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM funding_tasks WHERE matter_id = %s ORDER BY created_at ASC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_funding_task(conn: Any, task_id: UUID) -> dict[str, Any] | None:
    """Return a funding task row by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM funding_tasks WHERE id = %s", (str(task_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def update_funding_task(
    conn: Any,
    task_id: UUID,
    *,
    status: str | None,
    evidence_file_id: UUID | None,
) -> dict[str, Any]:
    """Patch a funding task's status and/or evidence file; return the row."""
    assignments = ["updated_at = now()"]
    params: list[Any] = []
    if status is not None:
        assignments.append("status = %s")
        params.append(status)
    if evidence_file_id is not None:
        assignments.append("evidence_file_id = %s")
        params.append(str(evidence_file_id))
    params.append(str(task_id))
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE funding_tasks SET {', '.join(assignments)} WHERE id = %s RETURNING *",
            params,
        )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


def list_assets_for_matter(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List assets usable for funding-task generation for a matter.

    Assets are household-scoped; a matter's funding tasks draw from every
    asset of the matter's household (assets explicitly bound to another
    matter are excluded).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.* FROM assets a
            JOIN matters m ON m.household_id = a.household_id
            WHERE m.id = %s AND (a.matter_id IS NULL OR a.matter_id = m.id)
            ORDER BY a.created_at ASC
            """,
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Vault shares
# ---------------------------------------------------------------------------


def insert_vault_share(
    conn: Any,
    *,
    firm_id: UUID,
    matter_id: UUID,
    label: str,
    token_hash: str,
    expires_at: Any,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Insert a vault share (only the token *hash* is stored); return the row."""
    row = _insert(
        conn,
        "vault_shares",
        {
            "firm_id": str(firm_id),
            "matter_id": str(matter_id),
            "label": label,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "created_by_user_id": str(created_by_user_id),
        },
    )
    assert row is not None
    return row


def get_vault_share_by_hash(conn: Any, token_hash: str) -> dict[str, Any] | None:
    """Return a vault share by its token hash, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM vault_shares WHERE token_hash = %s", (token_hash,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_vault_share(conn: Any, share_id: UUID) -> dict[str, Any] | None:
    """Return a vault share by id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM vault_shares WHERE id = %s", (str(share_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def list_vault_shares_for_matter(conn: Any, matter_id: UUID) -> list[dict[str, Any]]:
    """List a matter's vault shares, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM vault_shares WHERE matter_id = %s ORDER BY created_at DESC",
            (str(matter_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def revoke_vault_share(conn: Any, share_id: UUID) -> dict[str, Any] | None:
    """Mark a vault share revoked; return the row (or ``None`` if missing)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE vault_shares SET revoked_at = now()"
            " WHERE id = %s AND revoked_at IS NULL RETURNING *",
            (str(share_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def get_matter_vault_summary(conn: Any, matter_id: UUID) -> dict[str, Any] | None:
    """Return the redacted matter summary shown to vault-share bearers.

    Contains only non-PII plan context: ids, status, title, plan type and
    the jurisdiction code. Never names, emails, or household detail.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT m.id, m.status, m.title, m.plan_type,"
            " j.country_code, j.subdivision_code"
            " FROM matters m"
            " JOIN jurisdictions j ON j.id = m.primary_jurisdiction_id"
            " WHERE m.id = %s",
            (str(matter_id),),
        )
        row = cur.fetchone()
    if row is None:
        return None
    summary = dict(row)
    summary["jurisdiction_code"] = (
        f"{summary.pop('country_code')}-{summary.pop('subdivision_code')}"
    )
    return summary


# ---------------------------------------------------------------------------
# Reminders (worker actor)
# ---------------------------------------------------------------------------


def list_due_reminders(conn: Any, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return unsent reminders whose ``scheduled_for`` has passed (oldest first)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM reminders"
            " WHERE sent_at IS NULL AND scheduled_for <= now()"
            " ORDER BY scheduled_for ASC LIMIT %s"
            " FOR UPDATE SKIP LOCKED",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def mark_reminder_sent(conn: Any, reminder_id: UUID) -> None:
    """Mark a reminder sent (idempotent for the worker actor)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE reminders SET sent_at = now() WHERE id = %s AND sent_at IS NULL",
            (str(reminder_id),),
        )
