"""LegacyFlow API — FastAPI application entrypoint.

Thin composition root: middleware, RFC 7807 error handlers, router wiring.
Business logic lives in ``app.domains``; routers only validate/orchestrate
(artifact §6.4).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.idempotency import IdempotencyMiddleware
from app.core.logging import configure_logging, reset_request_id, set_request_id
from app.core.rate_limit import RateLimitExceededError, RateLimitMiddleware, rate_limit_response

SERVICE_VERSION = "0.1.0"

type ProblemDetail = dict[str, str | int]


def _problem(
    status_code: int,
    title: str,
    detail: str,
    request_id: str,
    type_uri: str = "about:blank",
) -> ProblemDetail:
    """Build an RFC 7807 problem-details object."""
    return {
        "type": type_uri,
        "title": title,
        "status": status_code,
        "detail": detail,
        "request_id": request_id,
    }


def _request_id_of(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else uuid.uuid4().hex


def _init_sentry() -> None:
    """Initialize Sentry only when a DSN is configured.

    The SDK import is optional: the API runs fine without ``sentry-sdk``
    installed. ``send_default_pii`` stays off — engineering-contract rule 9
    forbids shipping PII or plan content to third parties. A malformed DSN
    is logged and ignored (observability must never break app construction).
    """
    settings = get_settings()
    if not settings.SENTRY_DSN.strip():
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.API_ENV,
            release=SERVICE_VERSION,
            send_default_pii=False,
        )
    except Exception as exc:  # Sentry is best-effort; never break startup
        logging.getLogger(__name__).warning("Sentry init failed; continuing without it: %s", exc)


def _is_production(settings: Any) -> bool:
    return settings.API_ENV == "production" or settings.ENVIRONMENT == "production"


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    configure_logging(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    _init_sentry()
    # SEC-004: interactive docs / OpenAPI schema are disabled in production.
    production = _is_production(settings)
    application = FastAPI(
        title="LegacyFlow API",
        version=SERVICE_VERSION,
        docs_url=None if production else "/docs",
        openapi_url=None if production else "/openapi.json",
        redoc_url=None if production else "/redoc",
    )

    @application.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        token = set_request_id(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return response

    # First registered = outermost: idempotency sits outside the rate
    # limiter so cheap replays bypass it, while fresh mutating requests
    # are still limited.
    application.add_middleware(IdempotencyMiddleware)
    application.add_middleware(RateLimitMiddleware)

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        exc.request_id = _request_id_of(request)
        return JSONResponse(status_code=exc.status, content=exc.to_response())

    @application.exception_handler(RateLimitExceededError)
    async def rate_limit_exceeded_handler(
        request: Request, exc: RateLimitExceededError
    ) -> JSONResponse:
        # Registered for the per-router ``rate_limit_dependency`` path; the
        # global middleware renders its own 429 (middleware runs outside the
        # exception-handler stack). Starlette dispatches to the most-derived
        # handler, so this wins over the AppError handler above.
        return rate_limit_response(request, exc)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Summarize field locations only; never echo request values, which may
        # contain PII or plan content (engineering contract rule 9).
        fields = [
            ".".join(str(part) for part in error.get("loc", [])) or "body" for error in exc.errors()
        ]
        detail = f"Request validation failed for {len(fields)} field(s): {', '.join(fields)}."
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_problem(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Validation failed",
                detail,
                _request_id_of(request),
                "urn:legacyflow:error:validation_failed",
            ),
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log the full traceback server-side; never leak internal details to the client.
        logger = logging.getLogger(__name__)
        logger.exception(
            "Unhandled exception",
            extra={"request_id": _request_id_of(request), "path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_problem(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Internal server error",
                "An unexpected error occurred.",
                _request_id_of(request),
                "urn:legacyflow:error:internal_server_error",
            ),
        )

    application.include_router(v1_router, prefix="/v1")

    @application.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, Any]:
        """Liveness + DB reachability.

        A 200 here means the API can reach Postgres. Railway cannot route
        IPv6, so a DB check is the only meaningful health signal — a bare
        "ok" proves nothing (see AGENTS.md lesson 2026-09-27).
        """
        from app.core import db as db_module

        try:
            with db_module.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
        except Exception as exc:  # noqa: BLE001 — health must never raise
            # SEC-006: detail (service/version/env/db error) stays in
            # server logs only; the public body is just the status.
            logging.getLogger(__name__).warning(
                "healthz db check failed: service=legacyflow-api version=%s "
                "env=%s db=unreachable: %s",
                SERVICE_VERSION,
                settings.API_ENV,
                type(exc).__name__,
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "degraded"},
            )
        return {"status": "ok"}

    @application.get("/", tags=["ops"])
    def root() -> dict[str, str]:
        body: dict[str, str] = {
            "service": "legacyflow-api",
            "version": SERVICE_VERSION,
            "health": "/healthz",
        }
        if not production:
            body["docs"] = "/docs"
        return body

    return application


app = create_app()
