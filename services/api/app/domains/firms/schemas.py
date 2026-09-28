"""Pydantic schemas for the firms domain (artifact §7.2)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

FirmStatus = Literal["active", "suspended", "trial"]
FirmRole = Literal["firm_admin", "attorney", "staff", "auditor"]
MembershipStatus = Literal["active", "invited", "disabled"]


class FirmOut(BaseModel):
    """Public firm shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_name: str
    display_name: str
    status: FirmStatus
    created_at: datetime


class FirmMemberOut(BaseModel):
    """Public firm-membership shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID
    user_id: UUID
    role: FirmRole
    status: MembershipStatus
    created_at: datetime


class FirmMemberDetailOut(FirmMemberOut):
    """Firm-membership shape with the member's profile details."""

    display_name: str | None = None
    email: str | None = None
