"""R1 regression: document render context must resolve person references.

Production symptoms (2026-10-27):
1. Person answers stored as a person ID (wizard/Review person-select)
   rendered verbatim as a raw UUID in POA / healthcare / guardian slots,
   while interview-stored answers hold the person's NAME.
2. ``primary_executor_address`` / ``primary_guardian_address`` answers
   claimed the ``executor_name`` / ``guardian_name`` slots (substring +
   setdefault mapping over unordered answers) — a generated Will named
   "Austin, Texas" as executor and "123 Main Street" as guardian.
3. ``spouse_name`` was positional (second adult by created_at): with
   creation order testator -> guardian -> spouse, the Will printed
   "I am married to <guardian>".

Fixtures create persons in ADVERSARIAL order (testator, guardian,
spouse, everyone else) and exercise BOTH answer shapes per slot, then
the mirror fixture with the shapes swapped.

All fixture data is synthetic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import STORAGE_BUCKETS_STUB

from app.domains.documents import service as documents_service
from app.domains.intake import service as intake_service

CLIENT = "d3333333-3333-3333-3333-333333333333"
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " feature_flags, attorney_applications, review_packets, approval_gates,"
    " comments, clarification_requests,"
    " asset_flags, assets, liabilities, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
""" + STORAGE_BUCKETS_STUB

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
TEMPLATES_DIR = (
    Path(__file__).resolve().parents[1]
    / "content-packs" / "templates" / "us-va" / "v0"
)

UUID_SHAPE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

# Role persons. Creation order is adversarial: the guardian is created
# BEFORE the spouse, so any positional spouse/testator logic breaks.
TESTATOR = "Raj Sharma"
GUARDIAN = "Amit Sharma"
SPOUSE = "Neha Kapoor"
EXECUTOR = "Priya Nair"
BACKUP_EXECUTOR = "Marcus Webb"
BACKUP_GUARDIAN = "Sara Lindqvist"
SUCCESSOR_HEALTHCARE = "Tom Becker"
SUCCESSOR_POA = "Laura Chen"
SUCCESSOR_TRUSTEE = "George Osei"
CHILD = "Aarav Sharma"

PERSON_ORDER = [
    TESTATOR, GUARDIAN, SPOUSE, EXECUTOR, BACKUP_EXECUTOR,
    BACKUP_GUARDIAN, SUCCESSOR_HEALTHCARE, SUCCESSOR_POA, SUCCESSOR_TRUSTEE,
]

EXECUTOR_ADDRESS = "Austin, Texas"
GUARDIAN_ADDRESS = "123 Main Street"


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-person-render")))
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute(SEED_FILE.read_text())
    yield uri
    server.cleanup()


@pytest.fixture()
def db(pg_uri: str):
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _one(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def build_world(db, order=None):
    """Firm + household + matter + the CLIENT user as primary_client.

    Returns (household_id, matter_id, {full_name: person_id}) with the
    persons inserted in adversarial order at explicit, increasing
    created_at stamps so ORDER BY created_at is deterministic.
    ``order`` overrides PERSON_ORDER (creation order only — the
    primary_client member always points at the TESTATOR person).
    """
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
          (CLIENT, "client@example.test"))
    _exec(db, "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
              " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
          (CLIENT, "client@example.test", "Synthetic Client"))
    firm_id = str(uuid4())
    _exec(db, "INSERT INTO firms (id, legal_name, display_name, status)"
              " VALUES (%s, %s, %s, 'active')", (firm_id, "Synthetic Firm", "Synthetic Firm"))
    household_id = str(uuid4())
    _exec(db, "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
              " VALUES (%s, %s, %s, %s)",
          (household_id, firm_id, "Sharma Household", CLIENT))
    person_ids: dict[str, str] = {}
    for idx, name in enumerate(order or PERSON_ORDER):
        pid = str(uuid4())
        person_ids[name] = pid
        _exec(
            db,
            "INSERT INTO persons (id, household_id, full_name, kind, created_at)"
            " VALUES (%s, %s, %s, 'adult', %s)",
            (pid, household_id, name, f"2026-01-01T00:00:{idx:02d}Z"),
        )
    child_id = str(uuid4())
    person_ids[CHILD] = child_id
    _exec(db, "INSERT INTO persons (id, household_id, full_name, kind, created_at)"
              " VALUES (%s, %s, %s, 'minor', '2026-01-01T00:01:00Z')",
          (child_id, household_id, CHILD))
    # The testator is the household's primary_client member.
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, %s, 'primary_client', 'active')",
          (household_id, CLIENT, person_ids[TESTATOR]))
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "2.0.0"}
    )
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
        " completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 0)",
        (matter_id, firm_id, household_id, US_VA_JURISDICTION_ID, US_VA_PACK_ID,
         "Sharma Estate Plan", snapshot),
    )
    return household_id, matter_id, person_ids


