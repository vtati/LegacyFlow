"""In-memory sliding-window rate limiting.

Tiers (requests/minute, tunable via settings):

- ``auth`` (10): auth-adjacent endpoints — invitation accept/revoke,
  vault access, vault shares, download tokens, ``/me``.
- ``upload_ai`` (30): expensive endpoints — file uploads, AI ask,
  document generation, pack import, intake evaluation.
- ``default`` (120): everything else.

Identity: the verified JWT ``sub`` when a valid Bearer <redacted> is present,
otherwise the client IP. Bucketing by user keeps NAT'd clients from sharing
one bucket; unverifiable tokens fall back to the IP bucket so forged
subjects cannot evade the limit. Tokens are never logged (contract rule 9).

Exempt: ``/``, ``/healthz``, docs and OpenAPI — health checks and schema
fetches must never 429.

Single-instance limitation: buckets live in process memory. The scale path
is a Redis-backed sliding window keyed the same way; ``tier_for_path`` and
``check_rate_limit`` keep their contracts.

Wiring: ``RateLimitMiddleware`` is global (see ``app.main``). Per-router
opt-in is available via ``rate_limit_dependency(tier)``, which raises
``RateLimitExceededError`` — rendered as RFC 7807 with a ``Retry-After`` header
by the handler registered in ``app.main``.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import get_request_id
from app.core.security import verify_supabase_jwt


class RateTier(StrEnum):
    """Rate-limit tier applied to a request path."""

    AUTH = "auth"
    UPLOAD_AI = "upload_ai"
    DEFAULT = "default"


_EXEMPT_PATHS = frozenset(
    {"/", "/healthz", "/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"}
)

_AUTH_PREFIXES = (
    "/v1/invitations/accept",
    "/v1/firms/invitations",
    "/v1/vault",
    "/v1/me",
)
_AUTH_SUBSTRINGS = ("/vault-shares", "/download-token")

_UPLOAD_AI_PREFIXES = ("/v1/admin/packs/import",)
_UPLOAD_AI_SUBSTRINGS = (
    "/ai/",
    "/files/",
    "/generate",
    "/document-packages",
    "/intake/evaluate",
    "/evidence",
)

_MAX_BUCKETS = 10_000
_WINDOW_SECONDS = 60.0


def _matches(path: str, prefixes: tuple[str, ...], substrings: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return any(fragment in path for fragment in substrings)


def tier_for_path(path: str) -> RateTier | None:
    """Return the rate-limit tier for ``path``, or ``None`` when exempt."""
    if path in _EXEMPT_PATHS:
        return None
    if _matches(path, _AUTH_PREFIXES, _AUTH_SUBSTRINGS):
        return RateTier.AUTH
    if _matches(path, _UPLOAD_AI_PREFIXES, _UPLOAD_AI_SUBSTRINGS):
        return RateTier.UPLOAD_AI
    return RateTier.DEFAULT


def client_identity(request: Request) -> str:
    """Best-effort caller identity for abuse buckets.

    Returns ``user:<sub>`` when the request carries a verifiable Bearer
    <redacted>; otherwise ``ip:<client host>``. Any verification failure falls
    back to the IP bucket. Never logs the token.
    """
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        try:
            return f"user:{verify_supabase_jwt(token.strip()).sub}"
        except Exception:
            pass
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


class RateLimitExceededError(AppError):
    """Rate limit exceeded (HTTP 429)."""

    type = "urn:legacyflow:error:rate_limit_exceeded"
    title = "Rate limit exceeded"
    status = 429
    detail = "Too many requests."

    def __init__(
        self,
        detail: str = "",
        *,
        retry_after: int = 60,
        request_id: str | None = None,
    ) -> None:
        super().__init__(detail, request_id=request_id)
        self.retry_after = retry_after


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window limiter (single instance)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def check(
        self, bucket: str, identity: str, limit: int, *, window_seconds: float = _WINDOW_SECONDS
    ) -> float:
        """Record a hit; return ``0.0`` when allowed, else seconds until retry."""
        now = time.monotonic()
        key = (bucket, identity)
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            cutoff = now - window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.0, hits[0] + window_seconds - now)
            hits.append(now)
            if len(self._hits) > _MAX_BUCKETS:
                self._evict_expired(now, window_seconds)
            return 0.0

    def _evict_expired(self, now: float, window_seconds: float) -> None:
        cutoff = now - window_seconds
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        """Clear all buckets (tests)."""
        with self._lock:
            self._hits.clear()


_limiter = SlidingWindowRateLimiter()


def reset_rate_limiter() -> None:
    """Clear all rate-limit buckets (tests)."""
    _limiter.reset()


def _limit_for(tier: RateTier, settings: Settings) -> int:
    if tier is RateTier.AUTH:
        return settings.RATE_LIMIT_AUTH_PER_MINUTE
    if tier is RateTier.UPLOAD_AI:
        return settings.RATE_LIMIT_UPLOAD_AI_PER_MINUTE
    return settings.RATE_LIMIT_DEFAULT_PER_MINUTE


def check_rate_limit(request: Request, tier: RateTier) -> None:
    """Enforce the tier limit for ``request``; raise :class:`RateLimitExceededError`."""
    settings = get_settings()
    if not settings.RATE_LIMIT_ENABLED:
        return
    limit = _limit_for(tier, settings)
    retry_after = _limiter.check(tier.value, client_identity(request), limit)
    if retry_after > 0:
        raise RateLimitExceededError(
            f"Rate limit exceeded ({limit} requests/minute for this endpoint).",
            retry_after=max(1, math.ceil(retry_after)),
        )


def rate_limit_response(request: Request, exc: RateLimitExceededError) -> JSONResponse:
    """Render an RFC 7807 429 response with a ``Retry-After`` header."""
    raw = get_request_id() or getattr(request.state, "request_id", None)
    request_id = str(raw) if raw else uuid.uuid4().hex
    return JSONResponse(
        status_code=429,
        content={
            "type": exc.type,
            "title": exc.title,
            "status": 429,
            "detail": exc.detail,
            "request_id": request_id,
        },
        headers={"Retry-After": str(exc.retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global rate limiting driven by :func:`tier_for_path`."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tier = tier_for_path(request.url.path)
        if tier is None:
            return await call_next(request)
        try:
            check_rate_limit(request, tier)
        except RateLimitExceededError as exc:
            return rate_limit_response(request, exc)
        return await call_next(request)


def rate_limit_dependency(
    tier: RateTier = RateTier.DEFAULT,
) -> Callable[[Request], Awaitable[None]]:
    """Return a FastAPI dependency enforcing ``tier`` (per-router opt-in).

    Raises :class:`RateLimitExceededError`; register the handler in ``app.main``
    (already wired) to render RFC 7807 + ``Retry-After``.
    """

    async def _check(request: Request) -> None:
        check_rate_limit(request, tier)

    return _check
