"""FastAPI dependencies for authentication."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AuthError
from app.core.security import Claims, verify_supabase_jwt

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> Claims:
    """Resolve the authenticated caller from the ``Authorization: Bearer`` header.

    Returns the verified :class:`Claims` (sub, email, role, ...).
    Raises :class:`AuthError` (HTTP 401) when the header is missing or the
    token does not verify.
    """
    if credentials is None or not credentials.credentials:
        raise AuthError("A bearer token is required.")
    return verify_supabase_jwt(credentials.credentials)
