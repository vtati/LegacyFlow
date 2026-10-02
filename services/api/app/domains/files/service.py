"""Application services for the files domain (Slice 4, Track B4).

Upload lifecycle: intent (validated, ``scan_status='pending'``) -> complete
(bytes persisted, still ``pending``) -> worker scan (``clean`` or
``quarantined``) -> token-gated download (``clean`` only).

Scan triggering: the API cannot import the worker package, so — following
the notification-outbox pattern from Slice 3 — "triggering the scan" means
making the work durable and visible: bytes on disk, row ``pending``, audit
event recorded. The worker's ``scan_file`` actor (or the periodic
``scan_pending_uploads`` job) claims pending rows and records the verdict.
There is no in-process scan call from the API.

Authorization mirrors the assets domain: firm staff+ or household members;
cross-tenant access reads as "not found" (existence-oracle rule). Only
``clean`` files may be downloaded.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core import authz
from app.core.audit import record_audit_event
from app.core.config import get_settings
from app.core.errors import (
    AuthError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.domains.files import repository as repo
from app.domains.files import schemas as sch
from app.domains.files.storage_backend import (
    CLIENT_UPLOADS_BUCKET,
    LocalStorageBackend,
    get_storage_backend,
    split_stored_path,
)

# Download tokens live 15 minutes (Track B4 contract).
DOWNLOAD_TOKEN_TTL_SECONDS = 900

# Server-side filename suffix by (normalized) MIME type. The suffix comes
# from the validated MIME — never from the client-supplied filename.
_EXT_BY_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

# Household roles allowed to upload (write). Dependents and trusted
# contacts are read-only — mirrors the assets domain (artifact §3.2).
HOUSEHOLD_WRITE_ROLES = frozenset({"primary_client", "collaborator"})


def caller_id(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


# ---------------------------------------------------------------------------
# Download tokens (HMAC-SHA256, 15-minute TTL)
# ---------------------------------------------------------------------------


def build_download_token(
    upload_id: UUID, *, secret: str, ttl_seconds: int = DOWNLOAD_TOKEN_TTL_SECONDS
) -> tuple[str, datetime]:
    """Mint a ``<upload_id>:<exp_epoch>:<hmac_hex>`` download token."""
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    body = f"{upload_id.hex}:{int(expires_at.timestamp())}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}:{signature}", expires_at


def verify_download_token(token: str, *, secret: str) -> UUID:
    """Return the upload id for a valid, unexpired token.

    Raises AuthError (HTTP 401) for malformed tokens, bad signatures, and
    expired tokens alike — the caller learns nothing about which check
    failed.
    """
    try:
        upload_hex, exp_epoch, signature = token.split(":")
        upload_id = UUID(hex=upload_hex)
        expires_at = datetime.fromtimestamp(int(exp_epoch), tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise AuthError("The download token is invalid.") from exc
    body = f"{upload_hex}:{exp_epoch}"
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AuthError("The download token is invalid.")
    if datetime.now(UTC) > expires_at:
        raise AuthError("The download token has expired.")
    return upload_id


# ---------------------------------------------------------------------------
# Storage paths (backend-agnostic)
#
# ``stored_path`` values are ``<bucket>/<key>`` (e.g.
# ``client-uploads/abc123.pdf``). Rows written before the Supabase migration
# hold legacy local-disk paths; ``LocalStorageBackend`` still resolves those.
# ---------------------------------------------------------------------------


def _storage_backend():
    """Return the configured storage backend."""
    return get_storage_backend()


def resolve_stored_path(stored_path: str) -> Path:
    """Return the absolute local path for a stored file (local backend only).

    Kept for backward compatibility with callers that stream from disk.
    For Supabase-backed paths, use :func:`download_bytes` instead.
    """
    backend = _storage_backend()
    if isinstance(backend, LocalStorageBackend):
        return backend._resolve(stored_path)
    raise ConfigurationError(
        "Direct path resolution is unavailable with the Supabase storage backend."
    )


def download_bytes(stored_path: str) -> bytes:
    """Return the bytes for a stored path via the configured backend."""
    return _storage_backend().download(stored_path)


def stored_file_exists(stored_path: str) -> bool:
    """Return True when the stored path exists in the configured backend."""
    return _storage_backend().exists(stored_path)


def storage_signed_url(stored_path: str, expires_in: int = 900) -> str | None:
    """Return a time-limited download URL, or None when unsupported."""
    return _storage_backend().signed_url(stored_path, expires_in=expires_in)


def _new_stored_path(upload_id: UUID, mime_type: str) -> str:
    suffix = _EXT_BY_MIME[mime_type]
    return f"{CLIENT_UPLOADS_BUCKET}/{upload_id.hex}{suffix}"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadScope:
    """An upload the caller may access, plus how they may access it."""

    upload: dict[str, Any]
    via: str  # "firm" | "household"
    firm_role: str | None
    household_role: str | None


def get_upload_scope(conn: Any, upload_id: UUID, actor_id: UUID) -> UploadScope:
    """Load an upload the caller may access.

    Access is granted when the caller is an active member of the upload's
    firm, or an active member of the upload's household. Raises
    NotFoundError when the row is missing or the caller has no access
    (existence-oracle rule).
    """
    row = repo.get_file_upload(conn, upload_id)
    if row is None:
        raise NotFoundError("File not found.")
    firm_id = UUID(str(row["firm_id"]))
    household_id = UUID(str(row["household_id"]))

    firm_role = authz.get_firm_role(conn, actor_id, firm_id)
    if firm_role is not None:
        return UploadScope(upload=dict(row), via="firm", firm_role=firm_role, household_role=None)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT relationship_role FROM household_members"
            " WHERE household_id = %s AND user_id = %s AND access_status = 'active'",
            (str(household_id), str(actor_id)),
        )
        member = cur.fetchone()
    if member is None:
        raise NotFoundError("File not found.")
    return UploadScope(
        upload=dict(row),
        via="household",
        firm_role=None,
        household_role=str(member["relationship_role"]),
    )


def _require_upload_read(scope: UploadScope) -> None:
    """Firm staff+ or any active household member (auditors excluded)."""
    if scope.via == "firm" and scope.firm_role not in authz.STAFF_ROLES:
        raise AuthorizationError("This action requires a firm staff role or household membership.")


def _require_upload_write(scope: UploadScope) -> None:
    """Firm staff+ or household primary_client/collaborator.

    Callers with no access at all get HTTP 404 from the scope loader
    (existence-oracle rule); callers with read-only access get HTTP 403.
    """
    if scope.via == "firm":
        if scope.firm_role not in authz.STAFF_ROLES:
            raise AuthorizationError("This action requires a firm staff role.")
    elif scope.household_role not in HOUSEHOLD_WRITE_ROLES:
        raise AuthorizationError(
            "This action requires a firm staff role or the"
            " primary_client/collaborator household role."
        )


def _require_matter_write(scope: authz.MatterScope) -> None:
    """Write rule for matter-scoped file routes (mirrors upload write)."""
    if scope.via == "firm":
        if scope.firm_role not in authz.STAFF_ROLES:
            raise AuthorizationError("This action requires a firm staff role.")
    elif scope.household_role not in HOUSEHOLD_WRITE_ROLES:
        raise AuthorizationError(
            "This action requires a firm staff role or the"
            " primary_client/collaborator household role."
        )


def _require_matter_read(scope: authz.MatterScope) -> None:
    """Read rule for matter-scoped file routes (auditors excluded)."""
    if scope.via == "firm" and scope.firm_role not in authz.STAFF_ROLES:
        raise AuthorizationError("This action requires a firm staff role or household membership.")


# ---------------------------------------------------------------------------
# Row -> schema helpers
# ---------------------------------------------------------------------------


def _file_upload_out(row: dict[str, Any]) -> sch.FileUploadOut:
    return sch.FileUploadOut.model_validate(row)


# ---------------------------------------------------------------------------
# Intent / complete / list
# ---------------------------------------------------------------------------


def create_upload_intent(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    payload: sch.UploadIntentCreate,
    correlation_id: UUID | None = None,
) -> sch.UploadIntentResponse:
    """Reserve an upload slot for a matter (firm staff+ or household writer).

    Validates the filename/MIME/size at the boundary, generates a
    UUID-based storage path, and persists the row with
    ``scan_status='pending'``. The client then uploads bytes via
    :func:`complete_upload`.
    """
    actor_id = caller_id(actor_sub)
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    _require_matter_write(scope)

    upload_id = uuid4()
    stored_path = _new_stored_path(upload_id, payload.mime_type)
    row = repo.insert_file_upload(
        conn,
        upload_id=upload_id,
        firm_id=UUID(str(scope.matter["firm_id"])),
        household_id=UUID(str(scope.matter["household_id"])),
        matter_id=matter_id,
        uploader_user_id=actor_id,
        original_filename=payload.filename,
        stored_path=str(stored_path),
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    record_audit_event(
        conn,
        action="files.intent_created",
        resource_type="file_upload",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(row["firm_id"])),
        matter_id=matter_id,
        resource_id=upload_id,
        correlation_id=correlation_id,
        # Summary only: MIME, declared size, purpose — never the filename.
        metadata={
            "mime_type": payload.mime_type,
            "size_bytes": payload.size_bytes,
            "purpose": payload.purpose,
        },
    )
    return sch.UploadIntentResponse(
        upload_id=upload_id, stored_path=str(stored_path), status=str(row["scan_status"])
    )


def complete_upload(
    conn: Any,
    *,
    actor_sub: str,
    upload_id: UUID,
    content: bytes,
    correlation_id: UUID | None = None,
) -> sch.FileUploadOut:
    """Persist the upload bytes and mark the upload ready for scanning.

    The row must still be ``pending`` (re-completing a scanned upload is a
    409); the byte count must match the intent's declared size. The worker
    scan picks up the pending row from here — see the module docstring.
    """
    actor_id = caller_id(actor_sub)
    scope = get_upload_scope(conn, upload_id, actor_id)
    _require_upload_write(scope)

    row = scope.upload
    if str(row["scan_status"]) != "pending":
        raise ConflictError("This upload has already been completed.")
    declared = row["size_bytes"]
    if declared is not None and len(content) != int(declared):
        raise ValidationAppError(
            f"Uploaded {len(content)} bytes but the intent declared {declared}."
        )
    if len(content) > sch.MAX_UPLOAD_BYTES:
        raise ValidationAppError("Uploaded content exceeds the 25 MB limit.")

    stored_path = str(row["stored_path"])
    bucket, key = split_stored_path(stored_path)
    if not bucket:
        # Legacy local-disk path: normalize to client-uploads bucket.
        suffix = _EXT_BY_MIME.get(str(row["mime_type"] or ""), "")
        bucket, key = CLIENT_UPLOADS_BUCKET, f"{upload_id.hex}{suffix}"
    _storage_backend().upload(
        bucket, key, content, str(row["mime_type"] or "application/octet-stream")
    )

    record_audit_event(
        conn,
        action="files.upload_completed",
        resource_type="file_upload",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(row["firm_id"])),
        matter_id=UUID(str(row["matter_id"])) if row.get("matter_id") else None,
        resource_id=upload_id,
        correlation_id=correlation_id,
        metadata={"size_bytes": len(content), "scan_status": "pending"},
    )
    return _file_upload_out(row)


def list_matter_files(conn: Any, *, actor_sub: str, matter_id: UUID) -> list[sch.FileUploadOut]:
    """List a matter's uploads, newest first (firm staff+ or household member)."""
    actor_id = caller_id(actor_sub)
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    _require_matter_read(scope)
    return [_file_upload_out(row) for row in repo.list_file_uploads_for_matter(conn, matter_id)]