def set_answer(db, matter_id: str, key: str, value) -> None:
    _exec(
        db,
        "INSERT INTO intake_answers (matter_id, question_key, answer)"
        " VALUES (%s, %s, %s::jsonb)"
        " ON CONFLICT (matter_id, question_key) DO UPDATE SET answer = EXCLUDED.answer",
        (matter_id, key, json.dumps(value)),
    )


def set_answers(db, matter_id: str, answers: dict) -> None:
    for key, value in answers.items():
        set_answer(db, matter_id, key, value)


def answers_shape_a(ids: dict[str, str]) -> dict:
    """ID shape for poa / healthcare / backup-guardian; name shape else."""
    return {
        "testator_full_name": TESTATOR,
        "primary_executor_person": EXECUTOR,
        "primary_executor_address": EXECUTOR_ADDRESS,
        "backup_executor_person": BACKUP_EXECUTOR,
        "primary_guardian_person": GUARDIAN,
        "primary_guardian_address": GUARDIAN_ADDRESS,
        "backup_guardian_person": ids[BACKUP_GUARDIAN],
        "healthcare_agent_person": ids[SPOUSE],
        "healthcare_agent_phone": "555-0100",
        "successor_healthcare_agent_person": SUCCESSOR_HEALTHCARE,
        "living_will_directives": "No heroic measures",
        "healthcare_agent_limitations": "None stated",
        "organ_donation_wishes": "Donate any needed organs",
        "poa_agent_person": ids[SPOUSE],
        "poa_agent_address": "44 Oak Avenue",
        "successor_poa_agent_person": SUCCESSOR_POA,
        "poa_additional_powers": "Banking powers",
        "successor_trustee_person": SUCCESSOR_TRUSTEE,
        "trust_initial_property": "$10 and household effects",
        "trust_distribution_age": "25",
        "trust_marital_share": "Outright",
        "residuary_beneficiaries": SPOUSE,
        "specific_bequests": "Watch to Amit Sharma",
        "testator_street_address": "9 Elm Street",
        "testator_city": "Richmond",
        "guardian_relationship": "Brother",
        "children_upbringing_wishes": "Loving home",
        "children_education_wishes": "Public school",
        "children_religious_wishes": "Family traditions",
        "children_financial_provisions": "College fund",
        "children_extended_family": "Kapoor grandparents",
    }


def answers_shape_b(ids: dict[str, str]) -> dict:
    """Mirror of shape A: the shapes are swapped per slot."""
    answers = answers_shape_a(ids)
    answers.update({
        "primary_executor_person": ids[EXECUTOR],
        "backup_executor_person": ids[BACKUP_EXECUTOR],
        "primary_guardian_person": ids[GUARDIAN],
        "backup_guardian_person": BACKUP_GUARDIAN,
        "healthcare_agent_person": SPOUSE,
        "successor_healthcare_agent_person": ids[SUCCESSOR_HEALTHCARE],
        "poa_agent_person": SPOUSE,
        "successor_poa_agent_person": ids[SUCCESSOR_POA],
        "successor_trustee_person": ids[SUCCESSOR_TRUSTEE],
    })
    return answers


