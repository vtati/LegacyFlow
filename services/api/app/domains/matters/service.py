"""Application services for the matters domain (Slice 2).

Matter creation binds a household to a jurisdiction pack: the pack is
resolved from the ``jurisdictions``/``jurisdiction_packs`` tables (most
recent pack in an intake-usable status) and a content snapshot is taken
from the file pack so the matter stays auditable against the exact pack
version it was created with. Status changes go through the server-side
state machine (``state_machine.py``) and are recorded in
``matter_status_history`` plus an audit event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core import authz, db
from app.core.audit import record_audit_event
from app.core.errors import (
    ApprovalGateRequiredError,
    AuthError,
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    ValidationAppError,
)
from app.domains.legal_content.pack_loader import LoadedPack, get_pack
from app.domains.matters import repository as repo
from app.domains.matters.schemas import (
    MatterCreate,
    MatterDetailResponse,
    MatterHouseholdSummary,
    MatterListResponse,
    MatterOut,
    MatterProgressSummary,
    MatterTransition,
)
from app.domains.matters.state_machine import (
    MatterStatus,
    gate_required_for,
    validate_transition,
)
from app.domains.review import repository as review_repo


class UnknownJurisdictionError(ValidationAppError):
    """No usable jurisdiction pack exists for the requested code (HTTP 422)."""

    type = "urn:legacyflow:error:unknown_jurisdiction"
    title = "Unknown jurisdiction"
    detail = "No usable jurisdiction pack exists for the requested jurisdiction code."


# Lifecycle order used as the server-side completeness proxy. The index of a
# status in this list, scaled to 0-100 as an integer, is what we store in
# ``matters.completeness_pct`` until intake answers drive a real percentage
# (Slice 3+).
_LIFECYCLE_ORDER = [status.value for status in MatterStatus]


def completeness_for(status: str) -> int:
    """Return the lifecycle-position completeness percentage for ``status``."""
    try:
        index = _LIFECYCLE_ORDER.index(status)
    except ValueError:
        return 0
    return round(100 * index / (len(_LIFECYCLE_ORDER) - 1))


def caller_id(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def _split_jurisdiction_code(jurisdiction_code: str) -> tuple[str, str]:
    parts = jurisdiction_code.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UnknownJurisdictionError()
    return parts[0].upper(), parts[1].upper()


def matter_out(row: dict[str, Any]) -> MatterOut:
    data = dict(row)
    pct = data.get("completeness_pct")
    data["completeness_pct"] = int(pct) if pct is not None else 0
    data["title"] = data.get("title") or ""
    household_name = data.get("household_name")
    if data.get("household_id") is not None and household_name:
        data["household"] = MatterHouseholdSummary(
            id=data["household_id"],
            name=str(household_name),
            display_name=str(household_name),
        )
    return MatterOut.model_validate(data)


def create_matter(
    conn: Any,
    *,
    actor_sub: str,
    payload: MatterCreate,
    correlation_id: UUID | None = None,
) -> MatterOut:
    """Create a matter for the caller's firm.

    Firm staff may create matters for any household in their firm.
    Non-staff callers (families) may create matters only for households
    where they hold the ``primary_client`` or ``collaborator`` role.
    """
    actor_id = caller_id(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)

    household = repo.get_household_in_firm(conn, payload.household_id, firm_id)
    if household is None:
        # Existence-oracle: cross-firm households read as "not found".
        raise NotFoundError("Household not found.")

    firm_role = authz.get_firm_role(conn, actor_id, firm_id)
    if firm_role not in authz.STAFF_ROLES:
        # Non-staff: require primary_client/collaborator on this household.
        household_role = repo.get_household_role(conn, payload.household_id, actor_id)
        if household_role not in ("primary_client", "collaborator"):
            raise AuthorizationError(
                "This action requires a firm staff role or the "
                "primary_client/collaborator household role."
            )

    country_code, subdivision_code = _split_jurisdiction_code(payload.jurisdiction_code)
    resolved = repo.resolve_jurisdiction_pack(conn, country_code, subdivision_code)
    if resolved is None:
        raise UnknownJurisdictionError()
    try:
        pack = get_pack(payload.jurisdiction_code.upper())
    except FileNotFoundError as exc:
        raise UnknownJurisdictionError() from exc

    snapshot: dict[str, Any] = {
        "pack_id": pack.manifest.pack_id,
        "jurisdiction_code": payload.jurisdiction_code.upper(),
        "semantic_version": pack.manifest.semantic_version,
        "rule_ids": [rule.id for rule in pack.escalation_rules],
        "question_set_versions": {
            name: (qs.get("question_set") or {}).get("version")
            for name, qs in pack.question_sets.items()
        },
    }

    matter = repo.insert_matter(
        conn,
        firm_id=firm_id,
        household_id=payload.household_id,
        primary_jurisdiction_id=UUID(str(resolved["jurisdiction_id"])),
        jurisdiction_pack_id=UUID(str(resolved["jurisdiction_pack_id"])),
        plan_type=payload.plan_type,
        title=payload.title or "",
        jurisdiction_snapshot=snapshot,
        completeness_pct=completeness_for(MatterStatus.INTAKE_IN_PROGRESS.value),
    )
    matter_id = UUID(str(matter["id"]))
    repo.insert_status_history(
        conn,
        matter_id=matter_id,
        from_status=None,
        to_status=MatterStatus.INTAKE_IN_PROGRESS.value,
        reason_code=None,
        actor_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="matters.created",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=matter_id,
        correlation_id=correlation_id,
        metadata={"jurisdiction_code": payload.jurisdiction_code.upper()},
    )
    matter["household_name"] = household.get("display_name")
    return matter_out(matter)


def list_matters(
    conn: Any,
    *,
    actor_sub: str,
    status: str | None = None,
    q: str | None = None,
    jurisdiction_code: str | None = None,
    assigned_to: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> MatterListResponse:
    """List matters visible to the caller.

    Firm members see their firm's matters; household clients see the matters
    of households they actively belong to.

    Filters: ``q`` matches matter title or household (client) name
    case-insensitively; ``jurisdiction_code`` is shaped ``COUNTRY-SUBDIVISION``
    (e.g. ``US-VA``); ``assigned_to`` is an attorney user id.
    Pagination is limit/offset based; the legacy ``page``/``page_size``
    envelope is derived for response compatibility.
    """
    actor_id = caller_id(actor_sub)
    if limit < 1 or offset < 0:
        raise ValidationAppError("Limit must be at least 1 and offset at least 0.")
    if status is not None:
        try:
            MatterStatus(status)
        except ValueError as exc:
            raise ValidationAppError("The status filter is not a known matter status.") from exc
    country_code: str | None = None
    subdivision_code: str | None = None
    if jurisdiction_code is not None:
        parts = jurisdiction_code.split("-", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValidationAppError(
                "The jurisdiction_code filter must look like COUNTRY-SUBDIVISION (e.g. US-VA)."
            )
        country_code, subdivision_code = parts[0].upper(), parts[1].upper()
    assigned_to_id: UUID | None = None
    if assigned_to is not None:
        try:
            assigned_to_id = UUID(assigned_to)
        except ValueError as exc:
            raise ValidationAppError("The assigned_to filter is not a valid user id.") from exc

    try:
        firm_id = authz.get_user_firm_id(conn, actor_id)
    except AuthorizationError:
        firm_id = None

    filters = {
        "status": status,
        "q": q,
        "country_code": country_code,
        "subdivision_code": subdivision_code,
        "assigned_to": assigned_to_id,
        "limit": limit,
        "offset": offset,
    }
    if firm_id is not None:
        items, total = repo.list_matters_for_firm(conn, firm_id, **filters)
    else:
        household_ids = repo.active_household_ids_for_user(conn, actor_id)
        if not household_ids:
            # Authenticated but belonging to no household yet (e.g. a fresh
            # self-serve signup): an empty list, not a 403, so the UI can
            # render its "no matters yet" empty state.
            return MatterListResponse(items=[], page=offset // limit + 1, page_size=limit, total=0)
        items, total = repo.list_matters_for_households(conn, household_ids, **filters)
    return MatterListResponse(
        items=[matter_out(row) for row in items],
        page=offset // limit + 1,
        page_size=limit,
        total=total,
    )


def get_matter_detail(conn: Any, *, actor_sub: str, matter_id: UUID) -> MatterDetailResponse:
    """Return a matter with its household summary and progress summary."""
    actor_id = caller_id(actor_sub)
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    household = repo.get_household(conn, UUID(str(scope.matter["household_id"])))
    if household is None:
        raise NotFoundError("Matter not found.")
    matter = matter_out(scope.matter)
    return MatterDetailResponse(
        matter=matter,
        household=MatterHouseholdSummary(
            id=household["id"],
            name=str(household["display_name"]),
            display_name=str(household["display_name"]),
        ),
        progress=MatterProgressSummary(
            completeness_pct=matter.completeness_pct,
            status=matter.status,
        ),
    )


def transition_matter(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    payload: MatterTransition,
    correlation_id: UUID | None = None,
) -> MatterOut:
    """Apply a validated status transition (firm staff+ only)."""
    actor_id = caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    matter_id = UUID(str(scope.matter["id"]))
    firm_id = UUID(str(scope.matter["firm_id"]))
    from_status = str(scope.matter["status"])
    to_status = payload.to_status

    try:
        validate_transition(from_status, to_status)
    except ValidationAppError:
        # Commit the rejection audit in its own transaction: the 422 raised
        # below rolls back the surrounding transaction.
        with db.transaction() as audit_conn:
            record_audit_event(
                audit_conn,
                action="matters.transition_rejected",
                resource_type="matter",
                actor_user_id=actor_id,
                actor_type="user",
                firm_id=firm_id,
                matter_id=matter_id,
                resource_id=matter_id,
                correlation_id=correlation_id,
                metadata={"from_status": from_status, "to_status": to_status},
            )
        raise

    gate = gate_required_for(from_status, to_status)
    if gate is not None:
        # Slice 3: a recorded, still-applicable approved gate of the required
        # type satisfies the gate; without one the transition fails closed
        # with a machine-readable 409.
        approved = review_repo.latest_gate_by_status(conn, matter_id, gate, "approved")
        if approved is None:
            raise ApprovalGateRequiredError(
                f"Transition {from_status} -> {to_status} requires the '{gate}' approval gate."
            )

    submitted_at = datetime.now(UTC) if to_status == MatterStatus.INTAKE_SUBMITTED.value else None
    updated = repo.update_matter_status(
        conn,
        matter_id,
        to_status=to_status,
        completeness_pct=completeness_for(to_status),
        submitted_at=submitted_at,
    )
    repo.insert_status_history(
        conn,
        matter_id=matter_id,
        from_status=from_status,
        to_status=to_status,
        reason_code=payload.reason,
        actor_user_id=actor_id,
    )
    household = repo.get_household(conn, UUID(str(updated["household_id"])))
    if household is not None:
        updated["household_name"] = household.get("display_name")
    record_audit_event(
        conn,
        action="matters.transitioned",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=matter_id,
        correlation_id=correlation_id,
        before_summary={"status": from_status},
        after_summary={"status": to_status},
    )
    return matter_out(updated)


# ---------------------------------------------------------------------------
# Shared helpers for the guided-intake track (Slice 2, Track C)
# ---------------------------------------------------------------------------


class PackNotFoundError(NotFoundError):
    """The matter's snapshotted pack version is not on disk (HTTP 404).

    Fail closed: the API never falls back to a different pack version than
    the one snapshotted on the matter.
    """

    type = "urn:legacyflow:error:pack_not_found"
    title = "Jurisdiction pack not found"
    detail = (
        "The jurisdiction pack version recorded on this matter is not available on this deployment."
    )


# Household roles allowed to write intake data. Dependents and trusted
# contacts are read-only (artifact §3.2).
HOUSEHOLD_WRITE_ROLES = frozenset({"primary_client", "collaborator"})


def require_matter_read_scope(conn: Any, matter_id: UUID, actor_id: UUID) -> authz.MatterScope:
    """Firm staff+ or any active household member (auditors excluded)."""
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    if scope.via == "firm" and scope.firm_role not in authz.STAFF_ROLES:
        raise AuthorizationError("This action requires a firm staff role or household membership.")
    return scope


def require_matter_write_scope(conn: Any, matter_id: UUID, actor_id: UUID) -> authz.MatterScope:
    """Firm staff+ or household primary_client/collaborator.

    Dependents and trusted contacts are read-only and get HTTP 403 here;
    callers with no access at all get HTTP 404 (existence-oracle rule).
    """
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    if scope.via == "firm":
        if scope.firm_role not in authz.STAFF_ROLES:
            raise AuthorizationError("This action requires a firm staff role.")
    elif scope.household_role not in HOUSEHOLD_WRITE_ROLES:
        raise AuthorizationError("This action requires the primary client or collaborator role.")
    return scope


def pack_version_for_matter(conn: Any, matter_id: UUID) -> tuple[str, str]:
    """Return ``(jurisdiction_code, semantic_version)`` for a matter's pack.

    Primary source is ``matters.jurisdiction_snapshot`` (written at matter
    creation). Rows with an empty snapshot fall back to the Slice-1 FKs via
    ``jurisdictions`` + ``jurisdiction_packs``.
    """
    matter = repo.get_matter(conn, matter_id)
    if matter is None:
        raise NotFoundError("Matter not found.")
    snapshot = matter.get("jurisdiction_snapshot") or {}
    code = snapshot.get("jurisdiction_code")
    version = snapshot.get("semantic_version")
    if code and version:
        return str(code), str(version)
    fallback = repo.get_matter_pack_fallback(conn, matter_id)
    if fallback is None or not fallback.get("version"):
        raise ConfigurationError("Matter has no resolvable jurisdiction pack version.")
    return (
        f"{fallback['country_code']}-{fallback['subdivision_code']}",
        str(fallback["version"]),
    )


def load_matter_pack(conn: Any, matter_id: UUID) -> tuple[LoadedPack, str, str]:
    """Load the file pack snapshotted on the matter.

    Returns ``(pack, jurisdiction_code, semantic_version)``. Raises
    :class:`PackNotFoundError` (HTTP 404) when the snapshotted version is
    not on disk — fail closed, never substitute another version.
    """
    code, version = pack_version_for_matter(conn, matter_id)
    try:
        return get_pack(code, version), code, version
    except FileNotFoundError as exc:
        raise PackNotFoundError() from exc


def assign_matter_attorney(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    attorney_user_id: UUID | None,
    correlation_id: UUID | None = None,
) -> MatterOut:
    """Assign (or unassign) the attorney for a matter (firm staff+ only).

    When assigning, the target user must hold an active attorney membership
    in the matter's firm.
    """
    actor_id = caller_id(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    matter_id = UUID(str(scope.matter["id"]))
    firm_id = UUID(str(scope.matter["firm_id"]))

    if attorney_user_id is not None:
        role = authz.get_firm_role(conn, attorney_user_id, firm_id)
        if role != "attorney":
            raise ValidationAppError(
                "The assignee must be an active attorney in this firm."
            )

    row = repo.set_assigned_attorney(
        conn, matter_id=matter_id, attorney_user_id=attorney_user_id
    )
    record_audit_event(
        conn,
        action="matters.attorney_assigned",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=matter_id,
        correlation_id=correlation_id,
    )
    return matter_out(row)