# ---------------------------------------------------------------------------
# Download tokens + token-gated download
# ---------------------------------------------------------------------------


def _download_secret() -> str:
    secret = get_settings().DOWNLOAD_TOKEN_SECRET
    if not secret:
        raise ConfigurationError("Download tokens are not configured.")
    return secret


def create_download_token(
    conn: Any,
    *,
    actor_sub: str,
    upload_id: UUID,
    correlation_id: UUID | None = None,
) -> sch.DownloadTokenResponse:
    """Mint a 15-minute HMAC download token (firm staff+ or household member)."""
    actor_id = caller_id(actor_sub)
    scope = get_upload_scope(conn, upload_id, actor_id)
    _require_upload_read(scope)

    token, expires_at = build_download_token(upload_id, secret=_download_secret())
    record_audit_event(
        conn,
        action="files.download_token_created",
        resource_type="file_upload",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.upload["firm_id"])),
        matter_id=UUID(str(scope.upload["matter_id"])) if scope.upload.get("matter_id") else None,
        resource_id=upload_id,
        correlation_id=correlation_id,
        metadata={"expires_at": expires_at.isoformat()},
    )
    return sch.DownloadTokenResponse(token=token, expires_at=expires_at)


def authorize_download(conn: Any, *, token: str) -> dict[str, Any]:
    """Validate a download token and return the upload row.

    Public endpoint helper: no JWT involved. Rejects bad/expired tokens
    (401), unknown uploads (404), and anything not ``clean`` (403) —
    quarantined and unscanned files are never downloadable.
    """
    upload_id = verify_download_token(token, secret=_download_secret())
    row = repo.get_file_upload(conn, upload_id)
    if row is None:
        raise NotFoundError("File not found.")
    if str(row["scan_status"]) != "clean":
        raise AuthorizationError("This file is not available for download.")
    return row