EXPECTED_SLOTS = {
    "testator_name": TESTATOR,
    "declarant_name": TESTATOR,
    "principal_name": TESTATOR,
    "grantor_name": TESTATOR,
    "parent_name": TESTATOR,
    "trustee_name": TESTATOR,
    "trust_name": "The Sharma Family Revocable Trust",
    "executor_name": EXECUTOR,
    "executor_address": EXECUTOR_ADDRESS,
    "alternate_executor_name": BACKUP_EXECUTOR,
    "guardian_name": GUARDIAN,
    "guardian_address": GUARDIAN_ADDRESS,
    "alternate_guardian_name": BACKUP_GUARDIAN,
    "healthcare_agent_name": SPOUSE,
    "organ_donation_agent": SPOUSE,
    "healthcare_agent_phone": "555-0100",
    "successor_healthcare_agent_name": SUCCESSOR_HEALTHCARE,
    "successor_healthcare_agent_address": "___",
    "successor_healthcare_agent_phone": "___",
    "agent_name": SPOUSE,
    "agent_address": "44 Oak Avenue",
    "successor_agent_name": SUCCESSOR_POA,
    "successor_trustee_name": SUCCESSOR_TRUSTEE,
    "guardian_relationship": "Brother",
    "residuary_beneficiaries": SPOUSE,
    "specific_bequests": "Watch to Amit Sharma",
    "living_will_directives": "No heroic measures",
    "healthcare_agent_limitations": "None stated",
    "anatomical_gift_directions": "Donate any needed organs",
    "additional_powers": "Banking powers",
    "initial_property": "$10 and household effects",
    "distribution_age": "25",
    "marital_share": "Outright",
    "testator_address": "9 Elm Street",
    "declarant_address": "9 Elm Street",
    "principal_address": "9 Elm Street",
    "grantor_address": "9 Elm Street",
    "parent_address": "9 Elm Street",
    "testator_city": "Richmond",
    "declarant_city": "Richmond",
    "principal_city": "Richmond",
    "grantor_city": "Richmond",
    "parent_city": "Richmond",
    "upbringing_wishes": "Loving home",
    "education_wishes": "Public school",
    "religious_wishes": "Family traditions",
    "financial_provisions": "College fund",
    "extended_family": "Kapoor grandparents",
    "children_list": CHILD,
}

NAME_SLOTS = [k for k in EXPECTED_SLOTS if k.endswith("_name") or k == "organ_donation_agent"]


def assert_context_correct(ctx: dict) -> None:
    for slot, expected in EXPECTED_SLOTS.items():
        assert ctx.get(slot) == expected, f"{slot}: {ctx.get(slot)!r} != {expected!r}"
    # No UUID-shaped string anywhere in the render context values.
    for key, value in ctx.items():
        assert not UUID_SHAPE.search(str(value)), f"UUID leaked into context[{key!r}]"
    # No address string in any name slot.
    for slot in NAME_SLOTS:
        value = str(ctx.get(slot, ""))
        assert EXECUTOR_ADDRESS not in value and GUARDIAN_ADDRESS not in value, slot


def render(db, matter_id: str, template_file: str) -> str:
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    body = (TEMPLATES_DIR / template_file).read_text(encoding="utf-8")
    return documents_service.render_template_content(body, ctx)


# ---------------------------------------------------------------------------
# Context matrix — both shapes, with a relationships spouse signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["a", "b"])
def test_context_resolves_both_answer_shapes(db, shape):
    household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids) if shape == "a" else answers_shape_b(ids)
    set_answers(db, matter_id, answers)
    _exec(db, "INSERT INTO relationships"
              " (household_id, from_person_id, to_person_id, relationship_type)"
              " VALUES (%s, %s, %s, 'spouse')",
          (household_id, ids[TESTATOR], ids[SPOUSE]))
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert_context_correct(ctx)
    # Spouse comes from the relationships signal — the spouse person,
    # never the guardian who was created before her.
    assert ctx["spouse_name"] == SPOUSE


