"""Materialization syncs: interview answers should create the records the
rest of the app reads — minor children as household persons (Family &
Guardians showed 'No minors recorded' in E2E #3 despite the children being
named in the interview) and one asset row per selected asset category
(Assets showed 'No assets yet'). Parsers are conservative: anything that
doesn't clearly parse is skipped, never guessed."""
from __future__ import annotations

from app.domains.intake.service import _asset_specs_for_categories, _parse_minor_children


# --- minor children parsing -------------------------------------------------
def test_parses_parenthesized_ages():
    out = _parse_minor_children("Aarav Sharma (12), Diya Sharma (9), Kabir Sharma (6)")
    assert out == [("Aarav Sharma", 12), ("Diya Sharma", 9), ("Kabir Sharma", 6)]


def test_parses_age_word_form():
    out = _parse_minor_children("Aarav Sharma, age 12; Diya Sharma age 9")
    assert out == [("Aarav Sharma", 12), ("Diya Sharma", 9)]


def test_names_only_list():
    out = _parse_minor_children("Aarav Sharma, Diya Sharma and Kabir Sharma")
    assert out == [("Aarav Sharma", None), ("Diya Sharma", None), ("Kabir Sharma", None)]


def test_prose_is_not_parsed_into_people():
    assert _parse_minor_children("Three wonderful kids who love soccer and reading") == []
    assert _parse_minor_children("We have two children") == []


def test_adult_ages_are_not_minors():
    assert _parse_minor_children("Raj Sharma (41), Priya Sharma (38)") == []


def test_dedupes_repeat_mentions():
    out = _parse_minor_children("Aarav Sharma (12). Aarav Sharma (12)")
    assert out == [("Aarav Sharma", 12)]


# --- asset category mapping ---------------------------------------------------
QUESTION = {
    "key": "asset_categories_present",
    "options": [
        {"value": "bank", "label": "Bank accounts"},
        {"value": "brokerage", "label": "Brokerage / investment accounts"},
        {"value": "real_property", "label": "Real estate"},
        {"value": "529", "label": "529 education accounts"},
        {"value": "digital", "label": "Digital assets"},
    ],
}


def test_category_specs_use_pack_labels_and_db_types():
    specs = _asset_specs_for_categories(QUESTION, ["bank", "real_property", "529"])
    assert specs == [
        ("bank_account", "Bank accounts"),
        ("real_estate", "Real estate"),
        ("other", "529 education accounts"),
    ]


def test_unknown_category_is_skipped():
    assert _asset_specs_for_categories(QUESTION, ["crypto_lottery"]) == []


# --- wiring -------------------------------------------------------------------
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from app.domains.assets import repository as assets_repo
from app.domains.household import repository as household_repo
from app.domains.intake import service as intake_service


class _Cur:
    def execute(self, *_a, **_k):
        pass

    def fetchone(self):
        return None


class _Conn:
    @contextmanager
    def cursor(self):
        yield _Cur()


def test_minor_sync_creates_persons_once(monkeypatch):
    created = []
    monkeypatch.setattr(household_repo, "list_persons_for_household",
                        lambda c, hid: [{"full_name": "Raj Sharma", "kind": "adult"}])
    monkeypatch.setattr(household_repo, "insert_person",
                        lambda c, **kw: created.append(kw) or {"id": str(uuid4())})
    answers = {"has_minor_children": True,
               "minor_children_details": "Aarav Sharma (12), Diya Sharma (9)"}
    intake_service._sync_minors_from_answers(_Conn(), household_id=uuid4(), answers=answers)
    assert [(c["full_name"], c["kind"]) for c in created] == [
        ("Aarav Sharma", "minor"), ("Diya Sharma", "minor")]
    assert "Age 12" in created[0]["notes"]


def test_minor_sync_skips_when_no_minors(monkeypatch):
    created = []
    monkeypatch.setattr(household_repo, "list_persons_for_household", lambda c, hid: [])
    monkeypatch.setattr(household_repo, "insert_person",
                        lambda c, **kw: created.append(kw) or {})
    intake_service._sync_minors_from_answers(
        _Conn(), household_id=uuid4(),
        answers={"has_minor_children": False, "minor_children_details": "Aarav Sharma (12)"})
    assert created == []


def test_asset_sync_creates_rows_for_categories(monkeypatch):
    created = []
    monkeypatch.setattr(assets_repo, "list_assets_for_household",
                        lambda c, hid, page, page_size: ([], 0))
    monkeypatch.setattr(assets_repo, "insert_asset",
                        lambda c, **kw: created.append(kw) or {"id": str(uuid4())})
    pack = SimpleNamespace(question_sets={
        "assets": {"sections": [{"id": "o", "questions": [QUESTION]}]}})
    intake_service._sync_assets_from_answers(
        _Conn(), firm_id=uuid4(), household_id=uuid4(), matter_id=uuid4(),
        actor_id=uuid4(),
        answers={"asset_categories_present": ["bank", "digital"]}, pack=pack)
    assert [(c["asset_type"], c["label"]) for c in created] == [
        ("bank_account", "Bank accounts"), ("other", "Digital assets")]
    assert all(c["beneficiary_designation"] == {} for c in created)


