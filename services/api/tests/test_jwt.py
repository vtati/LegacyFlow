"""JWT verification tests (HS256 test-override path; no external services)."""

import json
import time
from typing import Any
from uuid import uuid4

import jwt
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
    # Service role key is required-but-empty in Slice 1 (see config.py).
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(AuthError):
            verify_supabase_jwt(mint_token())
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Production JWKS path, exercised with a monkeypatched JWKS document so no
# network is needed. These prove both key types the live project may serve
# (ES256 today, RS256 historically) verify correctly.
# ---------------------------------------------------------------------------


def _production_settings(monkeypatch: pytest.MonkeyPatch) -> str:
    """Settings on the real JWKS path: SUPABASE_URL set, test override off."""
    from app.core.config import get_settings

    url = "https://test-project.supabase.co"
    monkeypatch.setenv("SUPABASE_URL", url)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("TEST_JWT_ISSUER", raising=False)
    monkeypatch.delenv("TEST_JWT_SECRET", raising=False)
    get_settings.cache_clear()
    return url


def _use_fake_jwks(monkeypatch: pytest.MonkeyPatch, jwk: dict[str, Any]) -> None:
    import app.core.security as security_module

    monkeypatch.setattr(security_module, "_fetch_jwks", lambda _url: {"keys": [jwk]})


def _ec_keypair(kid: str) -> tuple[Any, dict[str, Any]]:
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    private_key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = kid
    return private_key, jwk


def _rsa_keypair(kid: str) -> tuple[Any, dict[str, Any]]:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = kid
    return private_key, jwk


def _mint_asymmetric(private_key: Any, kid: str, algorithm: str, issuer: str) -> tuple[str, str]:
    sub = str(uuid4())
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": sub,
            "iss": issuer,
            "aud": "authenticated",
            "exp": now + 600,
            "iat": now,
        },
        private_key,
        algorithm=algorithm,
        headers={"kid": kid},
    )
    return token, sub


def test_es256_token_verifies_against_ec_jwk(monkeypatch: pytest.MonkeyPatch) -> None:
    """ES256 (the live project's current algorithm) verifies end to end."""
    url = _production_settings(monkeypatch)
    private_key, jwk = _ec_keypair("ec-key-1")
    _use_fake_jwks(monkeypatch, jwk)
    token, sub = _mint_asymmetric(private_key, "ec-key-1", "ES256", f"{url}/auth/v1")
    claims = verify_supabase_jwt(token)
    assert claims.sub == sub
    assert claims.iss == f"{url}/auth/v1"


def test_rs256_token_verifies_against_rsa_jwk(monkeypatch: pytest.MonkeyPatch) -> None:
    """RS256 (older Supabase projects) keeps verifying after the ES256 fix."""
    url = _production_settings(monkeypatch)
    private_key, jwk = _rsa_keypair("rsa-key-1")
    _use_fake_jwks(monkeypatch, jwk)
    token, sub = _mint_asymmetric(private_key, "rsa-key-1", "RS256", f"{url}/auth/v1")
    claims = verify_supabase_jwt(token)
    assert claims.sub == sub
    assert claims.iss == f"{url}/auth/v1"


def test_es256_token_with_wrong_kid_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _production_settings(monkeypatch)
    private_key, _jwk = _ec_keypair("ec-key-1")
    other_private_key, other_jwk = _ec_keypair("ec-key-2")
    _use_fake_jwks(monkeypatch, other_jwk)
    token, _sub = _mint_asymmetric(private_key, "ec-key-1", "ES256", f"{url}/auth/v1")
    del other_private_key
    with pytest.raises(AuthError):
        verify_supabase_jwt(token)


def test_unsupported_key_type_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _production_settings(monkeypatch)
    _use_fake_jwks(monkeypatch, {"kid": "sym-key-1", "kty": "oct", "k": "secret"})
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "iss": f"{url}/auth/v1",
            "aud": "authenticated",
            "exp": int(time.time()) + 600,
        },
        "secret",
        algorithm="HS256",
        headers={"kid": "sym-key-1"},
    )
    with pytest.raises(AuthError, match="not supported"):
        verify_supabase_jwt(token)