@pytest.mark.parametrize("shape", ["a", "b"])
def test_context_spouse_unset_without_any_signal(db, shape):
    _household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids) if shape == "a" else answers_shape_b(ids)
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert_context_correct(ctx)
    # No spouse signal exists: the slot stays unset so the merged body
    # blanks it (___) and the unfilled-placeholder escalation fires —
    # it must NOT fall back to the second-created adult (the guardian).
    assert "spouse_name" not in ctx


def test_context_spouse_from_household_member_role(monkeypatch):
    """Member-role spouse signal, unit-style: the live schema's CHECK on
    household_members.relationship_role currently only allows
    primary_client/collaborator/dependent/trusted_contact, so a 'spouse'
    member row cannot be inserted against pg — the resolver still honors
    the signal for when the role set grows (defensive read)."""
    from contextlib import contextmanager

    from app.domains.household import repository as household_repo
    from app.domains.intake import repository as intake_repo

    class _Cursor:
        def __init__(self, row):
            self._row = row

        def execute(self, *_a, **_k):
            pass

        def fetchone(self):
            return self._row

    class _Conn:
        def __init__(self, row):
            self._row = row

        @contextmanager
        def cursor(self):
            yield _Cursor(self._row)

    matter_id = uuid4()
    household_id = uuid4()
    ids = {name: str(uuid4()) for name in PERSON_ORDER}
    persons = [
        {"id": ids[name], "kind": "adult", "full_name": name, "date_of_birth": None}
        for name in PERSON_ORDER
    ]
    conn = _Conn({"id": str(matter_id), "household_id": str(household_id)})
    monkeypatch.setattr(documents_service, "_render_context", lambda c, m, answers=None: {})
    monkeypatch.setattr(household_repo, "list_persons_for_household",
                        lambda c, hid: persons)
    monkeypatch.setattr(household_repo, "list_members_for_household",
                        lambda c, hid: [
                            {"relationship_role": "primary_client",
                             "person_id": ids[TESTATOR]},
                            {"relationship_role": "spouse",
                             "person_id": ids[SPOUSE]},
                        ])
    monkeypatch.setattr(household_repo, "list_relationships_for_household",
                        lambda c, hid: [])
    monkeypatch.setattr(intake_repo, "list_answers", lambda c, mid: [])
    ctx = documents_service.build_template_context(conn, matter_id)
    assert ctx["testator_name"] == TESTATOR
    assert ctx["spouse_name"] == SPOUSE


def test_context_testator_from_primary_client_member(db):
    """No testator_full_name answer: the primary_client member's person
    wins over the first-created adult fallback ordering."""
    _household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids)
    del answers["testator_full_name"]
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["testator_name"] == TESTATOR
    assert ctx["principal_name"] == TESTATOR


