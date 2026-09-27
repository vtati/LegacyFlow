"""Application configuration loaded from the environment (pydantic-settings)."""

import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the LegacyFlow API service."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    SUPABASE_URL: str | None = None
    SUPABASE_JWKS_URL: str | None = None
    SUPABASE_JWT_AUDIENCE: str = "authenticated"
    SUPABASE_ANON_KEY: str | None = None
    # Required-but-empty in Slice 1: the variable must be declared (deployments
    # fail fast if it is missing) but no real value is used yet — the API makes
    # no Supabase admin calls in this slice. Never expose it to the browser.
    SUPABASE_SERVICE_ROLE_KEY: str
    DATABASE_URL: str | None = None
    TEST_JWT_ISSUER: str | None = None
    TEST_JWT_SECRET: str | None = None
    API_ENV: str = "development"
    # Deployment environment name. Invitation accept tokens are returned in
    # API responses only when this is not "production" (Slice 2).
    ENVIRONMENT: str = "development"
    # Directory where Slice 4 rendered documents and evidence files are
    # stored server-side (``file_uploads.stored_path`` points into it).
    # Defaults to a ``legacyflow-documents`` directory under the system
    # temp dir when unset.
    DOCUMENT_STORAGE_DIR: str | None = None
    # Directory where Track B4 client file uploads are stored server-side
    # (``file_uploads.stored_path`` points into it). Kept separate from the
    # rendered-document directory above. Defaults to a
    # ``legacyflow-uploads`` directory under the system temp dir when unset.
    STORAGE_DIR: str | None = None
    # HMAC secret used to sign file download tokens (Track B4). The
    # download endpoints fail closed when it is unset — downloads must
    # never be mintable without a configured secret.
    DOWNLOAD_TOKEN_SECRET: str | None = None
    # Sentry DSN for error monitoring (PRD §4.2). Empty disables Sentry;
    # the SDK import is optional (see app.main._init_sentry).
    SENTRY_DSN: str = ""
    # Structured log level (e.g. "INFO", "DEBUG").
    LOG_LEVEL: str = "INFO"
    # Rate limiting: requests/minute per tier (see app.core.rate_limit).
    # RATE_LIMIT_ENABLED is a kill switch for incidents.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    RATE_LIMIT_UPLOAD_AI_PER_MINUTE: int = 30
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 120
    # Idempotency-Key record TTL (see app.core.idempotency).
    IDEMPOTENCY_TTL_HOURS: int = 24

    @property
    def document_storage_dir(self) -> Path:
        """Resolved server-side document storage directory."""
        if self.DOCUMENT_STORAGE_DIR:
            return Path(self.DOCUMENT_STORAGE_DIR)
        return Path(tempfile.gettempdir()) / "legacyflow-documents"

    @property
    def storage_dir(self) -> Path:
        """Resolved server-side client-upload storage directory."""
        if self.STORAGE_DIR:
            return Path(self.STORAGE_DIR)
        return Path(tempfile.gettempdir()) / "legacyflow-uploads"

    @property
    def jwks_url(self) -> str | None:
        """Resolved JWKS endpoint.

        Defaults to ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json`` when
        ``SUPABASE_URL`` is set; ``None`` when neither variable is configured.
        """
        if self.SUPABASE_JWKS_URL:
            return self.SUPABASE_JWKS_URL
        if self.SUPABASE_URL:
            return f"{self.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        return None

    @property
    def jwt_issuer(self) -> str | None:
        """Expected ``iss`` claim for Supabase-issued JWTs."""
        if self.SUPABASE_URL:
            return f"{self.SUPABASE_URL.rstrip('/')}/auth/v1"
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    # pydantic-settings populates required fields (e.g.
    # SUPABASE_SERVICE_ROLE_KEY) from the environment at runtime.
    return Settings()  # type: ignore[call-arg]
