"""Application services for the assets domain (Slice 3).

Assets and liabilities are household-scoped: firm staff+ manage their
firm's households' records, and household members manage their own
household's records. Cross-tenant access reads as "not found"
(existence-oracle rule).

The alignment engine (:func:`evaluate_alignment_flags`) is a pure
function: it takes an asset row and the household's persons and returns
the deterministic flag set — no DB, no I/O — so it is unit-tested
directly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import (
    AuthError,
    AuthorizationError,
    NotFoundError,
)
from app.domains.assets import repository as repo
from app.domains.assets.schemas import (
    AlignmentEvaluationResponse,
    AssetCreate,
    AssetFlagOut,
    AssetListResponse,
    AssetOut,
    AssetPatch,
    BeneficiaryDesignation,
    LiabilityCreate,
    LiabilityListResponse,
    LiabilityOut,
    LiabilityPatch,
    designation_to_json,
)

# Asset types whose designations are expected to name contingent
# beneficiaries. Vocabulary matches the DB check constraint (Track A3).
RETIREMENT_INSURANCE_TYPES = frozenset({"retirement", "insurance"})

# Household roles allowed to write asset/liability records. Dependents and
# trusted contacts are read-only (artifact §3.2).
HOUSEHOLD_WRITE_ROLES = frozenset({"primary_client", "collaborator"})


def caller_id(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


# ---------------------------------------------------------------------------
# Deterministic alignment engine (pure: no DB, no I/O)
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """Canonical form for beneficiary/person name comparison."""
    return name.strip().casefold()


def _designation_entries(
    designation: Mapping[str, Any] | None, key: str
) -> list[Mapping[str, Any]]:
    if not isinstance(designation, Mapping):
        return []
    entries = designation.get(key)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


def _coerce_designation(raw: Any) -> Mapping[str, Any] | None:
    """Accept the jsonb column value as a dict (or a JSON string, defensively)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return raw if isinstance(raw, Mapping) else None


