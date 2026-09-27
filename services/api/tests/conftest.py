"""Shared fixtures: test JWT settings (HS256 override) and a TestClient.

All tests run without any external service (no Supabase, no Postgres).
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_ISSUER = "https://legacyflow-test.local/auth/v1"
TEST_SECRET = "slice1-test-secret-not-a-real-credential-32b"
TEST_SUB = "11111111-2222-3333-4444-555555555555"
TEST_EMAIL = "synthetic.user@example.com"
TEST_AUDIENCE = "authenticated"


def mint_token(
    *,
    sub: str = TEST_SUB,
    email: str = TEST_EMAIL,
    issuer: str = TEST_ISSUER,
    secret: str = TEST_SECRET,
    audience: str = TEST_AUDIENCE,
    expired: bool = False,
) -> str:
    """Mint a synthetic HS256 test JWT (never used outside tests)."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now - 60 if expired else now + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture()
def test_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the app at the HS256 test-override path; clear DATABASE_URL."""
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    # Service role key is required-but-empty in Slice 1 (see config.py).
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Rate limiting is disabled for the general suite: it is covered by its
    # own dedicated tests in test_hardening.py (which re-enable it via the
    # fresh_limits fixture). Leaving it on would make unrelated tests
    # order-dependent (429s after enough requests in a session).
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client(test_settings_env: None) -> TestClient:
    """TestClient with the test settings active."""
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)
