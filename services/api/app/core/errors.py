"""Application errors rendered as RFC 7807 problem-detail responses."""

from __future__ import annotations

import uuid

_ERROR_URN_PREFIX = "urn:legacyflow:error:"


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

    @property
    def code(self) -> str:
        """Machine-readable code: the ``type`` URN suffix.

        ``urn:legacyflow:error:approval_gate_required`` -> ``approval_gate_required``.
        """
        if self.type.startswith(_ERROR_URN_PREFIX):
            return self.type[len(_ERROR_URN_PREFIX) :]
        return self.type

    def to_response(self) -> dict[str, str | int]:
        """Serialize as an RFC 7807 problem-details object."""
        return {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
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


class PayloadTooLargeError(AppError):
    """Request body exceeds the endpoint's size cap (HTTP 413, SEC-014)."""

    type = "urn:legacyflow:error:payload_too_large"
    title = "Payload too large"
    status = 413
    detail = "The request body exceeds the size limit for this endpoint."


class ValidationAppError(AppError):
    """Request failed domain validation (HTTP 422)."""

    type = "urn:legacyflow:error:validation_failed"
    title = "Validation failed"
    status = 422
    detail = "The request failed validation."


class ConflictError(AppError):
    """Request conflicts with current resource state (HTTP 409)."""

    type = "urn:legacyflow:error:conflict"
    title = "Conflict"
    status = 409
    detail = "The request conflicts with the current state of the resource."


class ApprovalGateRequiredError(ConflictError):
    """A state transition needs a recorded attorney approval gate (HTTP 409).

    The machine-readable ``code`` is ``approval_gate_required``. Slice 3
    records gates in ``approval_gates``; transitions whose gate type has a
    recorded ``approved`` gate proceed, others fail closed here.
    """

    type = "urn:legacyflow:error:approval_gate_required"
    title = "Approval gate required"
    detail = (
        "This transition requires a recorded attorney approval gate. "
        "Create and approve one via the approval-gates endpoints."
    )


class GoneError(AppError):
    """Resource is no longer available (HTTP 410)."""

    type = "urn:legacyflow:error:gone"
    title = "Gone"
    status = 410
    detail = "The requested resource is no longer available."


class ConfigurationError(AppError):
    """Deployment is missing required configuration (HTTP 501)."""

    type = "urn:legacyflow:error:configuration_error"
    title = "Service not configured"
    status = 501
    detail = "The service is not configured for this operation."


class ProviderError(AppError):
    """An upstream AI provider call failed (HTTP 502).

    The detail sent to clients is generic on purpose: network and
    provider internals are logged server-side only, never exposed.
    """

    type = "urn:legacyflow:error:provider_error"
    title = "AI provider unavailable"
    status = 502
    detail = "The AI service is temporarily unavailable. Please try again in a moment."


class RateLimitError(AppError):
    """Too many requests (HTTP 429). Used for AI interview turn rate limiting."""

    type = "urn:legacyflow:error:rate_limit_exceeded"
    title = "Rate limit exceeded"
    status = 429
    detail = "Too many requests. Please slow down and try again shortly."
