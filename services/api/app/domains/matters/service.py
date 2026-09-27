"""Application services for the matters domain.

Slice-1 stub: real signatures, no fake logic. The state machine,
assignment, status history, and outbox events land in Slice 2
(artifact §15.5).
"""

from uuid import UUID

from app.domains.matters.schemas import MatterOut


class MatterService:
    """Matter lifecycle: creation, transitions, progress, assignment."""

    def get_matter(self, matter_id: UUID, requesting_user_id: UUID) -> MatterOut:
        """Return a matter by id.

        AUTHORIZATION PLACEHOLDER (Slice 2): this must verify that
        ``requesting_user_id`` holds an active membership in the matter's
        firm before returning anything, raising ``AuthorizationError``
        (HTTP 403) on cross-firm access. Postgres RLS on ``matters`` is the
        backstop, but the service must enforce the check explicitly as well
        (artifact §15.1: all firm/client data must be tenant-isolated).
        """
        raise NotImplementedError("slice 2")
