"""Feature-flag administration endpoints.

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.feature_flags.router import router
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims

from . import schemas as sch
from . import service

router = APIRouter(tags=["feature-flags"])

User = Annotated[Claims, Depends(get_current_user)]


@router.get("/admin/feature-flags", response_model=list[sch.EffectiveFlagOut])
def list_feature_flags(user: User, pack_id: str | None = None) -> list[sch.EffectiveFlagOut]:
    """List the effective feature flags for the caller's firm (firm_admin only).

    When ``pack_id`` is given, jurisdiction-scope flags for that pack are
    included in resolution.
    """
    with db.transaction() as conn:
        return service.list_effective_flags(conn, actor_sub=user.sub, pack_id=pack_id)


@router.put("/admin/feature-flags/{key}", response_model=sch.FeatureFlagOut)
def set_feature_flag(
    user: User, key: sch.FeatureFlagKey, payload: sch.FeatureFlagSetIn
) -> sch.FeatureFlagOut:
    """Set a firm-scope feature flag for the caller's firm (firm_admin only)."""
    with db.transaction() as conn:
        return service.set_firm_flag(
            conn, actor_sub=user.sub, key=key.value, is_enabled=payload.is_enabled
        )
