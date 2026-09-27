"""Live JWKS checks against the configured Supabase project.

Opt-in: every test here SKIPS unless ``SUPABASE_URL`` (or ``SUPABASE_JWKS_URL``)
is set in the environment. These tests exercise the API's real RS256/JWKS
verification path against the live project — the unit suite in ``test_jwt.py``
covers the HS256 test override only.

Run locally with, e.g.:
    SUPABASE_URL=https://<ref>.supabase.co pytest tests/test_jwks_live.py -q
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import jwt
import pytest

LIVE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
LIVE_JWKS_URL = os.environ.get("SUPABASE_JWKS_URL") or (
    f"{LIVE_URL}/auth/v1/.well-known/jwks.json" if LIVE_URL else ""
)

pytestmark = pytest.mark.skipif(
    not LIVE_JWKS_URL,
    reason="live JWKS tests need SUPABASE_URL or SUPABASE_JWKS_URL in the environment",
)


@pytest.fixture()
def live_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the app at the live project's JWKS (production code path)."""
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_URL", LIVE_URL)
    if os.environ.get("SUPABASE_JWKS_URL"):
        monkeypatch.setenv("SUPABASE_JWKS_URL", os.environ["SUPABASE_JWKS_URL"])
    else:
        monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Service role key is required-but-empty in Slice 1 (see config.py).
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    # Disable the HS256 test override so the RS256/JWKS path is exercised.
    monkeypatch.delenv("TEST_JWT_ISSUER", raising=False)
    monkeypatch.delenv("TEST_JWT_SECRET", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_live_jwks_document_is_valid_keyset() -> None:
    """The project's JWKS endpoint serves a parseable RSA or EC key set."""
    from app.core.security import _fetch_jwks

    document = _fetch_jwks(LIVE_JWKS_URL)
    keys = document.get("keys", [])
    assert isinstance(keys, list) and keys, "expected at least one JWK"
    assert any(k.get("kty") in ("RSA", "EC") for k in keys if isinstance(k, dict))
    assert any(k.get("kid") for k in keys if isinstance(k, dict))


def test_verify_rejects_unknown_kid_against_live_jwks(live_settings_env: None) -> None:
    """End-to-end through verify_supabase_jwt: unknown kid -> 401, not a crash."""
    from app.core.errors import AuthError
    from app.core.security import verify_supabase_jwt

    token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "iss": f"{LIVE_URL}/auth/v1",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        },
        "not-a-real-signing-key",
        algorithm="HS256",
        headers={"kid": "definitely-not-a-real-kid"},
    )
    with pytest.raises(AuthError):
        verify_supabase_jwt(token)
