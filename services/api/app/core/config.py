"""Application configuration loaded from the environment (pydantic-settings)."""

import tempfile
from dataclasses import dataclass
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
    # File storage backend: "local" (disk, dev/test default) or "supabase"
    # (Supabase Storage, production). Supabase mode requires SUPABASE_URL and
    # SUPABASE_SERVICE_ROLE_KEY.
    STORAGE_BACKEND: str = "local"
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
    # AI Interviewer (Phase 1). Provider selection is config-driven so models
    # are swappable without code changes: AI_LLM_PROVIDER=openai|deterministic_dev,
    # AI_STT_PROVIDER=whisper|deterministic_dev. Production sets both to the
    # OpenAI-backed adapters; tests keep the deterministic defaults (no network).
    AI_LLM_PROVIDER: str = "deterministic_dev"
    AI_STT_PROVIDER: str = "deterministic_dev"
    OPENAI_API_KEY: str | None = None
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    # Interview v2 model split: the conductor + document extractor need
    # the stronger model for extraction/tool discipline.
    OPENAI_INTERVIEW_MODEL: str = "gpt-4o"
    # Consistency review model. Benchmark 2026-10-26 (F2; 12 seeded
    # contradiction/consistent cases, production prompt, one pass each):
    # gpt-4o-mini caught 6/6 contradictions but false-flagged 1/6
    # consistent cases; gpt-5.5 caught 6/6 with 0/6 false positives.
    # Contradiction-spotting is a judgment task, so the reviewer gets
    # its own setting at the stronger tier instead of silently
    # inheriting the cheap shared chat model.
    OPENAI_CONSISTENCY_MODEL: str = "gpt-5.5"
    # Stronger extractor for the interview_v2 path ONLY (benchmark
    # 2026-10-25: gpt-5.5 beat gpt-4o on the recorded A/B paragraphs —
    # recall .933 vs .908, wrong-source proposals 0 vs 6). Flag-off
    # (v1) turns keep OPENAI_INTERVIEW_MODEL untouched.
    OPENAI_INTERVIEW_MODEL_V2: str = "gpt-5.5"
    OPENAI_WHISPER_MODEL: str = "whisper-1"
    # Fernet key (base64, 32 bytes) for interview turn-body encryption (ADR-002).
    # Unset fails closed: the interviewer refuses to start a session rather
    # than store plaintext. Never expose to browser code.
    INTERVIEW_TURN_ENCRYPTION_KEY: str | None = None
    INTERVIEW_TURN_KEY_VERSION: int = 1
    # Raw turn bodies are purged this many days after the matter reaches a
    # terminal state; proposals + traces are the permanent audit record.
    INTERVIEW_TURN_RETENTION_DAYS: int = 90
    # Cost guard: hard cap on turns per interview session.
    INTERVIEW_MAX_TURNS_PER_SESSION: int = 200
    # Dictation guard: max audio upload size (8 MiB ≈ several minutes).
    DICTATION_MAX_BYTES: int = 8 * 1024 * 1024

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


@dataclass(frozen=True)
class ModelCapabilities:
    """Chat-completions payload contract for one model.

    * ``send_temperature`` — False for model families that reject the
      ``temperature`` parameter outright (a 400, verified live for the
      gpt-5 family on 2026-10-25).
    * ``completion_token_param`` — the output-cap parameter the model
      accepts (``"max_completion_tokens"``), or None to send no cap.
    * ``reasoning_effort_none_with_tools`` — True when function tools are
      only accepted with ``reasoning_effort="none"`` (also a verified
      live 400 otherwise).
    """

    send_temperature: bool
    completion_token_param: str | None
    reasoning_effort_none_with_tools: bool


# The two verified contracts. The legacy shape stays byte-identical for
# the models every non-v2 path still uses; the next-gen shape is the
# gpt-5 contract verified live on 2026-10-25.
_LEGACY_CONTRACT = ModelCapabilities(
    send_temperature=True,
    completion_token_param=None,
    reasoning_effort_none_with_tools=False,
)
_NEXT_GEN_CONTRACT = ModelCapabilities(
    send_temperature=False,
    completion_token_param="max_completion_tokens",
    reasoning_effort_none_with_tools=True,
)

#: Explicit per-model payload contracts (F5, 2026-10-26). This table
#: replaces name-prefix routing: a rename or a new family can no longer
#: silently change request shape — a model is either listed here or it
#: resolves to :data:`DEFAULT_MODEL_CAPABILITIES`.
MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    "gpt-4o": _LEGACY_CONTRACT,
    "gpt-4o-mini": _LEGACY_CONTRACT,
    "gpt-5.5": _NEXT_GEN_CONTRACT,
}

#: Safe default for models not in the table: the modern contract.
#: Omitting ``temperature`` can never produce a 400 (the model uses its
#: own default), ``max_completion_tokens`` is accepted by every current
#: OpenAI chat model, and pinning reasoning to "none" alongside tools
#: matches the only verified tools contract. A future model that needs
#: different handling gets an explicit table entry, not a prefix rule.
DEFAULT_MODEL_CAPABILITIES = _NEXT_GEN_CONTRACT


def model_capabilities(model: str) -> ModelCapabilities:
    """Return the payload contract for ``model`` (name-normalized)."""
    return MODEL_CAPABILITIES.get(model.strip().lower(), DEFAULT_MODEL_CAPABILITIES)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    # pydantic-settings populates required fields (e.g.
    # SUPABASE_SERVICE_ROLE_KEY) from the environment at runtime.
    return Settings()  # type: ignore[call-arg]
