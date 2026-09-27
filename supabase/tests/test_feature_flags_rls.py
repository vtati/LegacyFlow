"""RLS allow/deny integration tests for feature flags + template lifecycle.

Covers the new ``feature_flags`` table (firm-admin reads of flags affecting
their firm; firm-admin writes limited to firm-scope flags for their own
firm; other-firm and client writes/reads denied) and the new
``document_templates.status`` lifecycle column (readable per the existing
firm-member policies; clients still denied).

Reuses the conftest ``db`` fixture (all migrations applied in order) and the
``as_user`` helper: each test runs as the least-privilege ``lf_app`` role
with auth.uid() set to the synthetic user under test, inside a transaction
that is always rolled back.

All fixture data is synthetic. Nothing sensitive is printed.
"""

import uuid

import pytest
from conftest import (
    FIRM_A,
    FIRM_B,
    USER_A,
    USER_B,
    USER_C,
    as_user,
)
from psycopg import errors

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
ADMIN_A = uuid.UUID("b0000000-0000-0000-0000-000000000000")  # firm A firm_admin
ADMIN_B = uuid.UUID("b1000000-0000-0000-0000-000000000000")  # firm B firm_admin

FLAG_ENV = uuid.UUID("c0000000-0000-0000-0000-000000000000")  # environment scope
FLAG_FIRM_A = uuid.UUID("c1000000-0000-0000-0000-000000000000")  # firm A scope
FLAG_FIRM_B = uuid.UUID("c2000000-0000-0000-0000-000000000000")  # firm B scope
FLAG_JURIS = uuid.UUID("c3000000-0000-0000-0000-000000000000")  # jurisdiction scope

TPL_A = uuid.UUID("d0000000-0000-0000-0000-000000000000")  # firm A template


@pytest.fixture(scope="module", autouse=True)
def _extra_fixtures(db):
    """Insert firm-admin users and flag/template rows (as owner; idempotent).

    Torn down at the end of this module so exact-set assertions in other
    RLS test modules are unaffected.
    """
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id) values (%s), (%s) on conflict (id) do nothing",
            (ADMIN_A, ADMIN_B),
        )
        cur.execute(
            "insert into profiles (id, display_name, email) values "
            "(%s, 'Synthetic Admin A', 'admin-a@example.test'), "
            "(%s, 'Synthetic Admin B', 'admin-b@example.test') "
            "on conflict (id) do nothing",
            (ADMIN_A, ADMIN_B),
        )
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status) values "
            "(%s, %s, 'firm_admin', 'active'), "
            "(%s, %s, 'firm_admin', 'active') "
            "on conflict (firm_id, user_id) do nothing",
            (FIRM_A, ADMIN_A, FIRM_B, ADMIN_B),
        )
        # Flag rows seeded as owner (RLS bypassed); policies gate app-role access.
        cur.execute(
            "insert into feature_flags (id, key, scope, scope_ref, is_enabled,"
            " created_by_user_id) values "
            "(%s, 'ai_assistance', 'environment', '', true, %s), "
            "(%s, 'document_assembly', 'firm', %s, true, %s), "
            "(%s, 'document_assembly', 'firm', %s, false, %s), "
            "(%s, 'funding_reminders', 'jurisdiction', 'us-va', true, %s) "
            "on conflict (id) do nothing",
            (
                FLAG_ENV,
                ADMIN_A,
                FLAG_FIRM_A,
                str(FIRM_A),
                ADMIN_A,
                FLAG_FIRM_B,
                str(FIRM_B),
                ADMIN_B,
                FLAG_JURIS,
                ADMIN_A,
            ),
        )
        cur.execute(
            "insert into document_templates (id, firm_id, name, template_type,"
            " pack_id, version, content, created_by_user_id, status) values "
            "(%s, %s, 'Synthetic Will', 'will', 'us-va', '1.0.0',"
            " '{\"body\": \"synthetic\"}'::jsonb, %s, 'approved') "
            "on conflict (id) do nothing",
            (TPL_A, FIRM_A, ADMIN_A),
        )
    yield
    # Teardown: remove everything this module inserted.
    with db.cursor() as cur:
        cur.execute("delete from document_templates where id = %s", (TPL_A,))
        cur.execute(
            "delete from feature_flags where id in (%s, %s, %s, %s)",
            (FLAG_ENV, FLAG_FIRM_A, FLAG_FIRM_B, FLAG_JURIS),
        )
        cur.execute(
            "delete from feature_flags where created_by_user_id in (%s, %s) and scope = 'firm'",
            (ADMIN_A, ADMIN_B),
        )
        cur.execute(
            "delete from firm_memberships where user_id in (%s, %s)",
            (ADMIN_A, ADMIN_B),
        )
        cur.execute("delete from profiles where id in (%s, %s)", (ADMIN_A, ADMIN_B))
        cur.execute("delete from auth.users where id in (%s, %s)", (ADMIN_A, ADMIN_B))


