"""Field-level encryption for sensitive interview content (ADR-002).

Turn bodies are encrypted with Fernet (AES-128-CBC + HMAC) before they reach
Postgres. The data key comes from ``INTERVIEW_TURN_ENCRYPTION_KEY`` and never
appears in logs, error messages, or the database. Reads and writes fail closed
when the key is unset — the interviewer refuses to operate rather than store
plaintext.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import ConfigurationError


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    raw = (settings.INTERVIEW_TURN_ENCRYPTION_KEY or "").strip()
    if not raw:
        raise ConfigurationError(
            "INTERVIEW_TURN_ENCRYPTION_KEY is not configured. Interview sessions "
            "cannot start without turn-body encryption (ADR-002)."
        )
    try:
        return Fernet(raw.encode())
    except Exception as exc:
        raise ConfigurationError(
            "INTERVIEW_TURN_ENCRYPTION_KEY is not a valid Fernet key "
            "(expected base64-encoded 32 bytes)."
        ) from exc


def encrypt_text(plaintext: str) -> tuple[str, int]:
    """Encrypt ``plaintext``; return ``(ciphertext, key_version)``."""
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return token, get_settings().INTERVIEW_TURN_KEY_VERSION


def decrypt_text(ciphertext: str) -> str:
    """Decrypt a Fernet ``ciphertext`` token; raises on tampering."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ConfigurationError(
            "Interview turn body failed integrity verification."
        ) from exc


def reset_fernet_cache() -> None:
    """Test helper: drop the cached Fernet instance after changing settings."""
    _fernet.cache_clear()
