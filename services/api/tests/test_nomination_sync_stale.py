"""Stale auto-nomination sweep: when an answer changes to a different person,
the earlier auto-created nomination from the SAME answer key must retire —
otherwise a matter ends up with two Priority-1 guardians (E2E #3: Priya +
Amit, both 'proposed', both sourced from primary_guardian_person).
Human-confirmed nominations are never swept."""
from __future__ import annotations

from app.domains.intake.service import _stale_auto_nominations

KEY = "primary_guardian_person"
NOTE = f"From interview answer: {KEY}"
AMIT = "11111111-1111-1111-1111-111111111111"
PRIYA = "22222222-2222-2222-2222-222222222222"


def row(person, *, notes=NOTE, status="proposed", ntype="guardian", priority=1, rid="r1"):
    return {"id": rid, "nomination_type": ntype, "person_id": person,
            "priority": priority, "notes": notes, "status": status}


def test_superseded_person_is_stale():
    stale = _stale_auto_nominations([row(PRIYA)], source_key=KEY, target=("guardian", 1, AMIT))
    assert [r["person_id"] for r in stale] == [PRIYA]


def test_current_target_is_kept():
    assert _stale_auto_nominations([row(AMIT)], source_key=KEY, target=("guardian", 1, AMIT)) == []


def test_confirmed_nomination_is_never_swept():
    assert _stale_auto_nominations(
        [row(PRIYA, status="confirmed")], source_key=KEY, target=("guardian", 1, AMIT)) == []


def test_other_source_key_is_kept():
    other = row(PRIYA, notes="From interview answer: backup_guardian_person")
    assert _stale_auto_nominations([other], source_key=KEY, target=("guardian", 1, AMIT)) == []


def test_manual_nomination_is_kept():
    manual = row(PRIYA, notes="Added by firm staff")
    assert _stale_auto_nominations([manual], source_key=KEY, target=("guardian", 1, AMIT)) == []


def test_priority_change_is_stale():
    stale = _stale_auto_nominations([row(AMIT, priority=2)], source_key=KEY, target=("guardian", 1, AMIT))
    assert len(stale) == 1


# --- wiring: the sync itself retires the superseded row -------------------
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from app.domains.household import repository as household_repo
from app.domains.intake import service as intake_service
from app.domains.nominations import repository as nom_repo


class _Cur:
    def execute(self, *_a, **_k):
        pass

    def fetchone(self):
        return None


class _Conn:
    @contextmanager
    def cursor(self):
        yield _Cur()


def test_sync_deletes_superseded_and_inserts_current(monkeypatch):
    amit_id = uuid4()
    priya_row = {"id": str(uuid4()), "nomination_type": "guardian",
                 "person_id": PRIYA, "priority": 1, "notes": NOTE, "status": "proposed"}
    deleted, inserted = [], []
    monkeypatch.setattr(household_repo, "list_persons_for_household",
                        lambda c, hid: [{"id": str(amit_id), "full_name": "Amit Sharma"}])
    monkeypatch.setattr(nom_repo, "list_nominations", lambda c, mid: [priya_row])
    monkeypatch.setattr(nom_repo, "delete_nomination", lambda c, nid: deleted.append(str(nid)))
    monkeypatch.setattr(nom_repo, "insert_nomination",
                        lambda c, **kw: inserted.append(kw) or {"id": str(uuid4())})
    pack = SimpleNamespace(question_sets={
        "guardians": {"sections": [{"id": "g", "questions": [
            {"key": KEY, "maps_to": "nominations.guardian.1"}]}]}})
    intake_service._sync_nominations_from_answers(
        _Conn(), matter_id=uuid4(), household_id=uuid4(), firm_id=uuid4(),
        actor_id=uuid4(), answers={KEY: "Amit Sharma"}, pack=pack)
    assert deleted == [priya_row["id"]]
    assert len(inserted) == 1 and str(inserted[0]["person_id"]) == str(amit_id)
