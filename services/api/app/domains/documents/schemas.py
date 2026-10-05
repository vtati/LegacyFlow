"""Pydantic contracts for the documents domain (Slice 4).

Persistence shapes follow Track A4's migration
(``20260927030000_files_documents_slice4.sql``): template ``content`` is a
jsonb object carrying the render body, package ``status`` is
``draft``/``demo_released``, funding task status is
``pending``/``in_progress``/``completed``, and reminders carry a
``scheduled_for`` timestamp with a nullable ``sent_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

DocumentTemplateType = Literal["will", "trust", "poa", "instruction_letter"]
PackageStatus = Literal["draft", "demo_released", "released"]
FundingTaskStatus = Literal["pending", "in_progress", "completed"]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class DocumentTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    template_type: DocumentTemplateType
    pack_id: str | None = Field(default=None, max_length=128)
    version: str = Field(default="1.0.0", min_length=1, max_length=32)
    content: str = Field(
        min_length=1,
        description="Template body text; {{placeholders}} are filled at render time.",
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class DocumentTemplateOut(BaseModel):
    id: UUID
    firm_id: UUID
    name: str
    template_type: DocumentTemplateType
    pack_id: str | None
    version: str
    content: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


class DocumentPackageCreate(BaseModel):
    template_id: UUID


class DocumentPackageOut(BaseModel):
    id: UUID
    firm_id: UUID
    matter_id: UUID
    template_id: UUID
    status: PackageStatus
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class DemoReleaseOut(BaseModel):
    package: DocumentPackageOut
    versions: list[DocumentVersionOut]


class ReleaseProductionError(BaseModel):
    error: str = Field(
        default="production_release_unavailable",
        description="Stable machine-readable code.",
    )
    message: str


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


class DocumentVersionOut(BaseModel):
    id: UUID
    package_id: UUID
    version: int
    file_format: Literal["docx", "pdf"]
    file_name: str
    content_type: str
    watermark_text: str | None
    is_demo: bool
    download_url: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Signing checklists
# ---------------------------------------------------------------------------


class SigningChecklistItemOut(BaseModel):
    key: str
    label: str
    detail: str | None = None


class SigningChecklistGenerate(BaseModel):
    """Optional request body for checklist generation."""

    document_type: str | None = Field(
        default=None,
        description="Must match the execution profile's document_type when given.",
    )


class AttestationIn(BaseModel):
    statement_key: str = Field(min_length=1, max_length=128)
    attested: bool
    attested_at: datetime


class AttestationOut(BaseModel):
    statement_key: str
    attested: bool
    attested_at: datetime
    attested_by_user_id: UUID


class SigningChecklistOut(BaseModel):
    id: UUID
    firm_id: UUID
    matter_id: UUID
    execution_profile_id: str
    document_type: str
    requirements: dict[str, Any] = Field(default_factory=dict)
    items: list[SigningChecklistItemOut] = Field(default_factory=list)
    attestations: list[AttestationOut] = Field(default_factory=list)
    completed_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Funding tasks
# ---------------------------------------------------------------------------


class FundingEvidenceOut(BaseModel):
    file_name: str
    size_bytes: int
    sha256: str | None = None


class FundingTaskOut(BaseModel):
    id: UUID
    firm_id: UUID
    matter_id: UUID
    asset_id: UUID | None
    task_type: str
    title: str
    status: FundingTaskStatus
    evidence_file_id: UUID | None
    evidence: FundingEvidenceOut | None = None
    evidence_type: str = Field(
        default="review_checklist",
        description="Playbook evidence vocabulary (e.g. deed_copy).",
    )
    attorney_review_recommended: bool = False
    due_date: str | None = None
    created_at: datetime
    updated_at: datetime


class FundingTaskUpdate(BaseModel):
    status: FundingTaskStatus | None = None
    evidence_file_id: UUID | None = None


# ---------------------------------------------------------------------------
# Vault shares
# ---------------------------------------------------------------------------


class VaultShareCreate(BaseModel):
    label: str = Field(default="", max_length=255)
    # Optional: defaults to 30 days when omitted (the DB column is NOT NULL).
    expires_at: datetime | None = None


class VaultShareCreateOut(BaseModel):
    id: UUID
    firm_id: UUID
    matter_id: UUID
    label: str
    expires_at: datetime
    created_at: datetime
    access_token: str = Field(description="Raw token — shown once. Only a SHA-256 hash is stored.")


class VaultShareOut(BaseModel):
    id: UUID
    firm_id: UUID
    matter_id: UUID
    label: str
    expires_at: datetime
    revoked_at: datetime | None
    created_by_user_id: UUID
    created_at: datetime


class VaultAccessIn(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class VaultAccessOut(BaseModel):
    """Redacted matter summary for vault-share bearers (no PII)."""

    matter_id: UUID
    matter_status: str
    matter_title: str
    plan_type: str
    jurisdiction_code: str


# ---------------------------------------------------------------------------
# Instruction letter (nonbinding demo)
# ---------------------------------------------------------------------------


class InstructionLetterIn(BaseModel):
    letter_text: str = Field(min_length=1)


class InstructionLetterOut(BaseModel):
    package: DocumentPackageOut
    versions: list[DocumentVersionOut]
