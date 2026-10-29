"""FUNC-029 regression: person-create must not mint privileged member roles.

``PersonCreate.relationship_role`` feeds the auto-created household
membership link in ``household/service.create_person``. Allowing
``primary_client`` (or ``collaborator``) there lets any household writer
mint a second ``primary_client`` member row; several consumers assume
exactly one such row and take the first by ``created_at``, and the
testator sync links the testator onto *all* of them — corrupting
member/testator resolution. The auto-assignment vocabulary is therefore
limited to the non-privileged roles; ``primary_client`` membership is
created by household provisioning and ``collaborator`` membership by
invitation, never by adding a person record.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domains.household.schemas import PersonCreate


@pytest.mark.parametrize("role", ["primary_client", "collaborator", " Primary_Client ", "COLLABORATOR"])
def test_privileged_roles_rejected(role: str) -> None:
    with pytest.raises(ValidationError):
        PersonCreate(full_name="Second Client", kind="adult", relationship_role=role)


@pytest.mark.parametrize("role", ["dependent", "trusted_contact", " Dependent "])
def test_non_privileged_roles_accepted_and_normalized(role: str) -> None:
    payload = PersonCreate(full_name="A Person", kind="adult", relationship_role=role)
    assert payload.relationship_role == role.strip().lower()


def test_default_role_is_dependent() -> None:
    payload = PersonCreate(full_name="A Person", kind="minor")
    assert payload.relationship_role == "dependent"


def test_unknown_role_still_rejected() -> None:
    with pytest.raises(ValidationError):
        PersonCreate(full_name="A Person", kind="adult", relationship_role="spouse")
