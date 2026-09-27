"""RLS allow/deny integration tests for LegacyFlow Slice 2 (household intake).

Covers the seven new tables (persons, relationships, invitations,
intake_answers, planning_decisions, nominations, escalations) plus the
Slice 2 ALTERs (matters status check, households.status,
household_members partial unique indexes).

Reuses the conftest `db` fixture (all migrations applied in order) and the
`as_user` helper: each test runs as the least-privilege `lf_app` role with
auth.uid() set to the synthetic user under test, inside a transaction that
is always rolled back.

All fixture data is synthetic. Nothing sensitive is printed.
"""

import uuid

import pytest
from psycopg import errors

from conftest import (
    FIRM_A,
    FIRM_B,
    HOUSEHOLD_A,
    HOUSEHOLD_B,
    MATTER_A,
    MATTER_B,
    USER_A,
    USER_B,
    USER_C,
    as_user,
)

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
USER_D = uuid.UUID("90000000-0000-0000-0000-000000000000")  # firm A staff
USER_E = uuid.UUID("91000000-0000-0000-0000-000000000000")  # firm A auditor

PERSON_A1 = uuid.UUID("81000000-0000-0000-0000-000000000000")  # household A
PERSON_A2 = uuid.UUID("82000000-0000-0000-0000-000000000000")  # household A
PERSON_B1 = uuid.UUID("83000000-0000-0000-0000-000000000000")  # household B
PERSON_B2 = uuid.UUID("8e000000-0000-0000-0000-000000000000")  # household B

REL_A1 = uuid.UUID("84000000-0000-0000-0000-000000000000")
REL_B1 = uuid.UUID("85000000-0000-0000-0000-000000000000")

INV_A1 = uuid.UUID("86000000-0000-0000-0000-000000000000")  # firm A, household A
INV_A2 = uuid.UUID("87000000-0000-0000-0000-000000000000")  # firm A, no household
INV_B1 = uuid.UUID("88000000-0000-0000-0000-000000000000")  # firm B, household B

NOM_A1 = uuid.UUID("89000000-0000-0000-0000-000000000000")
NOM_B1 = uuid.UUID("8a000000-0000-0000-0000-000000000000")

ESC_A1 = uuid.UUID("8b000000-0000-0000-0000-000000000000")
ESC_B1 = uuid.UUID("8c000000-0000-0000-0000-000000000000")

DEC_A1 = uuid.UUID("8d000000-0000-0000-0000-000000000000")

NEW_TABLES = (
    "persons",
    "relationships",
    "invitations",
    "intake_answers",
    "planning_decisions",
    "nominations",
    "escalations",
)


@pytest.fixture(scope="session")
def slice2(db):
    """Insert Slice 2 synthetic fixtures as owner (RLS bypassed)."""
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id) values (%s), (%s)",
            (USER_D, USER_E),
        )
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Staff D', 'staff-d@example.test'), "
            "(%s, 'Synthetic Auditor E', 'auditor-e@example.test')",
            (USER_D, USER_E),
        )
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status) values "
            "(%s, %s, 'staff', 'active'), "
            "(%s, %s, 'auditor', 'active')",
            (FIRM_A, USER_D, FIRM_A, USER_E),
        )
        cur.execute(
            "insert into persons (id, household_id, full_name, kind) values "
            "(%s, %s, 'Synthetic Person A1', 'adult'), "
            "(%s, %s, 'Synthetic Person A2', 'minor'), "
            "(%s, %s, 'Synthetic Person B1', 'adult')",
            (PERSON_A1, HOUSEHOLD_A, PERSON_A2, HOUSEHOLD_A, PERSON_B1, HOUSEHOLD_B),
        )
        cur.execute(
            "insert into persons (id, household_id, full_name, kind) values "
            "(%s, %s, 'Synthetic Person B2', 'adult')",
            (PERSON_B2, HOUSEHOLD_B),
        )
        cur.execute(
            "insert into relationships (id, household_id, from_person_id, to_person_id, relationship_type) values "
            "(%s, %s, %s, %s, 'parent'), "
            "(%s, %s, %s, %s, 'spouse')",
            (
                REL_A1, HOUSEHOLD_A, PERSON_A1, PERSON_A2,
                REL_B1, HOUSEHOLD_B, PERSON_B1, PERSON_B2,
            ),
        )
        cur.execute(
            "insert into invitations (id, firm_id, household_id, email, role, token_hash, expires_at, invited_by) values "
            "(%s, %s, %s, 'invitee-a1@example.test', 'client_primary', 'synth-hash-a1', now() + interval '7 days', %s), "
            "(%s, %s, null, 'invitee-a2@example.test', 'firm_staff', 'synth-hash-a2', now() + interval '7 days', %s), "
            "(%s, %s, %s, 'invitee-b1@example.test', 'client_primary', 'synth-hash-b1', now() + interval '7 days', %s)",
            (
                INV_A1, FIRM_A, HOUSEHOLD_A, USER_A,
                INV_A2, FIRM_A, USER_A,
                INV_B1, FIRM_B, HOUSEHOLD_B, USER_B,
            ),
        )
        cur.execute(
            "insert into intake_answers (matter_id, question_key, answer) values "
            "(%s, 'household.size', '{\"adults\": 2}'), "
            "(%s, 'household.size', '{\"adults\": 1}')",
            (MATTER_A, MATTER_B),
        )
        cur.execute(
            "insert into planning_decisions (id, matter_id, decision_key, value, decided_by) values "
            "(%s, %s, 'guardian.primary', '{\"person_id\": \"81000000-0000-0000-0000-000000000000\"}', %s)",
            (DEC_A1, MATTER_A, USER_A),
        )
        cur.execute(
            "insert into nominations (id, matter_id, nomination_type, person_id, priority) values "
            "(%s, %s, 'guardian', %s, 1), "
            "(%s, %s, 'executor', %s, 1)",
            (NOM_A1, MATTER_A, PERSON_A1, NOM_B1, MATTER_B, PERSON_B1),
        )
        cur.execute(
            "insert into escalations (id, matter_id, rule_id, severity, facts_snapshot) values "
            "(%s, %s, 'RULE-001', 'review', '{}'), "
            "(%s, %s, 'RULE-001', 'info', '{}')",
            (ESC_A1, MATTER_A, ESC_B1, MATTER_B),
        )
    db.commit()
    return db


