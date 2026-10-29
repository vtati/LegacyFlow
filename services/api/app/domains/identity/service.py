"""Application services for the identity domain."""

from uuid import UUID

from app.core import db
from app.core.config import Settings
from app.core.errors import AppError, AuthError, NotFoundError
from app.core.security import Claims
from app.domains.identity.repository import ProfileRepository
from app.domains.identity.schemas import ProfileOut


class ProfileStoreUnavailableError(AppError):
    """Raised when no profile store is configured for this deployment."""

    type = "urn:legacyflow:error:profile_store_unavailable"
    title = "Profile store unavailable"
    status = 501
    detail = (
        "Profile storage is not configured for this deployment "
        "(DATABASE_URL is unset). Wire a Postgres database to enable /v1/me."
    )


class ProfileNotFoundError(NotFoundError):
    """No profile row exists for the authenticated user."""

    type = "urn:legacyflow:error:profile_not_found"
    title = "Profile not found"
    status = 404
    detail = "No profile exists for the authenticated user."


class IdentityService:
    """Orchestrates identity-domain operations for the API layer."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_profile(self, claims: Claims) -> ProfileOut:
        """Return the caller's profile.

        DOCUMENTED BEHAVIOR (Slice 1): the profile store requires
        ``DATABASE_URL``. When it is unset the service raises
        :class:`ProfileStoreUnavailableError` (HTTP 501
        ``profile_store_unavailable``) rather than inventing a synthetic
        profile, so clients can distinguish "not wired" from "no profile".
        """
        database_url = self._settings.DATABASE_URL
        if not database_url:
            raise ProfileStoreUnavailableError()
        try:
            user_id = UUID(claims.sub)
        except ValueError as exc:
            raise AuthError("The token subject is invalid.") from exc
        # PERF-022: read through the core connection pool, not a fresh
        # per-call connection.
        with db.get_connection() as conn:
            row = ProfileRepository().get_profile(conn, user_id)
        if row is None:
            raise ProfileNotFoundError()
        return ProfileOut.model_validate(row)
