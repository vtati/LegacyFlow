"""Feature-flag resolution and firm-level administration.

Resolution precedence (highest wins): explicit ``LF_FLAG_<KEY>`` env
override > firm scope > jurisdiction scope > environment scope > built-in
default. Built-in defaults fail closed where it matters:
``document_assembly`` defaults to ``False``.

Authorization summary:
  * Read/write of effective/stored flags: firm_admin only (the caller's
    firm comes from ``authz.get_user_firm_id``; MVP: one firm).
  * Every write emits an auditable ``feature_flags.updated`` event.
"""

from __future__ import annotations

import os
import time
from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import AuthError, ValidationAppError

from . import repository as repo
from .schemas import EffectiveFlagOut, FeatureFlagOut

# In-memory flag cache: (key, firm_id, pack_id) -> (is_enabled, source, timestamp).
# Flags change rarely; 60s TTL avoids 2-3 DB roundtrips per interview turn.
_flag_cache: dict[tuple[str, str | None, str | None], tuple[bool, str, float]] = {}
_FLAG_CACHE_TTL = 60.0

# Built-in defaults used when no row and no env override applies.
# ``document_assembly`` fails closed: assembly must be explicitly enabled.
KNOWN_FLAG_DEFAULTS: dict[str, bool] = {
    "document_assembly": False,
    "ai_assistance": True,
    "funding_reminders": True,
    # AI interviewer: enabled by default for all firms. Per-firm overrides
    # via the admin feature-flags UI can still disable it per firm.
    "ai_interviewer": True,
    # Interview v2 (cluster flow + role bundles): fail-closed default;
    # enabled per firm by DB row (Demo firm) until the A/B bar passes.
    "interview_v2": False,
    # Attorney Reviewer Agent (§9): advisory pre-review of frozen draft
    # packages. Fail-closed default; enabled per firm (test firms first).
    "attorney_reviewer": False,
}

_ENV_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _actor_id(actor_sub: str) -> UUID:
    """Resolve the JWT ``sub`` claim to the caller's profile id."""
    try:
        return UUID(actor_sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def env_override(key: str) -> bool | None:
    """Return the explicit ``LF_FLAG_<KEY>`` env override, or ``None``.

    A blank value is treated as unset (platforms often inject empty env
    vars); a non-blank unrecognized value raises ``ValueError`` — an
    explicit override with garbage is a configuration error and must be
    loud, never silent.
    """
    raw = os.environ.get(f"LF_FLAG_{key.upper()}")
    if raw is None or not raw.strip():
        return None
    normalized = raw.strip().lower()
    if normalized in _ENV_TRUE_VALUES:
        return True
    if normalized in _ENV_FALSE_VALUES:
        return False
    raise ValueError(f"LF_FLAG_{key.upper()} has unrecognized value {raw!r}.")


def resolve_flag(
    conn: Any,
    key: str,
    *,
    firm_id: UUID | None = None,
    pack_id: str | None = None,
) -> tuple[bool, str]:
    """Resolve a flag to ``(is_enabled, source)``.

    ``source`` is one of ``env`` | ``firm`` | ``jurisdiction`` |
    ``environment`` | ``default``. Unknown keys fail closed (``False``).

    Results are cached in-process for 60s to avoid 2-3 DB roundtrips on
    every interview turn. Env overrides bypass the cache.
    """
    override = env_override(key)
    if override is not None:
        return override, "env"
    cache_key = (key, str(firm_id) if firm_id else None, pack_id)
    now = time.monotonic()
    cached = _flag_cache.get(cache_key)
    if cached is not None and now - cached[2] < _FLAG_CACHE_TTL:
        return cached[0], cached[1]
    if firm_id is not None:
        row = repo.get_flag(conn, key=key, scope="firm", scope_ref=str(firm_id))
        if row is not None:
            result = (bool(row["is_enabled"]), "firm")
            _flag_cache[cache_key] = (*result, now)
            return result
    if pack_id is not None:
        row = repo.get_flag(conn, key=key, scope="jurisdiction", scope_ref=pack_id)
        if row is not None:
            result = (bool(row["is_enabled"]), "jurisdiction")
            _flag_cache[cache_key] = (*result, now)
            return result
    row = repo.get_flag(conn, key=key, scope="environment", scope_ref="")
    if row is not None:
        result = (bool(row["is_enabled"]), "environment")
    else:
        result = (KNOWN_FLAG_DEFAULTS.get(key, False), "default")
    _flag_cache[cache_key] = (*result, now)
    return result


def is_enabled(
    conn: Any,
    key: str,
    *,
    firm_id: UUID | None = None,
    pack_id: str | None = None,
) -> bool:
    """Return the effective boolean value of a flag."""
    value, _source = resolve_flag(conn, key, firm_id=firm_id, pack_id=pack_id)
    return value


def list_effective_flags(
    conn: Any, *, actor_sub: str, pack_id: str | None = None
) -> list[EffectiveFlagOut]:
    """List the effective flags for the caller's firm (firm_admin only).

    When ``pack_id`` is given, jurisdiction-scope flags for that pack are
    included in resolution.
    """
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin"})
    return [
        EffectiveFlagOut(key=key, is_enabled=value, source=source)
        for key in KNOWN_FLAG_DEFAULTS
        for value, source in [resolve_flag(conn, key, firm_id=firm_id, pack_id=pack_id)]
    ]


def set_firm_flag(conn: Any, *, actor_sub: str, key: str, is_enabled: bool) -> FeatureFlagOut:
    """Set a firm-scope flag for the caller's firm (firm_admin only)."""
    if key not in KNOWN_FLAG_DEFAULTS:
        raise ValidationAppError(f"Unknown feature flag {key!r}.")
    actor_id = _actor_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, {"firm_admin"})
    row = repo.upsert_flag(
        conn,
        key=key,
        scope="firm",
        scope_ref=str(firm_id),
        is_enabled=is_enabled,
        created_by_user_id=actor_id,
    )
    # Invalidate cached resolutions for this flag+firm.
    _flag_cache.pop((key, str(firm_id), None), None)
    record_audit_event(
        conn,
        action="feature_flags.updated",
        resource_type="feature_flag",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=UUID(str(row["id"])),
        after_summary={"key": key, "scope": "firm", "is_enabled": is_enabled},
    )
    return FeatureFlagOut.model_validate(row)