def _ids(cur, table, id_col="id"):
    cur.execute(f"select {id_col} from {table} order by {id_col}")
    return {row[0] for row in cur.fetchall()}


# --- client isolation: household path ---------------------------------------

def test_client_cannot_read_other_household_persons(db, slice2):
    """USER_C (household A) sees household A persons, never household B's."""
    with as_user(db, USER_C) as cur:
        ids = _ids(cur, "persons")
    assert PERSON_A1 in ids and PERSON_A2 in ids
    assert PERSON_B1 not in ids


def test_client_cannot_read_other_household_matter_data(db, slice2):
    """USER_C sees only MATTER_A's answers/decisions/nominations/escalations."""
    with as_user(db, USER_C) as cur:
        cur.execute("select matter_id, question_key from intake_answers")
        assert {(r[0], r[1]) for r in cur.fetchall()} == {(MATTER_A, "household.size")}
        cur.execute("select matter_id from planning_decisions")
        assert {r[0] for r in cur.fetchall()} == {MATTER_A}
        assert _ids(cur, "nominations") == {NOM_A1}
        assert _ids(cur, "escalations") == {ESC_A1}
        cur.execute("select household_id from relationships")
        assert {r[0] for r in cur.fetchall()} == {HOUSEHOLD_A}


def test_client_cannot_read_other_firm_matter(db, slice2):
    """Household path grants nothing outside the client's own household —
    USER_C cannot touch firm B's matter rows at all."""
    with as_user(db, USER_C) as cur:
        cur.execute("select count(*) from matters where id = %s", (MATTER_B,))
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from intake_answers where matter_id = %s", (MATTER_B,))
        assert cur.fetchone()[0] == 0


# --- firm isolation ----------------------------------------------------------

def test_firm_a_staff_cannot_access_firm_b_slice2_rows(db, slice2):
    """USER_D (staff, firm A) sees firm A's slice-2 rows, none of firm B's."""
    with as_user(db, USER_D) as cur:
        assert PERSON_A1 in _ids(cur, "persons")
        assert PERSON_B1 not in _ids(cur, "persons")
        cur.execute("select matter_id from intake_answers")
        assert {r[0] for r in cur.fetchall()} == {MATTER_A}
        assert _ids(cur, "nominations") == {NOM_A1}
        assert _ids(cur, "escalations") == {ESC_A1}
        assert _ids(cur, "planning_decisions") == {DEC_A1}


