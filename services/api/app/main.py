"""LegacyFlow API — FastAPI application entrypoint.

Thin composition root: middleware, RFC 7807 error handlers, router wiring.
Business logic lives in ``app.domains``; routers only validate/orchestrate
(artifact §6.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import AppError

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


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    application = FastAPI(
        title="LegacyFlow API",
        version=SERVICE_VERSION,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @application.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        exc.request_id = _request_id_of(request)
        return JSONResponse(status_code=exc.status, content=exc.to_response())

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
        # Never leak internal details to the client.
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
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "legacyflow-api",
            "version": SERVICE_VERSION,
            "env": settings.API_ENV,
        }

    @application.get("/", tags=["ops"])
    def root() -> dict[str, str]:
        return {
            "service": "legacyflow-api",
            "version": SERVICE_VERSION,
            "docs": "/docs",
            "health": "/healthz",
        }

    return application


app = create_app()
