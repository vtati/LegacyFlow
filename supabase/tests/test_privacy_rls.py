"""RLS allow/deny integration tests for the reminders-privacy migration.

Covers the two new tables (notification_preferences, data_deletion_requests)
plus the new security-definer helpers (public.caller_firm_role,
public.is_firm_admin_of_user):

  * notification_preferences: users read/update their own row; firm admins
    read (never write) rows of their firm's users; nobody sees other
    firms' rows.
  * data_deletion_requests: users read/insert their own requests;
    firm_admin + attorney read their firm's queue; only firm_admin moves
    triage status (and only the status/processed_at/notes columns — the
    guard trigger rejects reassigning user_id/firm_id or rewriting
    requested_at/created_at).

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
    USER_A,
    USER_B,
    USER_C,
    as_user,
)

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
USER_P = uuid.UUID("b1000000-0000-0000-0000-000000000000")  # firm A admin
USER_Q = uuid.UUID("b2000000-0000-0000-0000-000000000000")  # no memberships

REQP_C1 = uuid.UUID("b3000000-0000-0000-0000-000000000000")  # USER_C, firm A


@pytest.fixture(scope="session")
def privacy(db):
    """Insert privacy-migration synthetic fixtures as owner (RLS bypassed)."""
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id) values (%s), (%s)",
            (USER_P, USER_Q),
        )
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Admin P', 'admin-p@example.test'), "
            "(%s, 'Synthetic Outsider Q', 'outsider-q@example.test')",
            (USER_P, USER_Q),
        )
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status) values "
            "(%s, %s, 'firm_admin', 'active')",
            (FIRM_A, USER_P),
        )
        # Client C (household member of firm A, no firm membership) opts out.
        cur.execute(
            "insert into notification_preferences (user_id, reminder_enabled) "
            "values (%s, false)",
            (USER_C,),
        )
        # Client C files a deletion request attributed to firm A.
        cur.execute(
            "insert into data_deletion_requests (id, user_id, firm_id, notes) "
            "values (%s, %s, %s, 'synthetic request')",
            (REQP_C1, USER_C, FIRM_A),
        )


# ---------------------------------------------------------------------------
# notification_preferences
# ---------------------------------------------------------------------------


def test_preferences_user_reads_own_row(db, privacy):
    with as_user(db, USER_C) as cur:
        cur.execute("select user_id, reminder_enabled from notification_preferences")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == USER_C
    assert rows[0][1] is False


def test_preferences_user_updates_own_row(db, privacy):
    with as_user(db, USER_C) as cur:
        cur.execute(
            "update notification_preferences set email_enabled = false "
            "where user_id = %s",
            (USER_C,),
        )
        assert cur.rowcount == 1


def test_preferences_other_firm_user_sees_nothing(db, privacy):
    with as_user(db, USER_B) as cur:  # firm B attorney
        cur.execute("select user_id from notification_preferences")
        assert cur.fetchall() == []


def test_preferences_outsider_sees_nothing(db, privacy):
    with as_user(db, USER_Q) as cur:
        cur.execute("select user_id from notification_preferences")
        assert cur.fetchall() == []


def test_preferences_firm_admin_reads_firm_user_row(db, privacy):
    with as_user(db, USER_P) as cur:  # firm A admin
        cur.execute("select user_id from notification_preferences")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [USER_C]


def test_preferences_firm_admin_cannot_write_user_row(db, privacy):
    # RLS UPDATE denial surfaces as zero affected rows, not an exception.
    with as_user(db, USER_P) as cur:
        cur.execute(
            "update notification_preferences set email_enabled = true "
            "where user_id = %s",
            (USER_C,),
        )
        assert cur.rowcount == 0
        cur.execute(
            "select email_enabled from notification_preferences "
            "where user_id = %s",
            (USER_C,),
        )
        assert cur.fetchone()[0] is True  # default untouched


def test_preferences_non_admin_staff_cannot_read_user_row(db, privacy):
    with as_user(db, USER_A) as cur:  # firm A attorney, not admin
        cur.execute("select user_id from notification_preferences")
        assert cur.fetchall() == []


def test_preferences_user_cannot_insert_for_someone_else(db, privacy):
    with as_user(db, USER_Q) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into notification_preferences (user_id) values (%s)",
                (USER_C,),
            )


# ---------------------------------------------------------------------------
# data_deletion_requests
# ---------------------------------------------------------------------------


def test_deletion_requests_user_reads_own(db, privacy):
    with as_user(db, USER_C) as cur:
        cur.execute("select id from data_deletion_requests")
        assert [r[0] for r in cur.fetchall()] == [REQP_C1]


def test_deletion_requests_user_inserts_own(db, privacy):
    with as_user(db, USER_Q) as cur:
        cur.execute(
            "insert into data_deletion_requests (user_id, notes) "
            "values (%s, 'synthetic') returning id"
        , (USER_Q,))
        assert cur.fetchone() is not None


def test_deletion_requests_attorney_reads_firm_queue(db, privacy):
    with as_user(db, USER_A) as cur:  # firm A attorney
        cur.execute("select id from data_deletion_requests")
        assert [r[0] for r in cur.fetchall()] == [REQP_C1]


def test_deletion_requests_other_firm_sees_nothing(db, privacy):
    with as_user(db, USER_B) as cur:  # firm B attorney
        cur.execute("select id from data_deletion_requests")
        assert cur.fetchall() == []


def test_deletion_requests_attorney_cannot_triage(db, privacy):
    # RLS UPDATE denial surfaces as zero affected rows, not an exception.
    with as_user(db, USER_A) as cur:
        cur.execute(
            "update data_deletion_requests set status = 'in_review' "
            "where id = %s",
            (REQP_C1,),
        )
        assert cur.rowcount == 0
        cur.execute(
            "select status from data_deletion_requests where id = %s",
            (REQP_C1,),
        )
        assert cur.fetchone()[0] == "open"  # unchanged


def test_deletion_requests_admin_triages_status(db, privacy):
    with as_user(db, USER_P) as cur:
        cur.execute(
            "update data_deletion_requests "
            "set status = 'in_review', processed_at = now(), notes = 'triaged' "
            "where id = %s",
            (REQP_C1,),
        )
        assert cur.rowcount == 1
        cur.execute(
            "select status, notes from data_deletion_requests where id = %s",
            (REQP_C1,),
        )
        row = cur.fetchone()
        assert row[0] == "in_review"
        assert row[1] == "triaged"


def test_deletion_requests_admin_cannot_reassign_user(db, privacy):
    with as_user(db, USER_P) as cur:
        with pytest.raises(errors.RaiseException):
            cur.execute(
                "update data_deletion_requests set user_id = %s where id = %s",
                (USER_Q, REQP_C1),
            )


def test_deletion_requests_admin_cannot_rewrite_requested_at(db, privacy):
    with as_user(db, USER_P) as cur:
        with pytest.raises(errors.RaiseException):
            cur.execute(
                "update data_deletion_requests set requested_at = now() "
                "where id = %s",
                (REQP_C1,),
            )


def test_caller_firm_role_helper(db, privacy):
    """Sanity: the new definer helper answers only about the caller."""
    with as_user(db, USER_P) as cur:
        cur.execute("select public.caller_firm_role(%s)", (FIRM_A,))
        assert cur.fetchone()[0] == "firm_admin"
        cur.execute("select public.caller_firm_role(%s)", (FIRM_B,))
        assert cur.fetchone()[0] is None
    with as_user(db, USER_Q) as cur:
        cur.execute("select public.is_firm_admin_of_user(%s)", (USER_C,))
        assert cur.fetchone()[0] is False
    with as_user(db, USER_P) as cur:
        cur.execute("select public.is_firm_admin_of_user(%s)", (USER_C,))
        assert cur.fetchone()[0] is True