def test_auditor_can_read_own_firm_slice2_rows(db, slice2):
    """USER_E (auditor, active firm member) can read firm A's slice-2 rows."""
    with as_user(db, USER_E) as cur:
        assert PERSON_A1 in _ids(cur, "persons")
        assert PERSON_B1 not in _ids(cur, "persons")
        cur.execute("select count(*) from intake_answers where matter_id = %s", (MATTER_A,))
        assert cur.fetchone()[0] == 1
        assert _ids(cur, "nominations") == {NOM_A1}
        assert _ids(cur, "escalations") == {ESC_A1}


def test_new_tables_invisible_across_firms(db, slice2):
    """USER_B (firm B) sees only firm B's slice-2 rows."""
    with as_user(db, USER_B) as cur:
        assert _ids(cur, "persons") == {PERSON_B1, PERSON_B2}
        cur.execute("select matter_id from intake_answers")
        assert {r[0] for r in cur.fetchall()} == {MATTER_B}
        assert _ids(cur, "nominations") == {NOM_B1}
        assert _ids(cur, "escalations") == {ESC_B1}


# --- client autosave write paths ----------------------------------------------

def test_client_can_upsert_own_intake_answers(db, slice2):
    """USER_C can INSERT and upsert (PK conflict) answers for MATTER_A."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "insert into intake_answers (matter_id, question_key, answer) "
            "values (%s, 'assets.home', '{\"owns\": true}')",
            (MATTER_A,),
        )
        cur.execute(
            "insert into intake_answers (matter_id, question_key, answer) "
            "values (%s, 'household.size', '{\"adults\": 3}') "
            "on conflict (matter_id, question_key) do update set answer = excluded.answer",
            (MATTER_A,),
        )
        cur.execute(
            "select answer from intake_answers where matter_id = %s and question_key = 'household.size'",
            (MATTER_A,),
        )
        assert cur.fetchone()[0] == {"adults": 3}
        cur.execute(
            "select updated_at is not null from intake_answers "
            "where matter_id = %s and question_key = 'assets.home'",
            (MATTER_A,),
        )
        assert cur.fetchone()[0] is True


def test_client_can_insert_own_planning_decisions(db, slice2):
    """USER_C can INSERT planning decisions for MATTER_A, not MATTER_B."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "insert into planning_decisions (matter_id, decision_key, value, decided_by) "
            "values (%s, 'trust.funding', '{\"fund\": true}', %s)",
            (MATTER_A, USER_C),
        )
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into planning_decisions (matter_id, decision_key, value, decided_by) "
                "values (%s, 'trust.funding', '{\"fund\": true}', %s)",
                (MATTER_B, USER_C),
            )


def test_client_cannot_write_other_household_answers(db, slice2):
    """USER_C cannot INSERT or UPDATE intake_answers for MATTER_B."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into intake_answers (matter_id, question_key, answer) "
                "values (%s, 'household.size', '{\"adults\": 9}')",
                (MATTER_B,),
            )
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update intake_answers set answer = '{\"adults\": 9}' where matter_id = %s",
            (MATTER_B,),
        )
        assert cur.rowcount == 0


def test_client_cannot_delete_intake_answers(db, slice2):
    """No DELETE policy: client DELETE touches zero rows; the row survives."""
    with as_user(db, USER_C) as cur:
        cur.execute(
            "delete from intake_answers where matter_id = %s and question_key = 'household.size'",
            (MATTER_A,),
        )
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute(
            "select count(*) from intake_answers where matter_id = %s and question_key = 'household.size'",
            (MATTER_A,),
        )
        assert cur.fetchone()[0] == 1


def test_client_cannot_delete_planning_decisions(db, slice2):
    """Clients get no DELETE on planning_decisions either."""
    with as_user(db, USER_C) as cur:
        cur.execute("delete from planning_decisions where matter_id = %s", (MATTER_A,))
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute("select count(*) from planning_decisions where id = %s", (DEC_A1,))
        assert cur.fetchone()[0] == 1


def test_client_cannot_insert_persons(db, slice2):
    """persons writes are firm-side only."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into persons (household_id, full_name, kind) values (%s, 'Synthetic X', 'adult')",
                (HOUSEHOLD_A,),
            )


# --- invitations ---------------------------------------------------------------

def test_invitations_invisible_across_firms(db, slice2):
    """Firm A members see only firm A invitations; firm B only firm B's."""
    with as_user(db, USER_A) as cur:
        assert _ids(cur, "invitations") == {INV_A1, INV_A2}
    with as_user(db, USER_B) as cur:
        assert _ids(cur, "invitations") == {INV_B1}


def test_invitations_client_household_scope(db, slice2):
    """USER_C sees the invitation for their own household only — not the
    household-less firm invitation, not firm B's."""
    with as_user(db, USER_C) as cur:
        assert _ids(cur, "invitations") == {INV_A1}


