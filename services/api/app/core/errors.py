"""Application errors rendered as RFC 7807 problem-detail responses."""

from __future__ import annotations

import uuid


class AppError(Exception):
    """Base application error carrying an RFC 7807 problem detail."""

    type: str = "about:blank"
    title: str = "Application error"
    status: int = 500
    detail: str = "An unexpected error occurred."

    def __init__(self, detail: str = "", *, request_id: str | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail
        self.request_id = request_id or uuid.uuid4().hex

    def to_response(self) -> dict[str, str | int]:
        """Serialize as an RFC 7807 problem-details object."""
        return {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "request_id": self.request_id,
        }


class AuthError(AppError):
    """Authentication failed (HTTP 401)."""

    type = "urn:legacyflow:error:authentication_failed"
    title = "Authentication failed"
    status = 401
    detail = "The request could not be authenticated."


class AuthorizationError(AppError):
    """Caller is authenticated but not allowed (HTTP 403)."""

    type = "urn:legacyflow:error:authorization_failed"
    title = "Forbidden"
    status = 403
    detail = "The authenticated caller is not allowed to perform this action."


class NotFoundError(AppError):
    """Requested resource does not exist (HTTP 404)."""

    type = "urn:legacyflow:error:not_found"
    title = "Not found"
    status = 404
    detail = "The requested resource was not found."


class ValidationAppError(AppError):
    """Request failed domain validation (HTTP 422)."""

    type = "urn:legacyflow:error:validation_failed"
    title = "Validation failed"
    status = 422
    detail = "The request failed validation."


class ConfigurationError(AppError):
    """Deployment is missing required configuration (HTTP 501)."""

    type = "urn:legacyflow:error:configuration_error"
    title = "Service not configured"
    status = 501
    detail = "The service is not configured for this operation."
