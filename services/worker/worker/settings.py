"""Worker configuration (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Runtime configuration for the LegacyFlow worker."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    REDIS_URL: str | None = None
    API_ENV: str = "development"
    # Database the notification outbox lives in (same Postgres as the API).
    DATABASE_URL: str | None = None
    # Email sandbox: rendered notifications are written here as JSON files,
    # one per outbox row. Real email is never sent from this sandbox.
    NOTIFICATION_SANDBOX_DIR: str = "/tmp/legacyflow-mail"
    # Attempts after which an outbox row is marked 'failed'.
    NOTIFICATION_MAX_ATTEMPTS: int = 5
    # File storage backend: "local" (disk) or "supabase" (Supabase Storage).
    STORAGE_BACKEND: str = "local"
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    STORAGE_DIR: str = "/tmp/legacyflow-uploads"


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Return the cached worker settings singleton."""
    return WorkerSettings()