# ---------------------------------------------------------------------------
# Merged body text — Will + POA + Guardianship
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["a", "b"])
def test_merged_bodies_have_names_not_ids_or_addresses(db, shape):
    household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids) if shape == "a" else answers_shape_b(ids)
    set_answers(db, matter_id, answers)
    _exec(db, "INSERT INTO relationships"
              " (household_id, from_person_id, to_person_id, relationship_type)"
              " VALUES (%s, %s, %s, 'spouse')",
          (household_id, ids[TESTATOR], ids[SPOUSE]))

    will = render(db, matter_id, "last-will-and-testament.txt")
    assert f"I am married to {SPOUSE}." in will
    assert f"I appoint {EXECUTOR}, of {EXECUTOR_ADDRESS}, as Executor" in will
    assert f"I nominate {GUARDIAN}, of {GUARDIAN_ADDRESS}, as guardian" in will
    assert f"I nominate {BACKUP_GUARDIAN} as successor guardian" in will

    poa = render(db, matter_id, "durable-power-of-attorney.txt")
    assert f"PRINCIPAL: {TESTATOR}," in poa
    assert f"I appoint {SPOUSE}, of 44 Oak Avenue, as my agent" in poa
    assert f"I appoint {SUCCESSOR_POA} as successor Agent" in poa

    guardianship = render(db, matter_id, "guardianship-designation.txt")
    assert f"Guardian: {GUARDIAN}" in guardianship
    assert f"Address: {GUARDIAN_ADDRESS}" in guardianship
    assert f"Successor Guardian: {BACKUP_GUARDIAN}" in guardianship

    for body in (will, poa, guardianship):
        assert not UUID_SHAPE.search(body)
        assert "{{" not in body


def test_merged_will_no_marital_assertion_without_signal(db):
    """No spouse signal AND no marital_status answer: the Will must not
    assert a marriage it has no evidence for (pre-fix it printed
    "I am married to ___." for every testator, married or not — the
    2026-10-27 Lee defect). It must NEVER name the guardian as spouse."""
    _household_id, matter_id, ids = build_world(db)
    set_answers(db, matter_id, answers_shape_a(ids))
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "married to" not in will
    assert f"married to {GUARDIAN}" not in will


# ---------------------------------------------------------------------------
# Intake nomination sync — ID-shaped values must not create UUID persons
# ---------------------------------------------------------------------------


def test_save_answers_id_shaped_person_value_reuses_existing_person(db):
    _household_id, matter_id, ids = build_world(db)
    before = _one(db, "SELECT count(*) AS n FROM persons")["n"]
    intake_service.save_answers(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id),
        answers={"poa_agent_person": ids[SPOUSE]},
    )
    db.commit()
    after = _all(db, "SELECT full_name FROM persons")
    assert len(after) == before
    assert not any(UUID_SHAPE.fullmatch(str(r["full_name"]).strip()) for r in after)
    nominations = _all(
        db, "SELECT person_id FROM nominations WHERE matter_id = %s", (matter_id,)
    )
    assert nominations, "expected an auto-created nomination"
    assert any(str(n["person_id"]) == ids[SPOUSE] for n in nominations)


def build_world_duplicate_names(db, *, spouse_link: str):
    """Two adults sharing ONE name; the real testator is the second one.

    The unrelated same-name adult is created FIRST; the real testator is
    created second and is the household's primary_client member. The
    spouse relationship row hangs off ``spouse_link`` ("testator" or
    "impostor"). Returns (household_id, matter_id, ids) where ids has
    keys "impostor", "testator", "spouse".
    """
    dup_name = "Sam Lee"
    spouse_name = "Jordan Lee"
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
          (CLIENT, "client@example.test"))
    _exec(db, "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
              " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
          (CLIENT, "client@example.test", "Synthetic Client"))
    firm_id = str(uuid4())
    _exec(db, "INSERT INTO firms (id, legal_name, display_name, status)"
              " VALUES (%s, %s, %s, 'active')", (firm_id, "Synthetic Firm", "Synthetic Firm"))
    household_id = str(uuid4())
    _exec(db, "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
              " VALUES (%s, %s, %s, %s)",
          (household_id, firm_id, "Lee Household", CLIENT))
    ids = {"impostor": str(uuid4()), "testator": str(uuid4()), "spouse": str(uuid4())}
    for idx, (key, name) in enumerate([
        ("impostor", dup_name), ("testator", dup_name), ("spouse", spouse_name),
    ]):
        _exec(
            db,
            "INSERT INTO persons (id, household_id, full_name, kind, created_at)"
            " VALUES (%s, %s, %s, 'adult', %s)",
            (ids[key], household_id, name, f"2026-01-01T00:00:{idx:02d}Z"),
        )
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, %s, 'primary_client', 'active')",
          (household_id, CLIENT, ids["testator"]))
    linked = ids["testator"] if spouse_link == "testator" else ids["impostor"]
    _exec(db, "INSERT INTO relationships"
              " (household_id, from_person_id, to_person_id, relationship_type)"
              " VALUES (%s, %s, %s, 'spouse')",
          (household_id, linked, ids["spouse"]))
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "2.0.0"}
    )
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
        " completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 0)",
        (matter_id, firm_id, household_id, US_VA_JURISDICTION_ID, US_VA_PACK_ID,
         "Lee Estate Plan", snapshot),
    )
    return household_id, matter_id, ids


