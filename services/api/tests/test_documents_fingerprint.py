"""FUNC-022: the source fingerprint must cover relationships and
household-member links — build_template_context consumes both (spouse
resolution, testator link), so editing them must mark drafts stale.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.domains.documents import service as documents_service
from app.domains.household import repository as household_repo
from app.domains.intake import repository as intake_repo
from app.domains.nominations import repository as nominations_repo

HOUSEHOLD_ID = uuid4()
MATTER_ID = uuid4()
TESTATOR_PID = uuid4()
SPOUSE_PID = uuid4()
OTHER_PID = uuid4()
USER_ID = uuid4()


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        pass

    def fetchone(self):
        return {"display_name": "Synthetic Household"}


class _Conn:
    def cursor(self):
        return _Cursor()


def _base_state():
    return {
        "answers": [{"question_key": "marital_status", "answer": "married"}],
        "persons": [
            {"id": TESTATOR_PID, "full_name": "Testator", "kind": "adult", "date_of_birth": None},
            {"id": SPOUSE_PID, "full_name": "Spouse", "kind": "adult", "date_of_birth": None},
        ],
        "nominations": [],
        "relationships": [],
        "members": [
            {
                "user_id": USER_ID,
                "person_id": TESTATOR_PID,
                "relationship_role": "primary_client",
                "access_status": "active",
            }
        ],
    }


@pytest.fixture()
def state(monkeypatch):
    s = _base_state()
    monkeypatch.setattr(intake_repo, "list_answers", lambda conn, mid: s["answers"])
    monkeypatch.setattr(
        household_repo, "list_persons_for_household", lambda conn, hid: s["persons"]
    )
    monkeypatch.setattr(
        nominations_repo, "list_nominations", lambda conn, mid: s["nominations"]
    )
    monkeypatch.setattr(
        household_repo,
        "list_relationships_for_household",
        lambda conn, hid: s["relationships"],
    )
    monkeypatch.setattr(
        household_repo, "list_members_for_household", lambda conn, hid: s["members"]
    )
    return s


def _fp():
    matter = {"id": str(MATTER_ID), "household_id": str(HOUSEHOLD_ID), "title": "Matter"}
    return documents_service.compute_source_fingerprint(_Conn(), matter)


def _spouse_rel(from_pid, to_pid):
    return {
        "household_id": HOUSEHOLD_ID,
        "from_person_id": from_pid,
        "to_person_id": to_pid,
        "relationship_type": "spouse",
    }


def test_fingerprint_changes_when_spouse_relationship_added(state):
    before = _fp()
    state["relationships"] = [_spouse_rel(TESTATOR_PID, SPOUSE_PID)]
    assert _fp() != before


def test_fingerprint_changes_when_spouse_relationship_removed(state):
    state["relationships"] = [_spouse_rel(TESTATOR_PID, SPOUSE_PID)]
    before = _fp()
    state["relationships"] = []
    assert _fp() != before


def test_fingerprint_changes_when_spouse_relationship_repointed(state):
    state["relationships"] = [_spouse_rel(TESTATOR_PID, SPOUSE_PID)]
    before = _fp()
    state["relationships"] = [_spouse_rel(TESTATOR_PID, OTHER_PID)]
    assert _fp() != before


def test_fingerprint_changes_when_primary_client_person_changes(state):
    before = _fp()
    state["members"] = [
        {**state["members"][0], "person_id": OTHER_PID}
    ]
    assert _fp() != before


def test_fingerprint_stable_across_ordering_permutations(state):
    rel_a = _spouse_rel(TESTATOR_PID, SPOUSE_PID)
    rel_b = {
        "household_id": HOUSEHOLD_ID,
        "from_person_id": SPOUSE_PID,
        "to_person_id": OTHER_PID,
        "relationship_type": "parent",
    }
    member_b = {
        "user_id": uuid4(),
        "person_id": SPOUSE_PID,
        "relationship_role": "collaborator",
        "access_status": "active",
    }
    member_a = state["members"][0]
    state["relationships"] = [rel_a, rel_b]
    state["members"] = [member_a, member_b]
    first = _fp()
    state["relationships"] = [rel_b, rel_a]
    state["members"] = [member_b, member_a]
    assert _fp() == first


def test_fingerprint_changes_on_relationship_type_change(state):
    state["relationships"] = [_spouse_rel(TESTATOR_PID, SPOUSE_PID)]
    before = _fp()
    state["relationships"] = [
        {**_spouse_rel(TESTATOR_PID, SPOUSE_PID), "relationship_type": "partner"}
    ]
    assert _fp() != before
