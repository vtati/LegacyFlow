"""Application services for the nominations domain (Slice 2).

Nominations record who the client nominates for plan roles (guardian,
executor, trustee, ...). Creation is allowed for firm staff+ and for
household primary_client/collaborator members; deletion is restricted to
firm staff+ and the primary_client.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.authz import MatterScope
from app.core.errors import AuthError, AuthorizationError, NotFoundError
from app.domains.nominations import repository as repo
from app.domains.nominations.schemas import NominationCreate, NominationOut

HOUSEHOLD_WRITER_ROLES = {"primary_client", "collaborator"}


def _user_id_or_raise(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


def _require_nominate(scope: MatterScope) -> None:
    if scope.via == "firm" and scope.firm_role in authz.STAFF_ROLES:
        return
    if scope.via == "household" and scope.household_role in HOUSEHOLD_WRITER_ROLES:
        return
    raise AuthorizationError(
        "This action requires a firm staff role or the primary_client/collaborator household role."
    )


def _require_delete(scope: MatterScope) -> None:
    if scope.via == "firm" and scope.firm_role in authz.STAFF_ROLES:
        return
    if scope.via == "household" and scope.household_role == "primary_client":
        return
    raise AuthorizationError(
        "Deleting a nomination requires a firm staff role or the primary_client household role."
    )


def create_nomination(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    payload: NominationCreate,
    correlation_id: UUID | None = None,
) -> NominationOut:
    """Create a nomination on a matter.

    The nominated person must belong to the matter's household.
    """
    actor_id = _user_id_or_raise(actor_sub)
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    _require_nominate(scope)
    household_id = UUID(str(scope.matter["household_id"]))

    person = repo.get_person(conn, payload.person_id)
    if person is None or UUID(str(person["household_id"])) != household_id:
        raise NotFoundError("Person not found.")

    row = repo.insert_nomination(
        conn,
        matter_id=matter_id,
        nomination_type=payload.nomination_type,
        person_id=payload.person_id,
        priority=payload.priority,
        notes=payload.notes,
    )
    record_audit_event(
        conn,
        action="nominations.created",
        resource_type="nomination",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=UUID(str(row["id"])),
        correlation_id=correlation_id,
        metadata={"nomination_type": payload.nomination_type},
    )
    return NominationOut.model_validate(row)


def list_nominations(
    conn: Any, *, actor_sub: str, matter_id: UUID, nomination_type: str | None = None
) -> list[NominationOut]:
    """List a matter's nominations ordered by type, then priority."""
    actor_id = _user_id_or_raise(actor_sub)
    authz.get_matter_scope(conn, matter_id, actor_id)
    rows = repo.list_nominations(conn, matter_id, nomination_type)
    return [NominationOut.model_validate(row) for row in rows]


def delete_nomination(
    conn: Any,
    *,
    actor_sub: str,
    matter_id: UUID,
    nomination_id: UUID,
    correlation_id: UUID | None = None,
) -> None:
    """Delete a nomination (firm staff+ or primary_client)."""
    actor_id = _user_id_or_raise(actor_sub)
    scope = authz.get_matter_scope(conn, matter_id, actor_id)
    _require_delete(scope)
    nomination = repo.get_nomination(conn, nomination_id)
    if nomination is None or UUID(str(nomination["matter_id"])) != matter_id:
        raise NotFoundError("Nomination not found.")
    repo.delete_nomination(conn, nomination_id)
    record_audit_event(
        conn,
        action="nominations.deleted",
        resource_type="nomination",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=nomination_id,
        correlation_id=correlation_id,
        metadata={"nomination_type": str(nomination["nomination_type"])},
    )
