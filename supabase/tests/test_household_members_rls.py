"""RLS allow/deny integration tests for `household_members` (LegacyFlow Slice 1).

`household_members` previously had only indirect coverage (via the
household-member read path in test_rls.py). This file asserts the direct
policy semantics from
supabase/migrations/20260926220000_foundation_slice1.sql §5b:

  * SELECT: firm members of the household's firm OR active household members.
  * INSERT / UPDATE / DELETE: firm members of the household's firm only.

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
    HOUSEHOLD_A,
    HOUSEHOLD_B,
    USER_A,
    USER_B,
    USER_C,
    as_user,
)

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
USER_G = uuid.UUID("d1000000-0000-0000-0000-000000000000")  # firm B client: household B member
USER_H = uuid.UUID("d2000000-0000-0000-0000-000000000000")  # unattached invitee profile

MEM_G = uuid.UUID("d3000000-0000-0000-0000-000000000000")  # USER_G's household B membership


@pytest.fixture(scope="session")
def members(db):
    """Insert synthetic household-member fixtures as owner (RLS bypassed).

    USER_G is an active household B member with no firm membership, mirroring
    USER_C's role for household A. USER_H is a bare profile used as an
    INSERT target for write-deny probes.
    """
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id) values (%s), (%s)",
            (USER_G, USER_H),
        )
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Client G', 'client-g@example.test'), "
            "(%s, 'Synthetic Invitee H', 'invitee-h@example.test')",
            (USER_G, USER_H),
        )
        cur.execute(
            "insert into household_members (id, household_id, user_id, relationship_role, access_status) values "
            "(%s, %s, %s, 'collaborator', 'active')",
            (MEM_G, HOUSEHOLD_B, USER_G),
        )
    db.commit()
    return db


def _member_rows(cur):
    cur.execute("select id, household_id, user_id from household_members order by id")
    return cur.fetchall()


# --- read paths ---------------------------------------------------------------

def test_household_user_can_read_own_household_members(db, members):
    """USER_C (active household A member, no firm membership) reads exactly
    household A's membership rows — including their own."""
    with as_user(db, USER_C) as cur:
        rows = _member_rows(cur)
    assert rows, "expected to see own household's members"
    assert {r[1] for r in rows} == {HOUSEHOLD_A}
    assert USER_C in {r[2] for r in rows}


def test_firm_a_attorney_can_read_own_firm_household_members(db, members):
    """USER_A (firm A attorney) reads household A's members via the firm
    path, but none of firm B's household."""
    with as_user(db, USER_A) as cur:
        rows = _member_rows(cur)
    assert rows, "expected to see own firm's household members"
    assert {r[1] for r in rows} == {HOUSEHOLD_A}


def test_other_firm_user_cannot_read_household_members(db, members):
    """USER_B (firm B attorney) sees only firm B's household members —
    never household A's roster. Symmetrically for firm B's client."""
    with as_user(db, USER_B) as cur:
        rows = _member_rows(cur)
    assert {r[1] for r in rows} == {HOUSEHOLD_B}
    assert USER_G in {r[2] for r in rows}

    with as_user(db, USER_G) as cur:
        rows = _member_rows(cur)
    assert {r[1] for r in rows} == {HOUSEHOLD_B}
    assert HOUSEHOLD_A not in {r[1] for r in rows}


def test_anonymous_gets_nothing_from_household_members(db, members):
    """No app.user_id -> zero rows from household_members."""
    with as_user(db, None) as cur:
        cur.execute("select count(*) from household_members")
        assert cur.fetchone()[0] == 0


# --- write-deny paths ------------------------------------------------------------

def test_other_firm_user_cannot_write_household_members(db, members):
    """USER_B (firm B) cannot INSERT/UPDATE/DELETE household A's members."""
    # INSERT denied: policy WITH CHECK fails -> privilege error
    with as_user(db, USER_B) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into household_members (household_id, user_id, relationship_role, access_status) "
                "values (%s, %s, 'collaborator', 'active')",
                (HOUSEHOLD_A, USER_H),
            )
    # denied INSERT aborts its transaction: separate blocks for the probes
    with as_user(db, USER_B) as cur:
        cur.execute(
            "update household_members set access_status = 'revoked' "
            "where user_id = %s and household_id = %s",
            (USER_C, HOUSEHOLD_A),
        )
        assert cur.rowcount == 0
    with as_user(db, USER_B) as cur:
        cur.execute(
            "delete from household_members where user_id = %s and household_id = %s",
            (USER_C, HOUSEHOLD_A),
        )
        assert cur.rowcount == 0


def test_household_client_cannot_write_household_members(db, members):
    """USER_C (household client) has NO write path on household_members —
    even on their own membership row."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into household_members (household_id, user_id, relationship_role, access_status) "
                "values (%s, %s, 'collaborator', 'invited')",
                (HOUSEHOLD_A, USER_H),
            )
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update household_members set access_status = 'revoked' where user_id = %s",
            (USER_C,),
        )
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute("delete from household_members where user_id = %s", (USER_C,))
        assert cur.rowcount == 0


def test_anonymous_cannot_write_household_members(db, members):
    """Anonymous callers cannot insert into household_members."""
    with as_user(db, None) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into household_members (household_id, relationship_role, access_status) "
                "values (%s, 'collaborator', 'invited')",
                (HOUSEHOLD_A,),
            )


# --- positive write paths (control: the denies above are policy, not breakage)

def test_firm_attorney_write_paths_allowed(db, members):
    """USER_A (firm A attorney) CAN insert/update/delete own firm's
    household members. Transactions roll back, so fixtures are untouched."""
    with as_user(db, USER_A) as cur:
        cur.execute(
            "insert into household_members (household_id, user_id, relationship_role, access_status) "
            "values (%s, %s, 'collaborator', 'invited')",
            (HOUSEHOLD_A, USER_H),
        )
        cur.execute(
            "update household_members set access_status = 'active' "
            "where user_id = %s and household_id = %s",
            (USER_H, HOUSEHOLD_A),
        )
        assert cur.rowcount == 1
        cur.execute(
            "delete from household_members where user_id = %s and household_id = %s",
            (USER_H, HOUSEHOLD_A),
        )
        assert cur.rowcount == 1
