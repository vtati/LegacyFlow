"""Application services for the firms domain.

Slice-1 stub: real signatures, no fake logic. Implementations land in
Slice 2 (auth, firms, memberships, invitations per artifact §15.4 step 3).
"""

from uuid import UUID

from app.domains.firms.schemas import FirmMemberOut, FirmOut


class FirmService:
    """Firm settings, branding, enabled jurisdictions, feature flags."""

    def get_current_firm(self, user_id: UUID) -> FirmOut:
        """Return the firm the caller belongs to.

        Slice 2 will resolve this via ``firm_memberships`` (exactly one
        active firm per user in the MVP) and raise ``NotFoundError`` when the
        caller has no active membership.
        """
        raise NotImplementedError("slice 2")

    def list_members(self, firm_id: UUID) -> list[FirmMemberOut]:
        """List members of a firm.

        Slice 2 will scope this by RLS on ``firm_memberships`` and require the
        caller to hold an active membership in the same firm (staff/attorney
        role or above for the full roster).
        """
        raise NotImplementedError("slice 2")
