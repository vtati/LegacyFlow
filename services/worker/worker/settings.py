"""Worker configuration (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Runtime configuration for the LegacyFlow worker."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    REDIS_URL: str | None = None
    API_ENV: str = "development"


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Return the cached worker settings singleton."""
    return WorkerSettings()
