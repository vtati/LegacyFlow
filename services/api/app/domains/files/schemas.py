"""Pydantic models for the files domain (Slice 4, Track B4).

Request-side validation (filename safety, MIME allowlist, size cap, purpose
vocabulary) lives here so violations surface as HTTP 422 before any DB or
filesystem work happens.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Hard cap for a single upload (Track B4 contract).
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# MIME types the scanner knows how to validate via magic bytes
# (worker/worker/scanning.py). The allowlist is deliberately small:
# estate-planning evidence and documents only.
ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/png",
        "image/jpeg",
    }
)

# ``purpose`` is validated metadata (there is no purpose column on
# file_uploads); it is audit-logged with the intent.
ALLOWED_PURPOSES = frozenset({"evidence", "document", "attachment"})


def _validate_filename(value: str) -> str:
    """Reject path traversal, separators, and empty names.

    The stored path is always UUID-generated server-side, so the original
    filename never reaches the filesystem — but a hostile name must still
    be rejected at the boundary rather than persisted.
    """
    if "\x00" in value:
        raise ValueError("Filename must not contain NUL bytes.")
    name = value.strip()
    if not name:
        raise ValueError("Filename must not be empty.")
    if len(name) > 255:
        raise ValueError("Filename must be at most 255 characters.")
    if "/" in name or "\\" in name:
        raise ValueError("Filename must not contain path separators.")
    if name in {".", ".."}:
        raise ValueError("Filename must not be '.' or '..'.")
    return name


class UploadIntentCreate(BaseModel):
    """Request body for POST /matters/{matter_id}/files/intent."""

    filename: str = Field(max_length=255)
    mime_type: str
    size_bytes: int = Field(ge=0, le=MAX_UPLOAD_BYTES)
    purpose: str

    @field_validator("filename")
    @classmethod
    def _filename_safe(cls, value: str) -> str:
        return _validate_filename(value)

    @field_validator("mime_type")
    @classmethod
    def _mime_allowed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_MIME_TYPES:
            raise ValueError(
                "MIME type must be one of: " + ", ".join(sorted(ALLOWED_MIME_TYPES)) + "."
            )
        return normalized

    @field_validator("purpose")
    @classmethod
    def _purpose_allowed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_PURPOSES:
            raise ValueError("Purpose must be one of: " + ", ".join(sorted(ALLOWED_PURPOSES)) + ".")
        return normalized


class UploadIntentResponse(BaseModel):
    """Created intent: the reserved upload id and where bytes will land."""

    upload_id: UUID
    stored_path: str
    status: str


class KeyFact(BaseModel):
    """One AI-extracted fact: label + value (e.g. deed → property address)."""

    label: str
    value: str


class FileUploadOut(BaseModel):
    """Full file_uploads metadata, including the scanner verdict."""

    id: UUID
    firm_id: UUID
    household_id: UUID
    matter_id: UUID | None
    uploader_user_id: UUID
    original_filename: str
    stored_path: str
    mime_type: str | None
    size_bytes: int | None
    scan_status: str
    scan_detail: str | None
    # Vault AI analysis (null/empty until extracted + analyzed).
    extracted_text: str | None = None
    extraction_status: str = "pending"
    document_type: str | None = None
    ai_summary: str | None = None
    key_facts: list[KeyFact] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    analysis_confidence: float | None = None
    analyzed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FileAnalysisOut(BaseModel):
    """Result of POST /files/{upload_id}/analyze."""

    upload_id: UUID
    document_type: str
    summary: str
    key_facts: list[KeyFact]
    suggested_tags: list[str]
    confidence: float
    analyzed_at: datetime


class FileTagsUpdate(BaseModel):
    """Request body for POST /files/{upload_id}/tags."""

    tags: list[str] = Field(min_length=1, max_length=20)

    @field_validator("tags")
    @classmethod
    def _tags_valid(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not tag.strip():
                raise ValueError("Tags must not be blank.")
            if len(tag) > 40:
                raise ValueError("Each tag must be at most 40 characters.")
        return value


class DownloadTokenResponse(BaseModel):
    """Short-lived HMAC download token for GET /files/download."""

    token: str
    expires_at: datetime