# ---------------------------------------------------------------------------
# I-1: unmatched UUID-shaped person values must never print
# ---------------------------------------------------------------------------


def test_context_foreign_uuid_person_value_is_dropped(db):
    _household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids)
    foreign = str(uuid4())
    answers["primary_executor_person"] = foreign
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    # Dropped = unset: the slot is absent so the merge blanks it to ___
    # and the unfilled-placeholder escalation records it.
    assert "executor_name" not in ctx
    assert ctx.get("primary_executor_person") != foreign
    for key, value in ctx.items():
        assert not UUID_SHAPE.search(str(value)), f"UUID leaked into context[{key!r}]"
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert f"I appoint ___, of {EXECUTOR_ADDRESS}, as Executor" in will
    assert not UUID_SHAPE.search(will)


def test_context_uuid_of_person_outside_household_is_dropped(db):
    _household_id, matter_id, ids = build_world(db)
    # A real person UUID that belongs to a DIFFERENT household.
    other_firm = str(uuid4())
    _exec(db, "INSERT INTO firms (id, legal_name, display_name, status)"
              " VALUES (%s, %s, %s, 'active')", (other_firm, "Other Firm", "Other Firm"))
    other_household = str(uuid4())
    _exec(db, "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
              " VALUES (%s, %s, %s, %s)",
          (other_household, other_firm, "Other Household", CLIENT))
    outsider_id = str(uuid4())
    _exec(db, "INSERT INTO persons (id, household_id, full_name, kind)"
              " VALUES (%s, %s, %s, 'adult')",
          (outsider_id, other_household, "Out Sider"))
    answers = answers_shape_a(ids)
    answers["primary_executor_person"] = outsider_id
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert "executor_name" not in ctx
    for key, value in ctx.items():
        assert not UUID_SHAPE.search(str(value)), f"UUID leaked into context[{key!r}]"
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert not UUID_SHAPE.search(will)


def test_context_uppercase_uuid_of_existing_person_resolves(db):
    _household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids)
    answers["primary_executor_person"] = ids[EXECUTOR].upper()
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["executor_name"] == EXECUTOR
    for key, value in ctx.items():
        assert not UUID_SHAPE.search(str(value)), f"UUID leaked into context[{key!r}]"
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert f"I appoint {EXECUTOR}, of {EXECUTOR_ADDRESS}, as Executor" in will
    assert not UUID_SHAPE.search(will)


def test_context_unmatched_uuid_testator_answer_is_dropped(db):
    """M-1: testator_full_name is in the scrub set — an ID-shaped value
    under that key must never survive; the member chain takes over."""
    _household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids)
    foreign = str(uuid4())
    answers["testator_full_name"] = foreign
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx.get("testator_full_name") != foreign
    for key, value in ctx.items():
        assert not UUID_SHAPE.search(str(value)), f"UUID leaked into context[{key!r}]"
    # Dropped testator answer falls through to the primary_client member.
    assert ctx["testator_name"] == TESTATOR


# ---------------------------------------------------------------------------
# I-2: testator identity carried as a person id (duplicate-name households)
# ---------------------------------------------------------------------------


