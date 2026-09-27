"""Pydantic schemas for the invitations domain (Slice 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Role values accepted on invitation create. Client roles join a household;
# firm roles create a firm_membership. Must stay in sync with the
# ``invitations`` role check constraint (Slice 2 migration).
InvitationRole = Literal[
    "client_primary",
    "client_collaborator",
    "firm_admin",
    "firm_attorney",
    "firm_staff",
    "firm_auditor",
]

InvitationStatus = Literal["pending", "accepted", "expired", "revoked"]


class InvitationCreate(BaseModel):
    """Create an invitation (``POST /v1/firms/invitations``)."""

    email: str = Field(min_length=3, max_length=320)
    role: InvitationRole
    household_id: UUID | None = None


class InvitationOut(BaseModel):
    """Public invitation shape. The raw token is never included."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    email: str
    role: str
    household_id: UUID | None = None
    status: str
    expires_at: datetime
    accepted_user_id: UUID | None = None
    created_at: datetime


class InvitationCreateResponse(BaseModel):
    """Invitation create response.

    ``accept_token`` is the raw token, returned ONLY when the deployment
    environment is not ``production`` (Slice 2 bootstrap aid).
    """

    invitation: InvitationOut
    accept_token: str | None = None


class InvitationAcceptRequest(BaseModel):
    """Accept an invitation (``POST /v1/invitations/accept``)."""

    token: str = Field(min_length=16, max_length=256)


class InvitationAcceptResponse(BaseModel):
    """Result of accepting an invitation."""

    invitation: InvitationOut
    membership_type: Literal["firm", "household"]
    membership_id: UUID