def evaluate_alignment_flags(
    asset: Mapping[str, Any],
    household_persons: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Evaluate beneficiary alignment for one asset (deterministic, pure).

    ``asset`` needs ``asset_type`` and ``beneficiary_designation`` (the
    jsonb value: ``{"primary": [{"name", "relationship"}], "contingent":
    [...]}`` or ``None``). ``household_persons`` needs each person's
    ``full_name``.

    Rules (applied in order; the flag list order is deterministic):
      * No beneficiary designation (missing, empty, or no primary
        beneficiaries) -> ``unknown_beneficiary`` (severity ``review``).
      * A named beneficiary matching no household person (compared
        case-insensitively after stripping whitespace) ->
        ``non_household_beneficiary`` (severity ``review``), one flag per
        unmatched name, sorted by name.
      * A retirement/insurance asset naming no contingent beneficiaries ->
        ``missing_contingent`` (severity ``info``).
    """
    flags: list[dict[str, str]] = []
    designation = _coerce_designation(asset.get("beneficiary_designation"))
    primary = _designation_entries(designation, "primary")
    contingent = _designation_entries(designation, "contingent")

    if not primary:
        flags.append(
            {
                "flag_type": "unknown_beneficiary",
                "severity": "review",
                "message": "No beneficiary designation is recorded for this asset.",
            }
        )

    person_names = {
        _normalize_name(str(person.get("full_name", "")))
        for person in household_persons
        if str(person.get("full_name", "")).strip()
    }
    # Collect unmatched names first so flags emit in sorted (deterministic)
    # order regardless of designation entry order.
    unmatched: dict[str, str] = {}
    for entry in primary + contingent:
        raw_name = entry.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        normalized = _normalize_name(raw_name)
        if normalized in person_names:
            continue
        unmatched.setdefault(normalized, raw_name.strip())
    for normalized in sorted(unmatched):
        flags.append(
            {
                "flag_type": "non_household_beneficiary",
                "severity": "review",
                "message": (
                    f"Beneficiary '{unmatched[normalized]}' is not recorded"
                    " as a person in this household."
                ),
            }
        )

    if str(asset.get("asset_type", "")) in RETIREMENT_INSURANCE_TYPES and not contingent:
        flags.append(
            {
                "flag_type": "missing_contingent",
                "severity": "info",
                "message": ("This retirement/insurance asset names no contingent beneficiaries."),
            }
        )

    # Deterministic order: unknown_beneficiary, then unmatched names sorted,
    # then missing_contingent (the append order above already satisfies this).
    return flags


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HouseholdScope:
    """A household the caller may access, plus how they may access it."""

    household: dict[str, Any]
    firm_id: UUID
    via: str  # "firm" | "household"
    firm_role: str | None  # firm_memberships.role when via == "firm"
    household_role: str | None  # household_members.relationship_role when via == "household"


def get_household_scope(conn: Any, household_id: UUID, actor_id: UUID) -> HouseholdScope:
    """Load a household the caller may access.

    Access is granted when the caller is an active member of the
    household's firm, or an active member of the household itself. Raises
    NotFoundError when the caller has no access (existence-oracle rule).
    """
    household = repo.get_household(conn, household_id)
    if household is None:
        raise NotFoundError("Household not found.")
    firm_id = UUID(str(household["firm_id"]))

    firm_role = authz.get_firm_role(conn, actor_id, firm_id)
    if firm_role is not None:
        return HouseholdScope(
            household=dict(household),
            firm_id=firm_id,
            via="firm",
            firm_role=firm_role,
            household_role=None,
        )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT relationship_role FROM household_members"
            " WHERE household_id = %s AND user_id = %s AND access_status = 'active'",
            (str(household_id), str(actor_id)),
        )
        member = cur.fetchone()
    if member is None:
        # No firm path and no household path: hide existence.
        raise NotFoundError("Household not found.")
    return HouseholdScope(
        household=dict(household),
        firm_id=firm_id,
        via="household",
        firm_role=None,
        household_role=str(member["relationship_role"]),
    )


def _require_household_read(scope: HouseholdScope) -> None:
    """Firm staff+ or any active household member (auditors excluded)."""
    if scope.via == "firm" and scope.firm_role not in authz.STAFF_ROLES:
        raise AuthorizationError("This action requires a firm staff role or household membership.")


def _require_household_write(scope: HouseholdScope) -> None:
    """Firm staff+ or household primary_client/collaborator.

    Callers with no access at all get HTTP 404 from
    :func:`get_household_scope` (existence-oracle rule); callers with
    read-only access get HTTP 403 here.
    """
    if scope.via == "firm":
        if scope.firm_role not in authz.STAFF_ROLES:
            raise AuthorizationError("This action requires a firm staff role.")
    elif scope.household_role not in HOUSEHOLD_WRITE_ROLES:
        raise AuthorizationError(
            "This action requires a firm staff role or the"
            " primary_client/collaborator household role."
        )


def _validate_matter_in_household(conn: Any, matter_id: UUID | None, household_id: UUID) -> None:
    """Ensure an optional matter link stays inside the asset's household."""
    if matter_id is None:
        return
    matter = repo.get_matter(conn, matter_id)
    if matter is None or UUID(str(matter["household_id"])) != household_id:
        raise NotFoundError("Matter not found.")


# ---------------------------------------------------------------------------
# Row -> schema helpers
# ---------------------------------------------------------------------------


def _asset_out(row: dict[str, Any]) -> AssetOut:
    data = dict(row)
    # The column is NOT NULL (default '{}'): always validate a designation.
    data["beneficiary_designation"] = BeneficiaryDesignation.model_validate(
        _coerce_designation(data.get("beneficiary_designation")) or {}
    )
    return AssetOut.model_validate(data)


def _liability_out(row: dict[str, Any]) -> LiabilityOut:
    return LiabilityOut.model_validate(row)


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def create_asset(
    conn: Any,
    *,
    actor_sub: str,
    household_id: UUID,
    payload: AssetCreate,
    correlation_id: UUID | None = None,
) -> AssetOut:
    """Create an asset for a household (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    scope = get_household_scope(conn, household_id, actor_id)
    _require_household_write(scope)
    _validate_matter_in_household(conn, payload.matter_id, household_id)

    row = repo.insert_asset(
        conn,
        firm_id=scope.firm_id,
        household_id=household_id,
        matter_id=payload.matter_id,
        asset_type=payload.asset_type,
        label=payload.label,
        estimated_value_cents=payload.estimated_value_cents,
        ownership=payload.ownership,
        beneficiary_designation=designation_to_json(payload.beneficiary_designation),
        notes=payload.notes,
        created_by_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="assets.created",
        resource_type="asset",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=scope.firm_id,
        matter_id=payload.matter_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
        # Summary only: never the label, value, notes, or beneficiary names.
        metadata={"asset_type": payload.asset_type, "household_id": str(household_id)},
    )
    return _asset_out(row)


def list_assets(
    conn: Any,
    *,
    actor_sub: str,
    household_id: UUID,
    page: int = 1,
    page_size: int = 25,
) -> AssetListResponse:
    """List a household's assets (firm staff+ or any household member)."""
    actor_id = caller_id(actor_sub)
    scope = get_household_scope(conn, household_id, actor_id)
    _require_household_read(scope)
    items, total = repo.list_assets_for_household(
        conn, household_id, page=page, page_size=page_size
    )
    return AssetListResponse(
        items=[_asset_out(row) for row in items],
        page=page,
        page_size=page_size,
        total=total,
    )


def _get_asset_in_scope(
    conn: Any, asset_id: UUID, actor_id: UUID
) -> tuple[dict[str, Any], HouseholdScope]:
    """Load an asset the caller may access (404 otherwise)."""
    row = repo.get_asset(conn, asset_id)
    if row is None:
        raise NotFoundError("Asset not found.")
    scope = get_household_scope(conn, UUID(str(row["household_id"])), actor_id)
    return row, scope


def get_asset(conn: Any, *, actor_sub: str, asset_id: UUID) -> AssetOut:
    """Return one asset (firm staff+ or any household member)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_asset_in_scope(conn, asset_id, actor_id)
    _require_household_read(scope)
    return _asset_out(row)


_ASSET_PATCH_FIELDS = frozenset(
    {"matter_id", "asset_type", "label", "estimated_value_cents", "ownership", "notes"}
)


def update_asset(
    conn: Any,
    *,
    actor_sub: str,
    asset_id: UUID,
    payload: AssetPatch,
    correlation_id: UUID | None = None,
) -> AssetOut:
    """Patch an asset (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_asset_in_scope(conn, asset_id, actor_id)
    _require_household_write(scope)
    fields = {
        k: v for k, v in payload.model_dump(exclude_unset=True).items() if k in _ASSET_PATCH_FIELDS
    }
    if "matter_id" in fields:
        _validate_matter_in_household(conn, fields["matter_id"], UUID(str(row["household_id"])))
    if fields:
        row = repo.update_asset(conn, asset_id, fields)
        record_audit_event(
            conn,
            action="assets.updated",
            resource_type="asset",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=scope.firm_id,
            matter_id=UUID(str(row["matter_id"])) if row.get("matter_id") else None,
            resource_id=asset_id,
            correlation_id=correlation_id,
            # Summary only: the changed field names, never the values.
            metadata={"updated_fields": sorted(fields)},
        )
    return _asset_out(row)


def delete_asset(
    conn: Any,
    *,
    actor_sub: str,
    asset_id: UUID,
    correlation_id: UUID | None = None,
) -> None:
    """Delete an asset and its flags (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_asset_in_scope(conn, asset_id, actor_id)
    _require_household_write(scope)
    matter_id = UUID(str(row["matter_id"])) if row.get("matter_id") else None
    repo.delete_asset(conn, asset_id)
    record_audit_event(
        conn,
        action="assets.deleted",
        resource_type="asset",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=scope.firm_id,
        matter_id=matter_id,
        resource_id=asset_id,
        correlation_id=correlation_id,
    )


def update_beneficiary(
    conn: Any,
    *,
    actor_sub: str,
    asset_id: UUID,
    payload: BeneficiaryDesignation,
    correlation_id: UUID | None = None,
) -> AssetOut:
    """Replace an asset's beneficiary designation (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_asset_in_scope(conn, asset_id, actor_id)
    _require_household_write(scope)
    row = repo.update_asset(
        conn, asset_id, {"beneficiary_designation": designation_to_json(payload)}
    )
    primary_count = len(payload.primary)
    contingent_count = len(payload.contingent)
    record_audit_event(
        conn,
        action="assets.beneficiary_updated",
        resource_type="asset",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=scope.firm_id,
        matter_id=UUID(str(row["matter_id"])) if row.get("matter_id") else None,
        resource_id=asset_id,
        correlation_id=correlation_id,
        # Counts only: never beneficiary names or relationships.
        metadata={"primary_count": primary_count, "contingent_count": contingent_count},
    )
    return _asset_out(row)


def evaluate_alignment(
    conn: Any,
    *,
    actor_sub: str,
    asset_id: UUID,
    correlation_id: UUID | None = None,
) -> AlignmentEvaluationResponse:
    """Regenerate an asset's alignment flags (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_asset_in_scope(conn, asset_id, actor_id)
    _require_household_write(scope)
    household_id = UUID(str(row["household_id"]))
    persons = repo.list_persons_for_household(conn, household_id)

    computed = evaluate_alignment_flags(row, persons)
    repo.delete_asset_flags(conn, asset_id)
    flag_rows = [
        repo.insert_asset_flag(
            conn,
            firm_id=scope.firm_id,
            asset_id=asset_id,
            flag_type=flag["flag_type"],
            severity=flag["severity"],
            message=flag["message"],
        )
        for flag in computed
    ]

    counts: dict[str, int] = {}
    for flag in computed:
        counts[flag["flag_type"]] = counts.get(flag["flag_type"], 0) + 1
    record_audit_event(
        conn,
        action="assets.alignment_evaluated",
        resource_type="asset",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=scope.firm_id,
        matter_id=UUID(str(row["matter_id"])) if row.get("matter_id") else None,
        resource_id=asset_id,
        correlation_id=correlation_id,
        # Summary counts only: never asset values or beneficiary names.
        metadata={"flag_count": len(computed), "flag_types": counts},
    )
    return AlignmentEvaluationResponse(
        asset_id=asset_id,
        flags=[AssetFlagOut.model_validate(flag_row) for flag_row in flag_rows],
        flag_counts=counts,
    )


# ---------------------------------------------------------------------------
# Liabilities
# ---------------------------------------------------------------------------


def create_liability(
    conn: Any,
    *,
    actor_sub: str,
    household_id: UUID,
    payload: LiabilityCreate,
    correlation_id: UUID | None = None,
) -> LiabilityOut:
    """Create a liability for a household (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    scope = get_household_scope(conn, household_id, actor_id)
    _require_household_write(scope)
    _validate_matter_in_household(conn, payload.matter_id, household_id)

    row = repo.insert_liability(
        conn,
        firm_id=scope.firm_id,
        household_id=household_id,
        matter_id=payload.matter_id,
        liability_type=payload.liability_type,
        label=payload.label,
        balance_cents=payload.balance_cents,
        monthly_payment_cents=payload.monthly_payment_cents,
        notes=payload.notes,
        created_by_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="liabilities.created",
        resource_type="liability",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=scope.firm_id,
        matter_id=payload.matter_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
        metadata={"liability_type": payload.liability_type, "household_id": str(household_id)},
    )
    return _liability_out(row)


def list_liabilities(
    conn: Any,
    *,
    actor_sub: str,
    household_id: UUID,
    page: int = 1,
    page_size: int = 25,
) -> LiabilityListResponse:
    """List a household's liabilities (firm staff+ or any household member)."""
    actor_id = caller_id(actor_sub)
    scope = get_household_scope(conn, household_id, actor_id)
    _require_household_read(scope)
    items, total = repo.list_liabilities_for_household(
        conn, household_id, page=page, page_size=page_size
    )
    return LiabilityListResponse(
        items=[_liability_out(row) for row in items],
        page=page,
        page_size=page_size,
        total=total,
    )


def _get_liability_in_scope(
    conn: Any, liability_id: UUID, actor_id: UUID
) -> tuple[dict[str, Any], HouseholdScope]:
    """Load a liability the caller may access (404 otherwise)."""
    row = repo.get_liability(conn, liability_id)
    if row is None:
        raise NotFoundError("Liability not found.")
    scope = get_household_scope(conn, UUID(str(row["household_id"])), actor_id)
    return row, scope


def get_liability(conn: Any, *, actor_sub: str, liability_id: UUID) -> LiabilityOut:
    """Return one liability (firm staff+ or any household member)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_liability_in_scope(conn, liability_id, actor_id)
    _require_household_read(scope)
    return _liability_out(row)


_LIABILITY_PATCH_FIELDS = frozenset(
    {"matter_id", "liability_type", "label", "balance_cents", "monthly_payment_cents", "notes"}
)


def update_liability(
    conn: Any,
    *,
    actor_sub: str,
    liability_id: UUID,
    payload: LiabilityPatch,
    correlation_id: UUID | None = None,
) -> LiabilityOut:
    """Patch a liability (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_liability_in_scope(conn, liability_id, actor_id)
    _require_household_write(scope)
    fields = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if k in _LIABILITY_PATCH_FIELDS
    }
    if "matter_id" in fields:
        _validate_matter_in_household(conn, fields["matter_id"], UUID(str(row["household_id"])))
    if fields:
        row = repo.update_liability(conn, liability_id, fields)
        record_audit_event(
            conn,
            action="liabilities.updated",
            resource_type="liability",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=scope.firm_id,
            matter_id=UUID(str(row["matter_id"])) if row.get("matter_id") else None,
            resource_id=liability_id,
            correlation_id=correlation_id,
            metadata={"updated_fields": sorted(fields)},
        )
    return _liability_out(row)


def delete_liability(
    conn: Any,
    *,
    actor_sub: str,
    liability_id: UUID,
    correlation_id: UUID | None = None,
) -> None:
    """Delete a liability (firm staff+ or household writer)."""
    actor_id = caller_id(actor_sub)
    row, scope = _get_liability_in_scope(conn, liability_id, actor_id)
    _require_household_write(scope)
    matter_id = UUID(str(row["matter_id"])) if row.get("matter_id") else None
    repo.delete_liability(conn, liability_id)
    record_audit_event(
        conn,
        action="liabilities.deleted",
        resource_type="liability",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=scope.firm_id,
        matter_id=matter_id,
        resource_id=liability_id,
        correlation_id=correlation_id,
    )