# ---------------------------------------------------------------------------
# feature_flags — reads
# ---------------------------------------------------------------------------


def test_firm_admin_reads_flags_affecting_their_firm(db):
    """A firm admin sees environment, jurisdiction, and own-firm flags."""
    with as_user(db, ADMIN_A) as cur:
        cur.execute("select id from feature_flags order by id")
        seen = {row[0] for row in cur.fetchall()}
    # Own firm flag + environment + jurisdiction flags are visible...
    assert FLAG_ENV in seen
    assert FLAG_JURIS in seen
    assert FLAG_FIRM_A in seen
    # ...but the other firm's flag is not.
    assert FLAG_FIRM_B not in seen


def test_other_firm_admin_cannot_read_foreign_firm_flag(db):
    with as_user(db, ADMIN_B) as cur:
        cur.execute("select id from feature_flags where id = %s", (FLAG_FIRM_A,))
        assert cur.fetchone() is None
        cur.execute("select id from feature_flags where id = %s", (FLAG_FIRM_B,))
        assert cur.fetchone() is not None


def test_non_admin_firm_member_cannot_read_flags(db):
    """USER_A is a firm A attorney (not firm_admin): no flag reads."""
    with as_user(db, USER_A) as cur:
        cur.execute("select id from feature_flags")
        assert cur.fetchall() == []


def test_client_cannot_read_flags(db):
    """USER_C is a household client with no firm membership: denied."""
    with as_user(db, USER_C) as cur:
        cur.execute("select id from feature_flags")
        assert cur.fetchall() == []


# ---------------------------------------------------------------------------
# feature_flags — writes
# ---------------------------------------------------------------------------


def test_firm_admin_write_allowed_for_own_firm_flag(db):
    new_id = uuid.uuid4()
    with as_user(db, ADMIN_A) as cur:
        cur.execute(
            "insert into feature_flags (id, key, scope, scope_ref, is_enabled,"
            " created_by_user_id) values (%s, 'funding_reminders', 'firm', %s, false, %s)"
            " returning id",
            (new_id, str(FIRM_A), ADMIN_A),
        )
        assert cur.fetchone()[0] == new_id
        cur.execute(
            "update feature_flags set is_enabled = true where id = %s returning is_enabled",
            (new_id,),
        )
        assert cur.fetchone()[0] is True


def test_firm_admin_write_denied_for_other_firm_flag(db):
    with as_user(db, ADMIN_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into feature_flags (key, scope, scope_ref, is_enabled) values "
                "('funding_reminders', 'firm', %s, false)",
                (str(FIRM_B),),
            )
    with as_user(db, ADMIN_A) as cur:
        cur.execute("update feature_flags set is_enabled = true where id = %s", (FLAG_FIRM_B,))
        assert cur.rowcount == 0


def test_firm_admin_write_denied_for_global_scopes(db):
    """Firm admins cannot write environment/jurisdiction scope flags."""
    with as_user(db, ADMIN_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into feature_flags (key, scope, scope_ref, is_enabled) values "
                "('ai_assistance', 'environment', '', false)"
            )
    with as_user(db, ADMIN_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into feature_flags (key, scope, scope_ref, is_enabled) values "
                "('funding_reminders', 'jurisdiction', 'us-va', false)"
            )


def test_non_admin_and_client_writes_denied(db):
    with as_user(db, USER_A) as cur:  # firm A attorney, not admin
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into feature_flags (key, scope, scope_ref, is_enabled) values "
                "('funding_reminders', 'firm', %s, false)",
                (str(FIRM_A),),
            )
    with as_user(db, USER_C) as cur:  # client
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into feature_flags (key, scope, scope_ref, is_enabled) values "
                "('funding_reminders', 'firm', %s, false)",
                (str(FIRM_A),),
            )


# ---------------------------------------------------------------------------
# document_templates — status lifecycle column
# ---------------------------------------------------------------------------


def test_template_status_readable_by_firm_members(db):
    """The new status column is visible through the existing firm policies."""
    with as_user(db, USER_A) as cur:  # firm A attorney
        cur.execute(
            "select status, approved_by_user_id, approved_at from document_templates where id = %s",
            (TPL_A,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "approved"


def test_template_status_not_visible_to_clients(db):
    """Clients still cannot read templates at all (existing policy)."""
    with as_user(db, USER_C) as cur:
        cur.execute("select status from document_templates where id = %s", (TPL_A,))
        assert cur.fetchone() is None


def test_template_status_not_visible_to_other_firms(db):
    with as_user(db, USER_B) as cur:  # firm B attorney
        cur.execute("select status from document_templates where id = %s", (TPL_A,))
        assert cur.fetchone() is None
