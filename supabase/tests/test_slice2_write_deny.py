"""Write-deny RLS tests for Slice 2 matter/household tables.

`nominations`, `escalations`, and `relationships` previously had read-deny
coverage only (test_slice2_rls.py). This file asserts the write-deny paths,
matching the policy semantics in
supabase/migrations/20260927010000_household_intake_slice2.sql:

  * nominations / escalations: INSERT / UPDATE / DELETE require an ACTIVE
    firm membership in the matter's firm ("insert/update/delete by firm
    members"). Household clients get NO write path.
  * relationships: INSERT / UPDATE / DELETE require active firm membership
    in the household's firm. Household clients get NO write path.

The fixture builds a third, synthetic firm (FIRM_C) so the deny probes run
against rows that are invisible to every user in the other test files —
cross-file equality assertions in test_slice2_rls.py etc. stay untouched.

Reuses the conftest `db` fixture and `as_user` helper: each test runs as
the least-privilege `lf_app` role inside a transaction that is always
rolled back.

All fixture data is synthetic. Nothing sensitive is printed.
"""

import uuid

import pytest
from psycopg import errors

from conftest import (
    USER_B,
    USER_C,
    VA_JURISDICTION,
    VA_PACK,
    as_user,
)

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
FIRM_C = uuid.UUID("c0000000-0000-0000-0000-000000000000")
USER_F = uuid.UUID("c1000000-0000-0000-0000-000000000000")  # firm C attorney
HOUSEHOLD_C = uuid.UUID("c4000000-0000-0000-0000-000000000000")
MATTER_C = uuid.UUID("c6000000-0000-0000-0000-000000000000")

PERSON_C1 = uuid.UUID("c8100000-0000-0000-0000-000000000000")
PERSON_C2 = uuid.UUID("c8200000-0000-0000-0000-000000000000")

REL_C1 = uuid.UUID("c8400000-0000-0000-0000-000000000000")
NOM_C1 = uuid.UUID("c8900000-0000-0000-0000-000000000000")
ESC_C1 = uuid.UUID("c8b00000-0000-0000-0000-000000000000")


@pytest.fixture(scope="session")
def firm_c(db):
    """Insert a synthetic third-firm fixture set as owner (RLS bypassed)."""
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (USER_F,))
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Attorney F', 'attorney-f@example.test')",
            (USER_F,),
        )
        cur.execute(
            "insert into firms (id, legal_name, display_name, status) values "
            "(%s, 'Synthetic Firm C PLLC', 'Synthetic Firm C', 'active')",
            (FIRM_C,),
        )
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status) values "
            "(%s, %s, 'attorney', 'active')",
            (FIRM_C, USER_F),
        )
        cur.execute(
            "insert into households (id, firm_id, display_name, created_by_user_id) values "
            "(%s, %s, 'Synthetic Household C', %s)",
            (HOUSEHOLD_C, FIRM_C, USER_F),
        )
        cur.execute(
            "insert into matters (id, firm_id, household_id, primary_jurisdiction_id, "
            "jurisdiction_pack_id, plan_type, status) values "
            "(%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS')",
            (MATTER_C, FIRM_C, HOUSEHOLD_C, VA_JURISDICTION, VA_PACK),
        )
        cur.execute(
            "insert into persons (id, household_id, full_name, kind) values "
            "(%s, %s, 'Synthetic Person C1', 'adult'), "
            "(%s, %s, 'Synthetic Person C2', 'adult')",
            (PERSON_C1, HOUSEHOLD_C, PERSON_C2, HOUSEHOLD_C),
        )
        cur.execute(
            "insert into relationships (id, household_id, from_person_id, to_person_id, relationship_type) values "
            "(%s, %s, %s, %s, 'spouse')",
            (REL_C1, HOUSEHOLD_C, PERSON_C1, PERSON_C2),
        )
        cur.execute(
            "insert into nominations (id, matter_id, nomination_type, person_id, priority) values "
            "(%s, %s, 'guardian', %s, 1)",
            (NOM_C1, MATTER_C, PERSON_C1),
        )
        cur.execute(
            "insert into escalations (id, matter_id, rule_id, severity, facts_snapshot) values "
            "(%s, %s, 'RULE-C1', 'review', '{}')",
            (ESC_C1, MATTER_C),
        )
    db.commit()
    return db


def _insert_nomination(cur, matter_id):
    cur.execute(
        "insert into nominations (matter_id, nomination_type, person_id, priority) "
        "values (%s, 'executor', %s, 2)",
        (matter_id, PERSON_C1),
    )


