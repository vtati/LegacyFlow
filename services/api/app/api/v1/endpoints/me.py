"""Authentication/profile endpoints (artifact §8.2)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.deps import get_current_user
from app.core.security import Claims
from app.domains.identity.schemas import ProfileOut
from app.domains.identity.service import IdentityService

router = APIRouter()


@router.get("/me", response_model=ProfileOut)
def read_current_profile(
    claims: Annotated[Claims, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProfileOut:
    """Return the authenticated user's profile (``GET /v1/me``).

    Thin router: delegates to :class:`IdentityService`. Returns HTTP 501
    ``profile_store_unavailable`` when no ``DATABASE_URL`` is configured
    (documented Slice-1 behavior), HTTP 401 when unauthenticated.
    """
    return IdentityService(settings).get_profile(claims)
