"""Supabase JWT verification.

Production path: validates RS256 signatures against the Supabase JWKS
endpoint (cached for 10 minutes), checking ``exp``, ``iss`` and ``aud``.

Test override: when ``TEST_JWT_ISSUER`` is configured and the token's
unverified ``iss`` claim matches it, the token is validated as HS256 with
``TEST_JWT_SECRET`` instead. This allows local tests to run without a
Supabase project.

Tokens are never logged.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.errors import AuthError

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 600

# URL -> (expires_at_monotonic, parsed JWKS document)
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class Claims(BaseModel):
    """Authenticated caller identity extracted from a verified JWT."""

    model_config = ConfigDict(extra="ignore")

    sub: str
    email: str | None = None
    role: str | None = None
    aud: str | list[str] | None = None
    iss: str | None = None
    exp: int | None = None
    iat: int | None = None


def _fetch_jwks(url: str) -> dict[str, Any]:
    """Fetch the JWKS document, honoring the 10-minute TTL cache."""
    now = time.monotonic()
    cached = _jwks_cache.get(url)
    if cached is not None:
        expires_at, document = cached
        if now < expires_at:
            return document
    try:
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()
        document = response.json()
    except httpx.HTTPError as exc:
        # Never include token material in the message.
        raise AuthError("The authentication service is unreachable.") from exc
    if not isinstance(document, dict):
        raise AuthError("The authentication service returned an invalid key set.")
    _jwks_cache[url] = (now + _JWKS_TTL_SECONDS, document)
    return document


def _signing_key_for(token: str, jwks_url: str) -> Any:
    """Resolve the RSA public key matching the token's ``kid`` header."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise AuthError("The token is malformed.") from exc
    if not kid:
        raise AuthError("The token is missing a key identifier.")
    document = _fetch_jwks(jwks_url)
    keys = document.get("keys", [])
    for jwk in keys:
        if isinstance(jwk, dict) and jwk.get("kid") == kid:
            try:
                return RSAAlgorithm.from_jwk(json.dumps(jwk))
            except Exception as exc:
                raise AuthError("The token signing key is invalid.") from exc
    raise AuthError("The token signing key is not recognized.")


def verify_supabase_jwt(token: str) -> Claims:
    """Verify a bearer token and return the caller's claims.

    Raises :class:`AuthError` (HTTP 401) on any verification failure.
    """
    settings = get_settings()

    # Test override for local development/CI without a Supabase project.
    if settings.TEST_JWT_ISSUER:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise AuthError("The token is malformed.") from exc
        if isinstance(unverified, dict) and unverified.get("iss") == settings.TEST_JWT_ISSUER:
            if not settings.TEST_JWT_SECRET:
                raise AuthError("Test JWT signing is not configured.")
            try:
                payload = jwt.decode(
                    token,
                    settings.TEST_JWT_SECRET,
                    algorithms=["HS256"],
                    audience=settings.SUPABASE_JWT_AUDIENCE,
                    issuer=settings.TEST_JWT_ISSUER,
                )
            except jwt.ExpiredSignatureError as exc:
                raise AuthError("The token has expired.") from exc
            except jwt.PyJWTError as exc:
                raise AuthError("The token signature is invalid.") from exc
            return Claims.model_validate(payload)

    jwks_url = settings.jwks_url
    if jwks_url is None:
        raise AuthError("Authentication is not configured for this deployment.")
    key = _signing_key_for(token, jwks_url)
    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=settings.SUPABASE_JWT_AUDIENCE,
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("The token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("The token signature is invalid.") from exc
    return Claims.model_validate(payload)
