"""Shared contract types for LegacyFlow (Pydantic v2).

These models mirror the core data model from artifact §7.2 so the API,
worker, and (via codegen) TypeScript clients share one vocabulary. They are
plain data shapes — no business logic, no legal content.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FirmStatus = Literal["active", "suspended", "trial"]
FirmRole = Literal["firm_admin", "attorney", "staff", "auditor"]
MembershipStatus = Literal["active", "invited", "disabled"]

JurisdictionType = Literal["country", "us_state", "district", "territory"]
JurisdictionPackStatus = Literal[
    "draft", "internal_testing", "attorney_review", "approved", "retired"
]

HouseholdRelationshipRole = Literal[
    "primary_client", "collaborator", "dependent", "trusted_contact"
]
HouseholdAccessStatus = Literal["active", "invited", "revoked", "none"]

ExecutionGuidance = Literal["enabled", "attorney_pending", "disabled"]


class Profile(BaseModel):
    """Auth user profile (``profiles`` table, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    created_at: datetime


class Firm(BaseModel):
    """Law-firm tenant (``firms`` table, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_name: str
    display_name: str
    status: FirmStatus
    created_at: datetime


class FirmMembership(BaseModel):
    """User's membership in a firm (``firm_memberships`` table, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    user_id: UUID
    role: FirmRole
    status: MembershipStatus
    created_at: datetime


class Jurisdiction(BaseModel):
    """A country / US state / district / territory (``jurisdictions``, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    country_code: str
    subdivision_code: str
    display_name: str
    jurisdiction_type: JurisdictionType
    is_enabled: bool = False
    created_at: datetime


class Capabilities(BaseModel):
    """Pack capability flags (artifact §9.3 ``capabilities`` block).

    ``execution_guidance`` is a tri-state because packs may mark execution
    guidance as ``attorney_pending`` rather than simply on/off.
    """

    intake: bool = False
    education: bool = False
    eligibility_triage: bool = False
    attorney_review: bool = False
    document_assembly: bool = False
    execution_guidance: ExecutionGuidance = "disabled"
    funding_guidance: bool = False


class JurisdictionPack(BaseModel):
    """Versioned jurisdiction content pack (``jurisdiction_packs``, §7.2).

    The ``manifest`` carries the full pack document (pack/governance/
    capabilities/requirements/sources per §9.2-§9.3) as an opaque dict here;
    typed pack-schema validation arrives with the Slice 3 pack importer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    jurisdiction_id: UUID
    version: str
    status: JurisdictionPackStatus
    effective_from: date
    effective_to: date | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    source_bundle_hash: str
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime


class Household(BaseModel):
    """Client household (``households`` table, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    display_name: str
    created_by_user_id: UUID
    created_at: datetime


class HouseholdMember(BaseModel):
    """Household membership (``household_members`` table, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    user_id: UUID | None = None
    relationship_role: HouseholdRelationshipRole
    access_status: HouseholdAccessStatus
    created_at: datetime


class Matter(BaseModel):
    """Estate-planning matter (``matters`` table, §7.2).

    ``status`` stays an opaque string in Slice 1; the server-side state
    machine arrives in Slice 2.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    household_id: UUID
    primary_jurisdiction_id: UUID
    jurisdiction_pack_id: UUID
    plan_type: str
    status: str
    status_version: int = 1
    jurisdiction_confirmation_status: str = "pending"
    assigned_attorney_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class MatterStatusHistory(BaseModel):
    """Audited matter status transition (``matter_status_history``, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    matter_id: UUID
    from_status: str | None = None
    to_status: str
    reason_code: str | None = None
    actor_user_id: UUID | None = None
    created_at: datetime


class AuditEvent(BaseModel):
    """Append-only audit event (``audit_events`` table, §7.2/§11.3).

    Payloads are redacted summaries only — never raw plan content
    (engineering contract rule 9).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID | None = None
    matter_id: UUID | None = None
    actor_user_id: UUID | None = None
    actor_type: str
    action: str
    resource_type: str
    resource_id: UUID | None = None
    correlation_id: UUID | None = None
    before_summary: dict[str, Any] | None = None
    after_summary: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
