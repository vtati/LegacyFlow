"""JWT verification tests (HS256 test-override path; no external services)."""

import pytest

from app.core.errors import AuthError
from app.core.security import verify_supabase_jwt
from tests.conftest import (
    TEST_EMAIL,
    TEST_ISSUER,
    TEST_SUB,
    mint_token,
)


def test_verify_accepts_valid_test_token(test_settings_env: None) -> None:
    claims = verify_supabase_jwt(mint_token())
    assert claims.sub == TEST_SUB
    assert claims.email == TEST_EMAIL
    assert claims.iss == TEST_ISSUER


def test_tampered_token_rejected(test_settings_env: None) -> None:
    token = mint_token()
    tampered = token[:-6] + ("a" if not token.endswith("a") else "b") * 6
    with pytest.raises(AuthError):
        verify_supabase_jwt(tampered)


def test_expired_token_rejected(test_settings_env: None) -> None:
    with pytest.raises(AuthError):
        verify_supabase_jwt(mint_token(expired=True))


def test_wrong_secret_rejected(test_settings_env: None) -> None:
    with pytest.raises(AuthError):
        verify_supabase_jwt(mint_token(secret="a-different-secret"))


def test_malformed_token_rejected(test_settings_env: None) -> None:
    with pytest.raises(AuthError):
        verify_supabase_jwt("not-a-jwt")


def test_foreign_issuer_without_supabase_config_rejected(test_settings_env: None) -> None:
    # Token whose iss does not match TEST_JWT_ISSUER falls through to the
    # production JWKS path, which is unconfigured in tests -> 401, no network.
    with pytest.raises(AuthError):
        verify_supabase_jwt(mint_token(issuer="https://someone-else.example/auth/v1"))


def test_missing_test_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.delenv("TEST_JWT_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(AuthError):
            verify_supabase_jwt(mint_token())
    finally:
        get_settings.cache_clear()