# --- parser hardening (diff-review findings) ----------------------------------
def test_descriptor_lead_ins_are_stripped():
    assert _parse_minor_children("Son Aarav Sharma (12)") == [("Aarav Sharma", 12)]
    assert _parse_minor_children("Twins Aarav Sharma (12) and Diya Sharma (9)") == [
        ("Aarav Sharma", 12), ("Diya Sharma", 9)]
    assert _parse_minor_children("Stepdaughter Diya Kapoor age 9 lives with us") == [
        ("Diya Kapoor", 9)]
    assert _parse_minor_children("Dr. Aarav Sharma") == [("Aarav Sharma", None)]


def test_negatives_never_become_people():
    assert _parse_minor_children("No") == []
    assert _parse_minor_children("None") == []


def test_ageless_sibling_is_not_dropped():
    assert _parse_minor_children("Aarav Sharma (12), Diya Sharma") == [
        ("Aarav Sharma", 12), ("Diya Sharma", None)]


# --- idempotence / existing-row wiring ----------------------------------------
def test_minor_sync_second_save_creates_nothing(monkeypatch):
    created = []
    state = {"persons": []}
    monkeypatch.setattr(household_repo, "list_persons_for_household",
                        lambda c, hid: list(state["persons"]))

    def fake_insert(c, **kw):
        created.append(kw)
        state["persons"].append({"full_name": kw["full_name"], "kind": kw["kind"]})
        return {"id": str(uuid4())}

    monkeypatch.setattr(household_repo, "insert_person", fake_insert)
    answers = {"has_minor_children": True,
               "minor_children_details": "Aarav Sharma (12)"}
    intake_service._sync_minors_from_answers(_Conn(), household_id=uuid4(), answers=answers)
    intake_service._sync_minors_from_answers(_Conn(), household_id=uuid4(), answers=answers)
    assert len(created) == 1


def test_asset_sync_skips_existing_rows(monkeypatch):
    created = []
    monkeypatch.setattr(
        assets_repo, "list_assets_for_household",
        lambda c, hid, page, page_size: (
            [{"asset_type": "bank_account", "label": "Bank accounts"}], 1))
    monkeypatch.setattr(assets_repo, "insert_asset",
                        lambda c, **kw: created.append(kw) or {"id": str(uuid4())})
    pack = SimpleNamespace(question_sets={
        "assets": {"sections": [{"id": "o", "questions": [QUESTION]}]}})
    intake_service._sync_assets_from_answers(
        _Conn(), firm_id=uuid4(), household_id=uuid4(), matter_id=uuid4(),
        actor_id=uuid4(),
        answers={"asset_categories_present": ["bank", "digital"]}, pack=pack)
    assert [(c["asset_type"], c["label"]) for c in created] == [("other", "Digital assets")]


class _RecordingCur:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *_a, **_k):
        self._conn.statements.append(str(sql))

    def fetchone(self):
        return None


class _RecordingConn:
    def __init__(self):
        self.statements = []

    @contextmanager
    def cursor(self):
        yield _RecordingCur(self)


def test_minor_sync_rolls_back_to_savepoint_on_db_error(monkeypatch):
    monkeypatch.setattr(household_repo, "list_persons_for_household", lambda c, hid: [])

    def boom(c, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(household_repo, "insert_person", boom)
    conn = _RecordingConn()
    # Must not raise (best-effort contract) and must roll back to its savepoint.
    intake_service._sync_minors_from_answers(
        conn, household_id=uuid4(),
        answers={"has_minor_children": True, "minor_children_details": "Aarav Sharma (12)"})
    assert any("ROLLBACK TO SAVEPOINT lf_minor_sync" in s for s in conn.statements)


def test_more_descriptor_variants_are_stripped():
    cases = [
        ("Step-son Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Step Daughter Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Foster Son Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Adopted Daughter Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("First Child Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Grandson Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Twin Brother Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("No Minor Children", []),
    ]
    for text, expected in cases:
        assert _parse_minor_children(text) == expected, text


def test_explicit_adult_is_not_readded_ageless():
    assert _parse_minor_children("Aarav Sharma (18), Aarav Sharma") == []
    assert _parse_minor_children(
        "Raj Sharma (41) and Priya Sharma (38), kids Aarav Sharma (12)"
    ) == [("Aarav Sharma", 12)]


def test_round3_descriptor_variants():
    cases = [
        ("Half Sister Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Half-Sister Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Older Brother Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Younger Sister Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Big Brother Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Special Needs Son Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Special Needs Daughter Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Our Daughters Diya Sharma and Anaya Sharma",
         [("Diya Sharma", None), ("Anaya Sharma", None)]),
        ("Our Sons Aarav Sharma and Kabir Sharma",
         [("Aarav Sharma", None), ("Kabir Sharma", None)]),
        ("Our sons Aarav Sharma and Kabir Sharma",
         [("Aarav Sharma", None), ("Kabir Sharma", None)]),
        ("Only Child Diya Sharma", [("Diya Sharma", None)]),
        ("José García (10)", [("José García", 10)]),
        ("Noah Sharma (8)", [("Noah Sharma", 8)]),
    ]
    for text, expected in cases:
        assert _parse_minor_children(text) == expected, text


def test_round4_descriptor_variants():
    cases = [
        ("Elder Daughter Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Elder Son Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Middle Child Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Young Son Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("First-born Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Second-born Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Godson Aarav Sharma (12)", [("Aarav Sharma", 12)]),
        ("Goddaughter Diya Sharma (9)", [("Diya Sharma", 9)]),
        ("Newborn Daughter Diya Sharma (1)", [("Diya Sharma", 1)]),
        ("Adoptive Son Aarav Sharma (12)", [("Aarav Sharma", 12)]),
    ]
    for text, expected in cases:
        assert _parse_minor_children(text) == expected, text
