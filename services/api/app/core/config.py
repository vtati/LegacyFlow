"""Application configuration loaded from the environment (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the LegacyFlow API service."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    SUPABASE_URL: str | None = None
    SUPABASE_JWKS_URL: str | None = None
    SUPABASE_JWT_AUDIENCE: str = "authenticated"
    DATABASE_URL: str | None = None
    TEST_JWT_ISSUER: str | None = None
    TEST_JWT_SECRET: str | None = None
    API_ENV: str = "development"

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
    return Settings()
