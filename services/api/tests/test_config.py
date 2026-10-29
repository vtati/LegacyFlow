"""Settings configuration tests.

Covers the Slice-1 "required-but-empty" contract for
``SUPABASE_SERVICE_ROLE_KEY``: deployments must fail fast when the variable
is missing, while an explicitly empty value is accepted (the key is not
used yet in Slice 1).
"""

import pytest
from pydantic import ValidationError


def test_service_role_key_missing_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_service_role_key_may_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.SUPABASE_SERVICE_ROLE_KEY == ""
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# SEC-002: TEST_JWT override must fail fast in production
# ---------------------------------------------------------------------------


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("TEST_JWT_ISSUER", raising=False)
    monkeypatch.delenv("TEST_JWT_SECRET", raising=False)
    monkeypatch.delenv("API_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)


def test_test_jwt_issuer_in_production_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    _clean_env(monkeypatch)
    monkeypatch.setenv("API_ENV", "production")
    monkeypatch.setenv("TEST_JWT_ISSUER", "https://evil.example/auth/v1")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_test_jwt_secret_in_production_via_environment_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    _clean_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TEST_JWT_SECRET", "some-secret")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_test_jwt_override_allowed_in_test_config_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from app.core.config import get_settings

    _clean_env(monkeypatch)
    monkeypatch.setenv("API_ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("TEST_JWT_ISSUER", "https://legacyflow-test.local/auth/v1")
    monkeypatch.setenv("TEST_JWT_SECRET", "per-run-secret")
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            settings = get_settings()
        assert settings.TEST_JWT_ISSUER == "https://legacyflow-test.local/auth/v1"
        assert any("TEST_JWT" in record.message for record in caplog.records)
    finally:
        get_settings.cache_clear()