def test_context_duplicate_names_spouse_follows_real_testator(db):
    _household_id, matter_id, _ids = build_world_duplicate_names(
        db, spouse_link="testator"
    )
    set_answer(db, matter_id, "testator_full_name", "Sam Lee")
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["testator_name"] == "Sam Lee"
    assert ctx["spouse_name"] == "Jordan Lee"


def test_context_duplicate_names_spouse_of_namesake_not_picked(db):
    _household_id, matter_id, _ids = build_world_duplicate_names(
        db, spouse_link="impostor"
    )
    set_answer(db, matter_id, "testator_full_name", "Sam Lee")
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["testator_name"] == "Sam Lee"
    # The spouse row belongs to the same-name non-testator: no signal
    # exists for the actual testator, so the slot stays unset.
    assert "spouse_name" not in ctx


# ---------------------------------------------------------------------------
# I-3: testator member-chain proven, not coincidental with adults[0]
# ---------------------------------------------------------------------------


def test_context_testator_member_chain_with_guardian_created_first(db):
    order = [GUARDIAN, TESTATOR, SPOUSE, EXECUTOR, BACKUP_EXECUTOR,
             BACKUP_GUARDIAN, SUCCESSOR_HEALTHCARE, SUCCESSOR_POA,
             SUCCESSOR_TRUSTEE]
    _household_id, matter_id, ids = build_world(db, order=order)
    answers = answers_shape_a(ids)
    del answers["testator_full_name"]
    set_answers(db, matter_id, answers)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    # The guardian was created first, so adults[0] is the guardian; the
    # primary_client member (the testator) must still win everywhere.
    assert ctx["testator_name"] == TESTATOR
    assert ctx["principal_name"] == TESTATOR
    assert ctx["declarant_name"] == TESTATOR
    assert ctx["trust_name"] == "The Sharma Family Revocable Trust"


# ---------------------------------------------------------------------------
# Stretch: merged bodies — Trust + Advance Medical Directive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["a", "b"])
def test_merged_trust_and_amd_bodies(db, shape):
    household_id, matter_id, ids = build_world(db)
    answers = answers_shape_a(ids) if shape == "a" else answers_shape_b(ids)
    set_answers(db, matter_id, answers)
    _exec(db, "INSERT INTO relationships"
              " (household_id, from_person_id, to_person_id, relationship_type)"
              " VALUES (%s, %s, %s, 'spouse')",
          (household_id, ids[TESTATOR], ids[SPOUSE]))

    trust = render(db, matter_id, "revocable-living-trust.txt")
    assert "The Sharma Family Revocable Trust" in trust
    assert f"I, {TESTATOR}, of 9 Elm Street, Richmond, Virginia, as Grantor" in trust
    assert f"{SUCCESSOR_TRUSTEE} shall serve as successor Trustee" in trust
    assert "$10 and household effects" in trust
    assert CHILD in trust

    amd = render(db, matter_id, "advance-medical-directive.txt")
    assert f"DECLARANT: I, {TESTATOR}, of 9 Elm Street, Richmond, Virginia" in amd
    assert f"Agent: {SPOUSE}" in amd
    assert f"Successor Agent: {SUCCESSOR_HEALTHCARE}" in amd
    assert f"I appoint {SPOUSE} to make any anatomical gift" in amd
    assert "No heroic measures" in amd

    for body in (trust, amd):
        assert not UUID_SHAPE.search(body)
        assert "{{" not in body


def test_save_answers_name_shaped_person_value_still_syncs(db):
    _household_id, matter_id, _ids = build_world(db)
    intake_service.save_answers(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id),
        answers={"poa_agent_person": SPOUSE},
    )
    db.commit()
    nominations = _all(
        db,
        "SELECT p.full_name AS full_name FROM nominations n"
        " JOIN persons p ON p.id = n.person_id WHERE n.matter_id = %s",
        (matter_id,),
    )
    assert any(n["full_name"] == SPOUSE for n in nominations)
