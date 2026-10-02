"""Application services for the household domain (Slice 2).

Households belong to firms; persons and relationships belong to
households. Firm staff+ may manage everything; household clients with the
primary_client or collaborator role may manage persons/relationships but
never membership rows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.authz import MatterScope
from app.core.errors import (
    AuthError,
    AuthorizationError,
    NotFoundError,
    ValidationAppError,
)
from app.domains.household import repository as repo
from app.domains.household.schemas import (
    HouseholdCreate,
    HouseholdDetailResponse,
    HouseholdMemberCreate,
    HouseholdMemberOut,
    HouseholdOut,
    PersonCreate,
    PersonOut,
    PersonPatch,
    RelationshipCreate,
    RelationshipOut,
)

# Household roles allowed to manage persons/relationships (client side).
HOUSEHOLD_WRITER_ROLES = {"primary_client", "collaborator"}


def _user_id_or_raise(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def _household_out(row: dict[str, Any]) -> HouseholdOut:
    data = dict(row)
    data["name"] = data["display_name"]
    return HouseholdOut.model_validate(data)


def _member_out(row: dict[str, Any]) -> HouseholdMemberOut:
    person: PersonOut | None = None
    if row.get("person_id") is not None:
        person = PersonOut.model_validate(
            {
                "id": row["person_id"],
                "household_id": row["person_household_id"],
                "full_name": row["person_full_name"],
                "date_of_birth": row["person_date_of_birth"],
                "email": row["person_email"],
                "kind": row["person_kind"],
                "notes": row["person_notes"],
                "created_at": row["person_created_at"],
            }
        )
    data = dict(row)
    data["person"] = person
    return HouseholdMemberOut.model_validate(data)


def _require_household_write(scope: MatterScope) -> None:
    """Require firm staff+ or household primary_client/collaborator."""
    if scope.via == "firm" and scope.firm_role in authz.STAFF_ROLES:
        return
    if scope.via == "household" and scope.household_role in HOUSEHOLD_WRITER_ROLES:
        return
    raise AuthorizationError(
        "This action requires a firm staff role or the primary_client/collaborator household role."
    )


def create_household(
    conn: Any,
    *,
    actor_sub: str,
    payload: HouseholdCreate,
    correlation_id: UUID | None = None,
) -> HouseholdOut:
    """Create a household for the caller's firm.

    Any authenticated user with an active firm membership may create a
    household (self-service family onboarding). The creator is recorded as
    the household's ``primary_client``. Firm staff retain full access via
    their firm role.
    """
    actor_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    row = repo.insert_household(
        conn,
        firm_id=firm_id,
        display_name=payload.name,
        created_by_user_id=actor_id,
    )
    # The creator becomes the household's primary client.
    repo.insert_household_member(
        conn,
        household_id=UUID(str(row["id"])),
        user_id=actor_id,
        relationship_role="primary_client",
        access_status="active",
        invited_by=actor_id,
    )
    record_audit_event(
        conn,
        action="households.created",
        resource_type="household",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
    )
    return _household_out(row)


def list_households(conn: Any, *, actor_sub: str) -> list[HouseholdOut]:
    """List the caller's firm households (firm staff+ only)."""
    actor_id = _user_id_or_raise(actor_sub)
    firm_id = authz.get_user_firm_id(conn, actor_id)
    authz.require_firm_role(conn, actor_id, firm_id, authz.STAFF_ROLES)
    return [_household_out(row) for row in repo.list_households_for_firm(conn, firm_id)]


def get_household_detail(conn: Any, *, actor_sub: str, matter_id: UUID) -> HouseholdDetailResponse:
    """Return a matter's household with members, persons, relationships."""
    actor_id = _user_id_or_raise(actor_sub)
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    household_id = UUID(str(scope.matter["household_id"]))
    household = repo.get_household(conn, household_id)
    if household is None:
        raise NotFoundError("Matter not found.")
    return HouseholdDetailResponse(
        household=_household_out(household),
        members=[_member_out(row) for row in repo.list_members_for_household(conn, household_id)],
        persons=[
            PersonOut.model_validate(row)
            for row in repo.list_persons_for_household(conn, household_id)
        ],
        relationships=[
            RelationshipOut.model_validate(row)
            for row in repo.list_relationships_for_household(conn, household_id)
        ],
    )