def test_staff_cannot_read_invitations(db, slice2):
    """Invitations are restricted to firm_admin/attorney roles: USER_D
    (staff, active member) sees none."""
    with as_user(db, USER_D) as cur:
        assert _ids(cur, "invitations") == set()


def test_attorney_can_invite_and_revoke(db, slice2):
    """USER_A (attorney) can INSERT and UPDATE invitations for firm A;
    USER_D (staff) is denied on both."""
    new_id = uuid.uuid4()
    with as_user(db, USER_A) as cur:
        cur.execute(
            "insert into invitations (id, firm_id, household_id, email, role, token_hash, expires_at, invited_by) "
            "values (%s, %s, %s, 'new-invitee@example.test', 'client_collaborator', 'synth-hash-new', "
            "now() + interval '7 days', %s)",
            (new_id, FIRM_A, HOUSEHOLD_A, USER_A),
        )
        cur.execute("update invitations set status = 'revoked' where id = %s", (new_id,))
        assert cur.rowcount == 1
    with as_user(db, USER_D) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into invitations (firm_id, email, role, token_hash, expires_at, invited_by) "
                "values (%s, 'staff-invitee@example.test', 'client_primary', 'synth-hash-staff', "
                "now() + interval '7 days', %s)",
                (FIRM_A, USER_D),
            )
    # denied INSERT aborts its transaction: separate block for the UPDATE probe
    with as_user(db, USER_D) as cur:
        cur.execute("update invitations set status = 'revoked' where id = %s", (INV_A1,))
        assert cur.rowcount == 0


# --- anonymous ------------------------------------------------------------------

def test_anonymous_gets_nothing_from_new_tables(db, slice2):
    """No app.user_id -> zero rows on every Slice 2 table."""
    with as_user(db, None) as cur:
        for table in NEW_TABLES:
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, table


# --- regression: slice-1 behavior preserved --------------------------------------

def test_matter_status_history_still_works(db, slice2):
    """Slice 1 status-history policies still hold under the new statuses:
    visible with parent-matter access; firm-side appends only."""
    with as_user(db, USER_A) as cur:
        cur.execute("select to_status from matter_status_history where matter_id = %s", (MATTER_A,))
        assert [r[0] for r in cur.fetchall()] == ["INTAKE_IN_PROGRESS"]
        cur.execute(
            "insert into matter_status_history (matter_id, from_status, to_status, reason_code, actor_user_id) "
            "values (%s, 'INTAKE_IN_PROGRESS', 'INTAKE_SUBMITTED', 'submitted', %s)",
            (MATTER_A, USER_A),
        )
        cur.execute("select count(*) from matter_status_history where matter_id = %s", (MATTER_A,))
        assert cur.fetchone()[0] == 2
    # the insert above rolled back with its as_user transaction; USER_C sees
    # the single fixture row and cannot touch firm B's history
    with as_user(db, USER_C) as cur:
        cur.execute("select count(*) from matter_status_history where matter_id = %s", (MATTER_A,))
        assert cur.fetchone()[0] == 1
        cur.execute("select count(*) from matter_status_history where matter_id = %s", (MATTER_B,))
        assert cur.fetchone()[0] == 0
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update matter_status_history set to_status = 'DRAFT' where matter_id = %s",
            (MATTER_A,),
        )
        assert cur.rowcount == 0
        cur.execute("delete from matter_status_history where matter_id = %s", (MATTER_A,))
        assert cur.rowcount == 0


def test_matters_status_check_rejects_unknown_states(db, slice2):
    """The closed 12-state check from the migration rejects legacy values."""
    with as_user(db, USER_A) as cur:
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                "insert into matters (firm_id, household_id, primary_jurisdiction_id, "
                "jurisdiction_pack_id, plan_type, status) "
                "values (%s, %s, "
                "(select primary_jurisdiction_id from matters where id = %s), "
                "(select jurisdiction_pack_id from matters where id = %s), 'will', 'intake')",
                (FIRM_A, HOUSEHOLD_A, MATTER_A, MATTER_A),
            )


def test_household_member_partial_uniques_hold(db, slice2):
    """The new partial unique indexes reject duplicate (household, user)."""
    with db.cursor() as cur:  # owner: RLS bypassed
        with pytest.raises(errors.UniqueViolation):
            cur.execute(
                "insert into household_members (household_id, user_id, relationship_role, access_status) "
                "values (%s, %s, 'collaborator', 'active')",
                (HOUSEHOLD_A, USER_C),
            )
        db.rollback()
