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

# Exactly the document_templates_template_type_check set (migration
# 20261006040000). The Literal lagged the DB at 4 values, which would have
# made _template_out fail validation on advance_directive / guardianship
# rows once they were retyped correctly (2026-10-26 plan, Task 1.1).
DocumentTemplateType = Literal[
    "will",
    "trust",
    "poa",
    "advance_directive",
    "guardianship",
    "codicil",
    "trust_amendment",
    "instruction_letter",
]
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
    # A fresh share is never revoked; the field keeps the create response
    # shape-compatible with VaultShareOut (and the web client's
    # VaultShareCreated, which extends the list item type).
    revoked_at: datetime | None = None
    created_at: datetime
    # Named `token` — the name the web client, the /vault/access payload,
    # and the files download-token response all use for a one-time token.
    # (`access_token` in this codebase means a Supabase JWT.)
    token: str = Field(description="Raw token — shown once. Only a SHA-256 hash is stored.")


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
# Family draft set (self-serve generation, 2026-10-26 plan D3)
# ---------------------------------------------------------------------------


class DraftApplicableOut(BaseModel):
    """Applicable-required completeness (declared/pruned never gate, D8)."""

    required_total: int
    required_answered: int
    complete: bool
    missing_labels: list[str] = Field(default_factory=list)


class DraftDocumentStateOut(BaseModel):
    """One draft-set type's state for the family review page."""

    template_type: str
    included: bool
    signal: str
    template_id: UUID | None = None
    template_name: str | None = None
    package_id: UUID | None = None
    package_status: str | None = None
    generated: bool = False
    stale: bool = False
    latest_version_at: datetime | None = None


class DraftAttorneyOut(BaseModel):
    user_id: UUID
    full_name: str
    email: str


class DraftPacketOut(BaseModel):
    id: UUID
    version: int
    created_at: datetime


class DraftSetStateOut(BaseModel):
    """The review page's single read: enablement, completeness, drafts, submit."""

    enabled: bool
    matter_status: str
    applicable: DraftApplicableOut
    documents: list[DraftDocumentStateOut]
    can_generate: bool
    can_submit: bool
    submit_blockers: list[str] = Field(default_factory=list)
    assigned_attorney: DraftAttorneyOut | None = None
    latest_packet: DraftPacketOut | None = None


class DraftSetGenerateOut(BaseModel):
    packages: list[DocumentPackageOut]
    versions: list[DocumentVersionOut]
    fingerprint: str


class DraftSubmitOut(BaseModel):
    matter_id: UUID
    matter_status: str
    packet_id: UUID
    packet_version: int
    assigned_attorney: DraftAttorneyOut


# ---------------------------------------------------------------------------
# Instruction letter (nonbinding demo)
# ---------------------------------------------------------------------------


class InstructionLetterIn(BaseModel):
    letter_text: str = Field(min_length=1)


class InstructionLetterOut(BaseModel):
    package: DocumentPackageOut
    versions: list[DocumentVersionOut]


# ---------------------------------------------------------------------------
# Amendments (codicils, trust amendments)
# ---------------------------------------------------------------------------


class AmendmentRequestIn(BaseModel):
    """Request changes to an implemented plan.

    The user describes what changed in plain language; the attorney reviews
    the delta and decides whether a codicil, trust amendment, or full
    restatement is needed.
    """

    description: str = Field(min_length=1, max_length=5000)
    affected_template_types: list[str] = Field(default_factory=list)


class AmendmentRequestOut(BaseModel):
    id: UUID
    matter_id: UUID
    description: str
    affected_template_types: list[str]
    status: str  # pending, approved, rejected, completed
    created_at: datetime


class AmendmentDecideIn(BaseModel):
    """Attorney decision on an amendment request."""

    decision: Literal["approved", "rejected", "completed"]
    notes: str | None = Field(default=None, max_length=1000)