def _matter_household_id(conn: Any, actor_id: UUID, matter_id: UUID) -> tuple[MatterScope, UUID]:
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    return scope, UUID(str(scope.matter["household_id"]))


def create_person(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    payload: PersonCreate,
    correlation_id: UUID | None = None,
) -> PersonOut:
    """Add a person to a matter's household."""
    actor_id = _user_id_or_raise(actor_sub)
    scope, household_id = _matter_household_id(conn, actor_id, matter_id)
    _require_household_write(scope)
    row = repo.insert_person(
        conn,
        household_id=household_id,
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        email=payload.email,
        kind=payload.kind,
        notes=payload.notes,
    )
    record_audit_event(
        conn,
        action="persons.created",
        resource_type="person",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
    )
    return PersonOut.model_validate(row)


def patch_person(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    person_id: UUID,
    payload: PersonPatch,
    correlation_id: UUID | None = None,
) -> PersonOut:
    """Patch a person; the person must belong to the matter's household."""
    actor_id = _user_id_or_raise(actor_sub)
    scope, household_id = _matter_household_id(conn, actor_id, matter_id)
    _require_household_write(scope)
    person = repo.get_person(conn, person_id)
    if person is None or UUID(str(person["household_id"])) != household_id:
        raise NotFoundError("Person not found.")
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        person = repo.update_person(conn, person_id, fields)
        record_audit_event(
            conn,
            action="persons.updated",
            resource_type="person",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=UUID(str(scope.matter["firm_id"])),
            matter_id=matter_id,
            resource_id=person_id,
            correlation_id=correlation_id,
        )
    return PersonOut.model_validate(person)


def create_relationship(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    payload: RelationshipCreate,
    correlation_id: UUID | None = None,
) -> RelationshipOut:
    """Relate two persons; both must belong to the matter's household."""
    actor_id = _user_id_or_raise(actor_sub)
    scope, household_id = _matter_household_id(conn, actor_id, matter_id)
    _require_household_write(scope)
    if payload.from_person_id == payload.to_person_id:
        raise ValidationAppError("A person cannot be related to themselves.")
    from_person = repo.get_person(conn, payload.from_person_id)
    to_person = repo.get_person(conn, payload.to_person_id)
    for person in (from_person, to_person):
        if person is None or UUID(str(person["household_id"])) != household_id:
            raise NotFoundError("Person not found.")
    row = repo.insert_relationship(
        conn,
        household_id=household_id,
        from_person_id=payload.from_person_id,
        to_person_id=payload.to_person_id,
        relationship_type=payload.relationship_type,
    )
    record_audit_event(
        conn,
        action="relationships.created",
        resource_type="relationship",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
    )
    return RelationshipOut.model_validate(row)


def add_household_member(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    payload: HouseholdMemberCreate,
    correlation_id: UUID | None = None,
) -> HouseholdMemberOut:
    """Add a member row to a matter's household (firm staff+ only).

    The row starts unclaimed (``user_id`` NULL, ``access_status`` 'invited')
    unless ``email`` matches an existing profile, in which case the user is
    linked immediately.
    """
    actor_id = _user_id_or_raise(actor_sub)
    scope = authz.require_matter_firm_role(conn, matter_id, actor_id, authz.STAFF_ROLES)
    household_id = UUID(str(scope.matter["household_id"]))
    firm_id = UUID(str(scope.matter["firm_id"]))

    user_id: UUID | None = None
    if payload.email:
        profile = repo.find_profile_by_email(conn, payload.email)
        if profile is not None:
            user_id = UUID(str(profile["id"]))

    if payload.person_id is not None:
        person = repo.get_person(conn, payload.person_id)
        if person is None or UUID(str(person["household_id"])) != household_id:
            raise NotFoundError("Person not found.")

    row = repo.insert_household_member(
        conn,
        household_id=household_id,
        relationship_role=payload.relationship_role,
        access_status="active" if user_id is not None else "invited",
        user_id=user_id,
        person_id=payload.person_id,
        invited_by=actor_id,
    )
    record_audit_event(
        conn,
        action="household_members.created",
        resource_type="household_member",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
    )
    member_rows = repo.list_members_for_household(conn, household_id)
    created = next(r for r in member_rows if UUID(str(r["id"])) == UUID(str(row["id"])))
    return _member_out(created)
