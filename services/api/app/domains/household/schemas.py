"""Pydantic schemas for the household domain (Slice 2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PersonKind = Literal["adult", "minor"]
HouseholdRole = Literal["primary_client", "collaborator", "dependent", "trusted_contact"]


class HouseholdCreate(BaseModel):
    """Create a household (``POST /v1/households``)."""

    name: str = Field(min_length=1, max_length=200)


class HouseholdOut(BaseModel):
    """Public household shape.

    ``name`` is an alias of ``display_name`` kept for API consistency.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    display_name: str
    name: str
    status: str | None = None
    created_by_user_id: UUID
    created_at: datetime


class PersonCreate(BaseModel):
    """Create a person in a matter's household."""

    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date | None = None
    email: str | None = Field(default=None, max_length=320)
    kind: PersonKind = Field(description="adult or minor")
    notes: str | None = Field(default=None, max_length=2000)
    relationship_role: str = Field(
        default="dependent",
        description=(
            "Household member role for the auto-created membership link. "
            "Only non-privileged roles may be auto-assigned here (FUNC-029)."
        ),
    )

    @field_validator("relationship_role")
    @classmethod
    def _role_allowed(cls, value: str) -> str:
        normalized = value.strip().lower()
        # FUNC-029: primary_client/collaborator are privileged membership
        # roles — primary_client is a household singleton that member and
        # testator resolution assume unique, and collaborator carries
        # household write access. They are minted by household
        # provisioning / invitation flows, never by person-create's
        # auto-assignment; accepting them here let any household writer
        # mint a second primary_client row and corrupt that resolution.
        allowed = {"dependent", "trusted_contact"}
        if normalized not in allowed:
            raise ValueError(
                "relationship_role for a new person must be one of: "
                f"{', '.join(sorted(allowed))}. primary_client and "
                "collaborator memberships are created through household "
                "provisioning and invitations, not by adding a person."
            )
        return normalized


class PersonPatch(BaseModel):
    """Patch a person. All fields optional."""

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    date_of_birth: date | None = None
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=2000)


class PersonOut(BaseModel):
    """Public person shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    full_name: str
    date_of_birth: date | None = None
    email: str | None = None
    kind: str
    notes: str | None = None
    created_at: datetime


class RelationshipCreate(BaseModel):
    """Create a relationship between two persons in the same household."""

    from_person_id: UUID
    to_person_id: UUID
    relationship_type: str = Field(min_length=1, max_length=50)


class RelationshipOut(BaseModel):
    """Public relationship shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    from_person_id: UUID
    to_person_id: UUID
    relationship_type: str
    created_at: datetime


class HouseholdMemberCreate(BaseModel):
    """Add a member to a matter's household (firm staff+ only)."""

    person_id: UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    relationship_role: HouseholdRole = Field(
        description="primary_client, collaborator, dependent, or trusted_contact"
    )


class HouseholdMemberOut(BaseModel):
    """Public household member shape, with the linked person when set."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    user_id: UUID | None = None
    person: PersonOut | None = None
    relationship_role: str
    access_status: str
    created_at: datetime


class HouseholdDetailResponse(BaseModel):
    """Household detail for a matter: members, persons, relationships."""

    household: HouseholdOut
    members: list[HouseholdMemberOut]
    persons: list[PersonOut]
    relationships: list[RelationshipOut]
