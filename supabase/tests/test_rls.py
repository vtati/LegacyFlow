"""RLS allow/deny integration tests for LegacyFlow Slice 1 (§12.1 RLS layer).

Covers §12.2 scenarios 1 (two firms cannot access each other's matters /
audit events) and 2 (client collaborator cannot access unrelated matters),
plus write-path deny cases and pack-config distinguishability (scenario 6:
synthetic pack produces different config without code change).
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
    VA_JURISDICTION,
    VA_PACK,
    XZ_PACK,
    as_user,
)


def _matter_ids(cur):
    cur.execute("select id from matters order by id")
    return {row[0] for row in cur.fetchall()}


# --- §12.2 scenario 1: cross-firm isolation ---------------------------------

def test_firm_a_attorney_sees_own_matter_not_other(db):
    """(a) as uA, SELECT matters returns mA but not mB."""
    with as_user(db, USER_A) as cur:
        ids = _matter_ids(cur)
    assert MATTER_A in ids
    assert MATTER_B not in ids


def test_firm_b_attorney_sees_own_matter_not_other(db):
    """(b) as uB, SELECT matters returns mB but not mA."""
    with as_user(db, USER_B) as cur:
        ids = _matter_ids(cur)
    assert MATTER_B in ids
    assert MATTER_A not in ids


def test_household_member_path_sees_own_matter(db):
    """(c) client linked via household_members (no firm membership) sees
    their own household's matter, not the other firm's."""
    with as_user(db, USER_C) as cur:
        ids = _matter_ids(cur)
    assert MATTER_A in ids
    assert MATTER_B not in ids


def test_unauthenticated_sees_zero_matters(db):
    """(d) no app.user_id set -> zero matters (and zero reference rows)."""
    with as_user(db, None) as cur:
        assert _matter_ids(cur) == set()
        cur.execute("select count(*) from jurisdictions")
        assert cur.fetchone()[0] == 0


def test_firm_a_cannot_read_firm_b_roster_or_households(db):
    """(e) uA sees only firm A's memberships and households."""
    with as_user(db, USER_A) as cur:
        cur.execute("select firm_id, user_id from firm_memberships")
        rows = cur.fetchall()
        assert rows, "expected to see own firm's roster"
        assert {r[0] for r in rows} == {FIRM_A}
        assert USER_B not in {r[1] for r in rows}

        cur.execute("select id from households")
        assert {r[0] for r in cur.fetchall()} == {HOUSEHOLD_A}


def test_cross_firm_matter_insert_denied(db):
    """(f) uA cannot INSERT a matter into firm B (write deny path)."""
    with as_user(db, USER_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into matters (firm_id, household_id, primary_jurisdiction_id, "
                "jurisdiction_pack_id, plan_type, status) "
                "values (%s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS')",
                (FIRM_B, HOUSEHOLD_B, VA_JURISDICTION, VA_PACK),
            )


def test_own_firm_matter_insert_allowed(db):
    """Positive write path: uA CAN insert a matter into firm A."""
    with as_user(db, USER_A) as cur:
        new_id = uuid.uuid4()
        cur.execute(
            "insert into matters (id, firm_id, household_id, primary_jurisdiction_id, "
            "jurisdiction_pack_id, plan_type, status) "
            "values (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS')",
            (new_id, FIRM_A, HOUSEHOLD_A, VA_JURISDICTION, VA_PACK),
        )
        cur.execute("select count(*) from matters where id = %s", (new_id,))
        assert cur.fetchone()[0] == 1


def test_household_member_cannot_insert_matter(db):
    """Client (household path) cannot create matters: firm-side only."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into matters (firm_id, household_id, primary_jurisdiction_id, "
                "jurisdiction_pack_id, plan_type, status) "
                "values (%s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS')",
                (FIRM_A, HOUSEHOLD_A, VA_JURISDICTION, VA_PACK),
            )


def test_audit_events_isolated_per_firm(db):
    """§12.2 scenario 1: firms cannot read each other's audit events."""
    with as_user(db, USER_A) as cur:
        cur.execute("select firm_id from audit_events")
        assert {r[0] for r in cur.fetchall()} == {FIRM_A}
    with as_user(db, USER_B) as cur:
        cur.execute("select firm_id from audit_events")
        assert {r[0] for r in cur.fetchall()} == {FIRM_B}
    with as_user(db, None) as cur:
        cur.execute("select count(*) from audit_events")
        assert cur.fetchone()[0] == 0


def test_audit_events_append_only_for_clients(db):
    """No direct writes on audit_events for client roles.

    INSERT with no policy raises (WITH CHECK violation); UPDATE/DELETE with
    no policy silently touch zero rows (deny-by-filter). Each statement runs
    in its own transaction since a denied INSERT aborts its transaction.
    """
    with as_user(db, USER_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into audit_events (firm_id, actor_type, action, resource_type) "
                "values (%s, 'attorney', 'test.write', 'matter')",
                (FIRM_A,),
            )
    with as_user(db, USER_A) as cur:
        cur.execute("update audit_events set action = 'tampered' where firm_id = %s", (FIRM_A,))
        assert cur.rowcount == 0
    with as_user(db, USER_A) as cur:
        cur.execute("delete from audit_events where firm_id = %s", (FIRM_A,))
        assert cur.rowcount == 0
    # and the rows are untouched
    with as_user(db, USER_A) as cur:
        cur.execute("select action from audit_events where firm_id = %s", (FIRM_A,))
        assert [r[0] for r in cur.fetchall()] == ["matter.created"]


def test_matter_status_history_follows_parent_access(db):
    """Status history is visible exactly when the parent matter is."""
    with as_user(db, USER_A) as cur:
        cur.execute("select matter_id from matter_status_history")
        assert {r[0] for r in cur.fetchall()} == {MATTER_A}
    with as_user(db, USER_C) as cur:  # household path also grants history
        cur.execute("select matter_id from matter_status_history")
        assert {r[0] for r in cur.fetchall()} == {MATTER_A}
    with as_user(db, USER_B) as cur:
        cur.execute("select matter_id from matter_status_history")
        assert {r[0] for r in cur.fetchall()} == {MATTER_B}


def test_profiles_self_and_colleagues_only(db):
    """uA reads own profile + firm A colleagues; never firm B profiles.
    USER_C has no firm membership, so per the minimal policy uA cannot see
    them; USER_C can still read their own profile."""
    with as_user(db, USER_A) as cur:
        cur.execute("select id from profiles")
        ids = {r[0] for r in cur.fetchall()}
        assert USER_A in ids
        assert USER_B not in ids
        assert USER_C not in ids
    with as_user(db, USER_C) as cur:
        cur.execute("select id from profiles")
        assert {r[0] for r in cur.fetchall()} == {USER_C}


def test_jurisdiction_reference_readable_not_writable(db):
    """Reference data: any authenticated user can read; client writes denied."""
    with as_user(db, USER_A) as cur:
        cur.execute("select subdivision_code from jurisdictions order by 1")
        assert [r[0] for r in cur.fetchall()] == ["VA", "XZ"]
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                "insert into jurisdictions (country_code, subdivision_code, display_name, jurisdiction_type) "
                "values ('US', 'YY', 'Synthetic', 'us_state')"
            )


