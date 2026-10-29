"""Spouse-relationship capture sync (2026-10-27 fix program follow-on).

The R1 renderer resolves the spouse ONLY from a ``relationships`` row
(type spouse/partner) involving the testator — deliberately, to end
positional guessing. But no capture path created that row: the v2
interview never wrote relationships, and household member roles are
CHECK-constrained away from spouse/partner. A married user completing
the interview end-to-end got a Will with a blank spouse slot.

The capture design pinned here:

* The us-va v2 pack asks ``spouse_full_name`` (required, identity
  cluster) gated on ``marital_status`` married/partnered — the
  interview prompt already licensed recording the spouse's name; it
  simply had no key to record it under.
* ``save_answers`` — the single funnel for interview-confirmed answers,
  wizard saves, and Review-screen edits — reconciles the household's
  spouse/partner relationship for the testator against the stored
  answers: create on capture, re-point on a name change, correct the
  type on a married<->partnered change, remove when marital status
  moves away, and stay idempotent across repeat saves.
* The renderer is NOT loosened: with no relationship signal the spouse
  slot still blanks (final test).

All fixture data is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import STORAGE_BUCKETS_STUB

from app.domains.documents import service as documents_service
from app.domains.household import repository as household_repo
from app.domains.intake import question_sets as qs
from app.domains.intake import service as intake_service
from app.domains.legal_content.pack_loader import get_pack

CLIENT = "e3333333-3333-3333-3333-333333333333"
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

TESTATOR = "Arjun Sharma"
SPOUSE = "Neha Kapoor"
SPOUSE_RENAMED = "Neha K. Kapoor"

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
    Path(__file__).resolve().parents[1] / "content-packs" / "templates" / "us-va" / "v0"
)


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-spouse-sync")))
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


def _all(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def build_world(db):
    """Firm + household + matter as household creation leaves them: the
    primary_client membership exists with NO person linked yet (the
    interview's identity capture creates/links the testator person).
    Returns (household_id, matter_id)."""
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
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, NULL, 'primary_client', 'active')",
          (household_id, CLIENT))
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
    return household_id, matter_id


def save(db, matter_id: str, answers: dict) -> None:
    intake_service.save_answers(
        db, actor_sub=CLIENT, matter_id=UUID(matter_id), answers=answers
    )
    db.commit()


def spouse_rows(db, household_id: str):
    return _all(
        db,
        "SELECT * FROM relationships WHERE household_id = %s"
        " AND relationship_type IN ('spouse', 'partner') ORDER BY created_at",
        (household_id,),
    )


def person_by_name(db, household_id: str, name: str):
    rows = _all(
        db,
        "SELECT * FROM persons WHERE household_id = %s AND full_name = %s",
        (household_id, name),
    )
    return rows[0] if rows else None


MARRIED_ANSWERS = {
    "testator_full_name": TESTATOR,
    "marital_status": "married",
    "spouse_full_name": SPOUSE,
}


# ---------------------------------------------------------------------------
# Capture: create / idempotent / re-point / type / removal
# ---------------------------------------------------------------------------


def test_married_save_creates_spouse_relationship(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    testator = person_by_name(db, household_id, TESTATOR)
    spouse = person_by_name(db, household_id, SPOUSE)
    assert testator is not None and spouse is not None
    assert str(rows[0]["from_person_id"]) == str(testator["id"])
    assert str(rows[0]["to_person_id"]) == str(spouse["id"])
    assert rows[0]["relationship_type"] == "spouse"


def test_repeat_saves_are_idempotent(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    first = spouse_rows(db, household_id)
    assert len(first) == 1
    # An unrelated answer save and a repeat of the same answers must
    # neither duplicate nor churn the row.
    save(db, matter_id, {"primary_residence_state": "Virginia"})
    save(db, matter_id, MARRIED_ANSWERS)
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == str(first[0]["id"])


def test_spouse_name_change_repoints_relationship(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    save(db, matter_id, {"spouse_full_name": SPOUSE_RENAMED})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    renamed = person_by_name(db, household_id, SPOUSE_RENAMED)
    assert renamed is not None
    assert str(rows[0]["to_person_id"]) == str(renamed["id"])


def test_marital_status_change_away_removes_relationship(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    assert len(spouse_rows(db, household_id)) == 1
    save(db, matter_id, {"marital_status": "single"})
    assert spouse_rows(db, household_id) == []


def test_single_from_start_creates_no_relationship(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, {"testator_full_name": TESTATOR, "marital_status": "single"})
    assert spouse_rows(db, household_id) == []
    assert person_by_name(db, household_id, SPOUSE) is None


def test_partnered_creates_partner_relationship(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, {
        "testator_full_name": TESTATOR,
        "marital_status": "partnered",
        "spouse_full_name": SPOUSE,
    })
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert rows[0]["relationship_type"] == "partner"


def test_married_type_corrects_to_partner_on_status_change(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    save(db, matter_id, {"marital_status": "partnered"})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert rows[0]["relationship_type"] == "partner"


def test_married_without_spouse_name_creates_nothing_yet(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, {"testator_full_name": TESTATOR, "marital_status": "married"})
    assert spouse_rows(db, household_id) == []
    save(db, matter_id, {"spouse_full_name": SPOUSE})
    assert len(spouse_rows(db, household_id)) == 1


def test_id_shaped_spouse_value_reuses_existing_person(db):
    """The Review person-select stores person IDs in answers (the R1
    both-shapes reality). After a name-shaped capture created both
    persons, an ID-shaped re-save must resolve to the same person —
    never mint a duplicate, never a person named by the UUID."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    spouse = person_by_name(db, household_id, SPOUSE)
    assert spouse is not None
    persons_before = len(_all(db, "SELECT * FROM persons WHERE household_id = %s",
                               (household_id,)))
    save(db, matter_id, {"spouse_full_name": str(spouse["id"])})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert str(rows[0]["to_person_id"]) == str(spouse["id"])
    persons_after = _all(db, "SELECT * FROM persons WHERE household_id = %s",
                         (household_id,))
    assert len(persons_after) == persons_before
    assert not any(
        str(p["full_name"]).strip() == str(spouse["id"]) for p in persons_after
    )


def test_spouse_person_rename_flows_through_the_same_row(db):
    """The Review persons editor renames the person; the relationship
    row points at the person ID, so the render name follows with no
    new row and no re-point."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    rows = spouse_rows(db, household_id)
    spouse = person_by_name(db, household_id, SPOUSE)
    household_repo.update_person(db, UUID(str(spouse["id"])), {"full_name": SPOUSE_RENAMED})
    db.commit()
    rows_after = spouse_rows(db, household_id)
    assert len(rows_after) == 1
    assert str(rows_after[0]["id"]) == str(rows[0]["id"])
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx.get("spouse_name") == SPOUSE_RENAMED


# ---------------------------------------------------------------------------
# Renderer integration — interview-shaped data renders the spouse;
# the spouse slot still stays unset without a signal (renderer not
# loosened), and the Will asserts marriage only on a married signal.
# ---------------------------------------------------------------------------


def test_spouse_renders_in_will_after_interview_shaped_save(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx.get("spouse_name") == SPOUSE
    body = (TEMPLATES_DIR / "last-will-and-testament.txt").read_text(encoding="utf-8")
    will = documents_service.render_template_content(body, ctx)
    assert f"I am married to {SPOUSE}." in will


def test_single_profile_renders_no_spouse(db):
    _household_id, matter_id = build_world(db)
    save(db, matter_id, {"testator_full_name": TESTATOR, "marital_status": "single"})
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert "spouse_name" not in ctx
    body = (TEMPLATES_DIR / "last-will-and-testament.txt").read_text(encoding="utf-8")
    will = documents_service.render_template_content(body, ctx)
    # A single testator's Will must not assert a marriage (pre-fix it
    # printed "I am married to ___." — the 2026-10-27 Lee defect); the
    # recital states the recorded status instead.
    assert "I am married to" not in will
    assert "I am not married." in will


# ---------------------------------------------------------------------------
# Pack gate — the question exists, is required in the identity cluster,
# and is applicable exactly when married/partnered.
# ---------------------------------------------------------------------------


def _progress(answers: dict):
    pack = get_pack("US-VA")
    return qs.compute_progress(pack.question_sets, answers)


def test_spouse_question_pruned_for_single():
    _per_set, _pct, _missing, pruned = _progress({"marital_status": "single"})
    assert "spouse_full_name" in pruned


def test_spouse_question_applicable_for_married():
    _per_set, _pct, missing, pruned = _progress({"marital_status": "married"})
    assert "spouse_full_name" not in pruned
    assert "spouse_full_name" in missing


def test_spouse_question_applicable_for_partnered():
    _per_set, _pct, missing, pruned = _progress({"marital_status": "partnered"})
    assert "spouse_full_name" not in pruned
    assert "spouse_full_name" in missing


# ---------------------------------------------------------------------------
# Review round (independent diff review, 2026-10-27): multi-testator
# anchoring, SAVEPOINT armor, legacy-pack gating, minor guard, audit.
# ---------------------------------------------------------------------------

SECOND_TESTATOR = "Rohan Sharma"
SECOND_SPOUSE = "Priya Sharma"


def insert_person(db, household_id: str, name: str, kind: str = "adult") -> str:
    pid = str(uuid4())
    _exec(
        db,
        "INSERT INTO persons (id, household_id, full_name, kind)"
        " VALUES (%s, %s, %s, %s)",
        (pid, household_id, name, kind),
    )
    return pid


def insert_relationship_row(db, household_id, from_id, to_id, rtype) -> str:
    rid = str(uuid4())
    _exec(
        db,
        "INSERT INTO relationships"
        " (id, household_id, from_person_id, to_person_id, relationship_type)"
        " VALUES (%s, %s, %s, %s, %s)",
        (rid, household_id, from_id, to_id, rtype),
    )
    return rid


def add_matter(db, household_id: str, version: str = "2.0.0",
               title: str = "Second Matter") -> str:
    firm = _all(db, "SELECT firm_id FROM households WHERE id = %s", (household_id,))[0]
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": version}
    )
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
        " completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 0)",
        (matter_id, str(firm["firm_id"]), household_id, US_VA_JURISDICTION_ID,
         US_VA_PACK_ID, title, snapshot),
    )
    return matter_id


def rows_involving(db, household_id: str, person_id: str):
    return _all(
        db,
        "SELECT * FROM relationships WHERE household_id = %s"
        " AND relationship_type IN ('spouse', 'partner')"
        " AND (from_person_id = %s OR to_person_id = %s) ORDER BY created_at",
        (household_id, person_id, person_id),
    )


def test_second_testator_anchors_on_themselves(db):
    """Each spouse with their own matter in ONE household: the second
    matter's sync must anchor on ITS testator (the renderer resolves the
    testator from the matter's own testator_full_name answer FIRST),
    never on the household's primary_client member."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    rohan = insert_person(db, household_id, SECOND_TESTATOR)
    matter2 = add_matter(db, household_id, title="Rohan Estate Plan")
    save(db, matter2, {
        "testator_full_name": SECOND_TESTATOR,
        "marital_status": "married",
        "spouse_full_name": SECOND_SPOUSE,
    })
    testator = person_by_name(db, household_id, TESTATOR)
    spouse = person_by_name(db, household_id, SPOUSE)
    priya = person_by_name(db, household_id, SECOND_SPOUSE)
    assert priya is not None
    pairs = {
        (str(r["from_person_id"]), str(r["to_person_id"]))
        for r in spouse_rows(db, household_id)
    }
    assert pairs == {
        (str(testator["id"]), str(spouse["id"])),
        (rohan, str(priya["id"])),
    }


def test_second_testator_married_to_primary_client(db):
    """B's spouse IS the primary client A: the row must be created for
    B (anchored on B), not swallowed by the self-link guard that fires
    when the anchor wrongly resolves to A."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    rohan = insert_person(db, household_id, SECOND_TESTATOR)
    matter2 = add_matter(db, household_id, title="Rohan Estate Plan")
    save(db, matter2, {
        "testator_full_name": SECOND_TESTATOR,
        "marital_status": "married",
        "spouse_full_name": TESTATOR,
    })
    testator = person_by_name(db, household_id, TESTATOR)
    rows = rows_involving(db, household_id, rohan)
    assert len(rows) == 1
    other = (
        rows[0]["to_person_id"]
        if str(rows[0]["from_person_id"]) == rohan
        else rows[0]["from_person_id"]
    )
    assert str(other) == str(testator["id"])
    ctx = documents_service.build_template_context(db, UUID(matter2))
    assert ctx.get("spouse_name") == TESTATOR


def test_second_testator_single_keeps_primary_spouse_row(db):
    """B recording 'single' on B's own matter must not delete A's
    spouse row: the delete branch is scoped to the resolved testator
    of the matter being saved."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    insert_person(db, household_id, SECOND_TESTATOR)
    matter2 = add_matter(db, household_id, title="Rohan Estate Plan")
    save(db, matter2, {
        "testator_full_name": SECOND_TESTATOR,
        "marital_status": "single",
    })
    assert len(spouse_rows(db, household_id)) == 1


def test_sync_failure_never_fails_the_save(db, monkeypatch):
    """A Postgres failure inside the sync (here: the spouse person
    INSERT path blowing up mid-transaction) must roll back only to the
    sync's savepoint — the answer save itself still succeeds and the
    connection stays usable for the next save."""
    household_id, matter_id = build_world(db)

    def boom(conn, hid, name):
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM table_that_does_not_exist")
        raise AssertionError("unreachable")

    monkeypatch.setattr(intake_service, "_ensure_person_for_name", boom)
    save(db, matter_id, MARRIED_ANSWERS)  # must not raise
    stored = _all(
        db, "SELECT question_key FROM intake_answers WHERE matter_id = %s",
        (matter_id,),
    )
    assert {r["question_key"] for r in stored} >= {
        "marital_status", "spouse_full_name",
    }
    assert spouse_rows(db, household_id) == []
    monkeypatch.undo()
    save(db, matter_id, {"primary_residence_state": "Virginia"})
    assert len(spouse_rows(db, household_id)) == 1


def test_legacy_pack_matter_save_never_touches_relationships(db):
    """Legacy (0.2.0) packs have no spouse_full_name question, so a
    legacy matter's saves carry no capture mandate: recording 'single'
    there must not delete a row the v2 flow created."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    assert len(spouse_rows(db, household_id)) == 1
    legacy = add_matter(db, household_id, version="0.2.0", title="Legacy Matter")
    save(db, legacy, {"marital_status": "single"})
    assert len(spouse_rows(db, household_id)) == 1


def test_sync_mutations_are_audited(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    save(db, matter_id, {"marital_status": "partnered"})
    save(db, matter_id, {"marital_status": "single"})
    actions = [
        r["action"] for r in _all(db, "SELECT action FROM audit_events")
    ]
    assert "relationships.spouse_sync_created" in actions
    assert "relationships.spouse_sync_updated" in actions
    assert "relationships.spouse_sync_removed" in actions


def test_spouse_name_set_to_testators_own_name_abstains(db):
    """spouse_full_name overwritten with the testator's OWN name is a
    data error, not a correction: the sync never self-links and leaves
    the existing (valid) row untouched rather than guessing."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    before = spouse_rows(db, household_id)
    assert len(before) == 1
    save(db, matter_id, {"spouse_full_name": TESTATOR})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == str(before[0]["id"])
    spouse = person_by_name(db, household_id, SPOUSE)
    assert str(rows[0]["to_person_id"]) == str(spouse["id"])


def test_minor_namesake_is_never_linked_as_spouse(db):
    """A minor child who happens to share the spouse's exact name must
    never become the spouse row's target (the renderer would print the
    child as the spouse). The sync abstains instead."""
    household_id, matter_id = build_world(db)
    insert_person(db, household_id, SPOUSE, kind="minor")
    save(db, matter_id, MARRIED_ANSWERS)
    assert spouse_rows(db, household_id) == []
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert "spouse_name" not in ctx


def test_type_correction_preserves_row_identity(db):
    """married<->partnered corrects the row's type IN PLACE — the row
    id (and its history) survives; delete+recreate would churn both."""
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    first = spouse_rows(db, household_id)
    save(db, matter_id, {"marital_status": "partnered"})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert rows[0]["relationship_type"] == "partner"
    assert str(rows[0]["id"]) == str(first[0]["id"])


def test_unmatched_uuid_spouse_value_creates_nothing(db):
    """A UUID-shaped spouse value that matches no household person is
    a dangling ID (the R1 bug class), never a name: no person may be
    minted with the UUID as their name."""
    household_id, matter_id = build_world(db)
    ghost = str(uuid4())
    save(db, matter_id, {
        "testator_full_name": TESTATOR,
        "marital_status": "married",
        "spouse_full_name": ghost,
    })
    assert spouse_rows(db, household_id) == []
    assert person_by_name(db, household_id, ghost) is None


def test_duplicate_rows_collapse_to_one(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    testator = person_by_name(db, household_id, TESTATOR)
    spouse = person_by_name(db, household_id, SPOUSE)
    insert_relationship_row(
        db, household_id, str(testator["id"]), str(spouse["id"]), "spouse")
    third = insert_person(db, household_id, "Other Person")
    insert_relationship_row(db, household_id, str(testator["id"]), third, "spouse")
    save(db, matter_id, {"primary_residence_state": "Virginia"})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert str(rows[0]["to_person_id"]) == str(spouse["id"])


def test_reverse_direction_row_is_kept(db):
    household_id, matter_id = build_world(db)
    testator_id = insert_person(db, household_id, TESTATOR)
    spouse_id = insert_person(db, household_id, SPOUSE)
    rid = insert_relationship_row(db, household_id, spouse_id, testator_id, "spouse")
    save(db, matter_id, MARRIED_ANSWERS)
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == rid


def test_unrelated_types_and_other_couples_survive(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    testator = person_by_name(db, household_id, TESTATOR)
    spouse = person_by_name(db, household_id, SPOUSE)
    insert_relationship_row(
        db, household_id, str(testator["id"]), str(spouse["id"]), "parent")
    c = insert_person(db, household_id, "Cousin One")
    d = insert_person(db, household_id, "Cousin Two")
    insert_relationship_row(db, household_id, c, d, "spouse")
    save(db, matter_id, {"marital_status": "single"})
    remaining = _all(
        db, "SELECT * FROM relationships WHERE household_id = %s", (household_id,))
    assert len(remaining) == 2
    kinds = {(str(r["from_person_id"]), r["relationship_type"]) for r in remaining}
    assert (str(testator["id"]), "parent") in kinds
    assert (c, "spouse") in kinds


def test_widowed_status_removes_relationship(db):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    save(db, matter_id, {"marital_status": "widowed"})
    assert spouse_rows(db, household_id) == []


# ---------------------------------------------------------------------------
# PERF-018: saves that touch no spouse-relevant key must not re-run the
# full household-graph reconcile when the graph already matches the
# stored answers (cheap watermark check instead of the ~8-12 query
# walk), and an unresolvable testator must log a warning, not vanish.
# ---------------------------------------------------------------------------


def test_unrelated_save_skips_full_reconcile(db, monkeypatch):
    household_id, matter_id = build_world(db)
    save(db, matter_id, MARRIED_ANSWERS)
    rows_before = spouse_rows(db, household_id)
    assert len(rows_before) == 1

    from app.domains.household import repository as household_repo

    original = household_repo.list_persons_for_household
    calls = {"n": 0}

    def counting(conn, hid):
        calls["n"] += 1
        return original(conn, hid)

    monkeypatch.setattr(
        household_repo, "list_persons_for_household", counting
    )
    # An unrelated autosave (the wizard saves constantly): the spouse
    # reconcile must take the cheap path — the full graph walk reads
    # the persons list repeatedly, the watermark check never does.
    save(db, matter_id, {"primary_residence_state": "Virginia"})
    assert calls["n"] == 0, (
        "unrelated save ran the full spouse reconcile "
        f"(list_persons called {calls['n']}x)"
    )
    rows_after = spouse_rows(db, household_id)
    assert [str(r["id"]) for r in rows_after] == [str(rows_before[0]["id"])]

    # And the early exit must not wedge the sync: a spouse-touching
    # save still re-points the row.
    monkeypatch.setattr(
        household_repo, "list_persons_for_household", original
    )
    save(db, matter_id, {"spouse_full_name": SPOUSE_RENAMED})
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    renamed = person_by_name(db, household_id, SPOUSE_RENAMED)
    assert renamed is not None
    assert str(rows[0]["to_person_id"]) == str(renamed["id"])


# ---------------------------------------------------------------------------
# Phase-3 triage (2026-10-29): ONE bulk save carrying the whole fact
# set — the production E2E's programmatic completion pattern.
# ---------------------------------------------------------------------------

BULK_TESTATOR = "Marcus PhaseThree"
BULK_SPOUSE = "Elena PhaseThree"


def test_bulk_save_with_nominations_captures_testator_and_spouse(db):
    """A single save carrying the full married-household fact set
    (testator + spouse + executor/guardian nominations + minor
    children) must still capture the testator person and the spouse
    relationship. The nomination sync creates named adult persons
    (the executor, the guardians) inside the SAME save, before the
    testator capture runs; those nominee adults are not the testator
    and must not block the capture. Pre-fix, the testator-name sync's
    create branch required NO adult person at all, so it never fired:
    no testator person existed, the spouse sync had no anchor person,
    no relationship row was created, and the Will rendered
    "I am married to ___." on a provably complete 39/39 packet
    (Phase-3 production E2E, 2026-10-29 — the Attorney Reviewer's
    unfilled_placeholders finding on family_identification was right).
    """
    household_id, matter_id = build_world(db)
    save(db, matter_id, {
        "testator_full_name": BULK_TESTATOR,
        "marital_status": "married",
        "spouse_full_name": BULK_SPOUSE,
        "has_minor_children": True,
        "minor_children_details": (
            "Aarav PhaseThree, age 12, Diya PhaseThree, age 9, "
            "Kabir PhaseThree, age 6"
        ),
        "primary_executor_person": BULK_SPOUSE,
        "backup_executor_person": "Amit Sharma",
        "primary_guardian_person": "Amit Sharma",
        "backup_guardian_person": "Neha Kapoor",
        "healthcare_agent_person": BULK_SPOUSE,
        "poa_agent_person": BULK_SPOUSE,
    })
    testator = person_by_name(db, household_id, BULK_TESTATOR)
    assert testator is not None, "bulk save never captured the testator person"
    spouse = person_by_name(db, household_id, BULK_SPOUSE)
    assert spouse is not None
    rows = spouse_rows(db, household_id)
    assert len(rows) == 1
    assert str(rows[0]["from_person_id"]) == str(testator["id"])
    assert str(rows[0]["to_person_id"]) == str(spouse["id"])
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx.get("spouse_name") == BULK_SPOUSE
    recital = ctx.get("family_identification") or ""
    assert f"I am married to {BULK_SPOUSE}." in recital
    assert "___" not in recital


def test_unresolvable_testator_logs_warning(db, caplog):
    import logging

    household_id, matter_id = build_world(db)
    # The primary_client membership is already linked to a person
    # with a real name, but the recorded testator name matches nobody:
    # a genuine identity conflict. The testator-name sync must NOT
    # mint a second primary-client person over it (Phase-3 triage,
    # 2026-10-29: the UNLINKED membership case now captures instead —
    # see test_bulk_save_with_nominations_captures_testator_and_spouse),
    # the renderer-anchoring chain resolves no testator, and the
    # reconcile must say so at WARNING with the household id instead
    # of returning silently.
    linked_testator = insert_person(db, household_id, TESTATOR)
    _exec(
        db,
        "UPDATE household_members SET person_id = %s"
        " WHERE household_id = %s AND relationship_role = 'primary_client'",
        (linked_testator, household_id),
    )
    answers = dict(MARRIED_ANSWERS, testator_full_name="Rohan Sharma")
    with caplog.at_level(logging.WARNING, logger="app.domains.intake.service"):
        save(db, matter_id, answers)
    assert spouse_rows(db, household_id) == []
    warnings = [
        r for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any(
        "testator" in r.getMessage().lower() and household_id in r.getMessage()
        for r in warnings
    ), [r.getMessage() for r in warnings]