def _insert_escalation(cur, matter_id):
    cur.execute(
        "insert into escalations (matter_id, rule_id, severity, facts_snapshot) "
        "values (%s, 'RULE-C9', 'info', '{}')",
        (matter_id,),
    )


def _insert_relationship(cur, household_id):
    cur.execute(
        "insert into relationships (household_id, from_person_id, to_person_id, relationship_type) "
        "values (%s, %s, %s, 'sibling')",
        (household_id, PERSON_C1, PERSON_C2),
    )


# --- cross-firm write deny -------------------------------------------------------

def test_cross_firm_user_cannot_insert_slice2_tables(db, firm_c):
    """USER_B (firm B attorney) cannot INSERT into firm C's nominations,
    escalations, or relationships."""
    with as_user(db, USER_B) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_nomination(cur, MATTER_C)
    with as_user(db, USER_B) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_escalation(cur, MATTER_C)
    with as_user(db, USER_B) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_relationship(cur, HOUSEHOLD_C)


def test_cross_firm_user_cannot_update_or_delete_slice2_tables(db, firm_c):
    """USER_B's UPDATE/DELETE of firm C's rows touches zero rows —
    RLS filters before the write, never errors."""
    with as_user(db, USER_B) as cur:
        cur.execute("update nominations set priority = 9 where id = %s", (NOM_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from nominations where id = %s", (NOM_C1,))
        assert cur.rowcount == 0
    with as_user(db, USER_B) as cur:
        cur.execute("update escalations set status = 'dismissed' where id = %s", (ESC_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from escalations where id = %s", (ESC_C1,))
        assert cur.rowcount == 0
    with as_user(db, USER_B) as cur:
        cur.execute("update relationships set relationship_type = 'parent' where id = %s", (REL_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from relationships where id = %s", (REL_C1,))
        assert cur.rowcount == 0


# --- household-client write deny ---------------------------------------------------

def test_household_client_cannot_write_other_household_slice2_rows(db, firm_c):
    """USER_C (household A client) cannot INSERT/UPDATE/DELETE another
    household's nominations, escalations, or relationships — clients have
    no write path on these tables at all."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_nomination(cur, MATTER_C)
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_escalation(cur, MATTER_C)
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_relationship(cur, HOUSEHOLD_C)
    with as_user(db, USER_C) as cur:
        cur.execute("update nominations set priority = 9 where id = %s", (NOM_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from nominations where id = %s", (NOM_C1,))
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute("update escalations set status = 'dismissed' where id = %s", (ESC_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from escalations where id = %s", (ESC_C1,))
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute("update relationships set relationship_type = 'parent' where id = %s", (REL_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from relationships where id = %s", (REL_C1,))
        assert cur.rowcount == 0


# --- anonymous ----------------------------------------------------------------------

def test_anonymous_cannot_write_slice2_tables(db, firm_c):
    """Unauthenticated callers get no write path on any of the three tables."""
    with as_user(db, None) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_nomination(cur, MATTER_C)
    with as_user(db, None) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_relationship(cur, HOUSEHOLD_C)
    with as_user(db, None) as cur:
        cur.execute("update escalations set status = 'resolved' where id = %s", (ESC_C1,))
        assert cur.rowcount == 0
        cur.execute("delete from nominations where id = %s", (NOM_C1,))
        assert cur.rowcount == 0


# --- positive write paths (control: the denies above are policy, not breakage) ------

def test_own_firm_attorney_write_paths_allowed(db, firm_c):
    """USER_F (firm C attorney) CAN insert/update/delete firm C's
    nominations, escalations, and relationships. Transactions roll back,
    so fixtures are untouched."""
    with as_user(db, USER_F) as cur:
        _insert_nomination(cur, MATTER_C)
        cur.execute("update nominations set priority = 2 where id = %s", (NOM_C1,))
        assert cur.rowcount == 1
        cur.execute("delete from nominations where id = %s", (NOM_C1,))
        assert cur.rowcount == 1
    with as_user(db, USER_F) as cur:
        _insert_escalation(cur, MATTER_C)
        cur.execute("update escalations set status = 'resolved' where id = %s", (ESC_C1,))
        assert cur.rowcount == 1
        cur.execute("delete from escalations where id = %s", (ESC_C1,))
        assert cur.rowcount == 1
    with as_user(db, USER_F) as cur:
        _insert_relationship(cur, HOUSEHOLD_C)
        cur.execute("update relationships set relationship_type = 'parent' where id = %s", (REL_C1,))
        assert cur.rowcount == 1
        cur.execute("delete from relationships where id = %s", (REL_C1,))
        assert cur.rowcount == 1