def test_packs_distinguishable_by_config(db):
    """§12.2 scenario 6: VA vs synthetic XZ packs differ by *config* —
    application code must read the manifest, never hard-code state behavior.

    Slice 2 syncs the pack manifests to the YAML files under content-packs/
    (us-va v0.1.0, us-xz-synthetic v1.0.0); the distinguishing fields below
    come from those files.
    """
    with as_user(db, USER_A) as cur:
        cur.execute(
            "select manifest->'pack'->>'display_name', "
            "       manifest->'requirements'->'required_question_set_versions'->>'household', "
            "       manifest->'requirements'->'required_question_set_versions'->>'guardians', "
            "       manifest->'requirements'->'required_question_set_versions'->>'assets', "
            "       jsonb_array_length(manifest->'sources'), "
            "       manifest->'sources'->0->>'source_id' "
            "from jurisdiction_packs where id = %s",
            (VA_PACK,),
        )
        va = cur.fetchone()
        cur.execute(
            "select manifest->'pack'->>'display_name', "
            "       manifest->'requirements'->'required_question_set_versions'->>'household', "
            "       manifest->'requirements'->'required_question_set_versions'->>'guardians', "
            "       manifest->'requirements'->'required_question_set_versions'->>'assets', "
            "       jsonb_array_length(manifest->'sources'), "
            "       manifest->'sources'->0->>'source_id' "
            "from jurisdiction_packs where id = %s",
            (XZ_PACK,),
        )
        xz = cur.fetchone()
        cur.execute("select manifest from jurisdiction_packs where id in (%s, %s)", (VA_PACK, XZ_PACK))
        manifests = [r[0] for r in cur.fetchall()]
    assert va == ("Virginia", "1.0.0", "1.0.0", "1.0.0", 2, "va-code-will-execution")
    assert xz == ("Xanadu (synthetic test jurisdiction)", "1.0.0", None, None, 1, "xz-synthetic-statute")
    assert va != xz
    assert manifests[0] != manifests[1]


def test_updated_at_trigger_fires(db):
    """set_updated_at() bumps updated_at on row update (profiles, matters)."""
    conn = db  # owner connection: RLS bypassed for fixture manipulation
    with conn.cursor() as cur:
        cur.execute(
            "update matters set updated_at = '2000-01-01 00:00:00+00' where id = %s",
            (MATTER_A,),
        )
        conn.commit()
    try:
        with as_user(db, USER_A) as cur:
            cur.execute("update matters set status = 'INTAKE_IN_PROGRESS' where id = %s", (MATTER_A,))
            assert cur.rowcount == 1
            cur.execute("select updated_at > '2000-01-01 00:00:00+00' from matters where id = %s", (MATTER_A,))
            assert cur.fetchone()[0] is True
    finally:
        # restore: trigger fires again, which is fine
        with conn.cursor() as cur:
            cur.execute("update matters set status = 'INTAKE_IN_PROGRESS' where id = %s", (MATTER_A,))
            conn.commit()


def test_cross_firm_matter_update_is_noop(db):
    """uA's UPDATE of firm B's matter touches zero rows (deny-by-filter)."""
    with as_user(db, USER_A) as cur:
        cur.execute("update matters set status = 'INTAKE_IN_PROGRESS' where id = %s", (MATTER_B,))
        assert cur.rowcount == 0


def test_seed_idempotency_explicit(db):
    """Re-running seeds changes nothing (fixed UUIDs + ON CONFLICT DO NOTHING)."""
    import pathlib

    seed_sql = pathlib.Path(__file__).resolve().parent.parent / "seeds" / "01_jurisdictions.sql"
    with db.cursor() as cur:
        cur.execute("select count(*) from jurisdictions")
        before_j = cur.fetchone()[0]
        cur.execute("select count(*) from jurisdiction_packs")
        before_p = cur.fetchone()[0]
        cur.execute(seed_sql.read_text())  # third apply overall
        cur.execute("select count(*) from jurisdictions")
        after_j = cur.fetchone()[0]
        cur.execute("select count(*) from jurisdiction_packs")
        after_p = cur.fetchone()[0]
    assert (before_j, before_p) == (after_j, after_p) and before_j >= 2 and before_p >= 2
