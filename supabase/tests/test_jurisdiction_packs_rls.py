"""RLS deny-path tests for `jurisdiction_packs` (LegacyFlow Slice 1 reference data).

Verified policy semantics in
supabase/migrations/20260926220000_foundation_slice1.sql §5b
("jurisdictions / jurisdiction_packs (reference data)"):

  * SELECT: any authenticated user (auth.uid() is not null).
  * INSERT / UPDATE / DELETE: NO policy at all — deny by default. Packs are
    curated by the content pipeline via the service role; neither clients
    nor firm members get client-role writes.

Reuses the conftest `db` fixture (seeds already loaded) and the `as_user`
helper: each test runs as the least-privilege `lf_app` role inside a
transaction that is always rolled back.

All fixture data is synthetic. Nothing sensitive is printed.
"""

import pytest
from psycopg import errors

from conftest import (
    USER_A,
    USER_C,
    VA_JURISDICTION,
    VA_PACK,
    XZ_PACK,
    as_user,
)


# --- read allow paths (control: the denies below are scoped correctly) ---------

def test_authenticated_users_can_read_packs(db):
    """Both a firm attorney (USER_A) and a household client (USER_C) can
    read the seeded packs — the only SELECT policy is 'for authenticated'."""
    for user in (USER_A, USER_C):
        with as_user(db, user) as cur:
            cur.execute("select id from jurisdiction_packs order by id")
            ids = {r[0] for r in cur.fetchall()}
        assert VA_PACK in ids and XZ_PACK in ids


# --- read deny path ---------------------------------------------------------------

def test_anonymous_cannot_read_packs(db):
    """No app.user_id -> zero pack rows (the SELECT policy requires
    auth.uid() is not null)."""
    with as_user(db, None) as cur:
        cur.execute("select count(*) from jurisdiction_packs")
        assert cur.fetchone()[0] == 0
        cur.execute("select id from jurisdiction_packs where id = %s", (VA_PACK,))
        assert cur.fetchone() is None


# --- write deny paths --------------------------------------------------------------

def _insert_pack(cur):
    cur.execute(
        "insert into jurisdiction_packs (jurisdiction_id, version, status, "
        "effective_from, manifest, source_bundle_hash) "
        "values (%s, '99.0.0-synthetic', 'draft', current_date, '{}', 'synthetic-hash')",
        (VA_JURISDICTION,),
    )


def test_client_cannot_write_packs(db):
    """USER_C (household client) cannot insert/update/delete packs:
    there is no write policy, so inserts raise and updates/deletes are
    no-ops on invisible rows."""
    with as_user(db, USER_C) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_pack(cur)
    with as_user(db, USER_C) as cur:
        cur.execute("update jurisdiction_packs set status = 'approved' where id = %s", (VA_PACK,))
        assert cur.rowcount == 0
    with as_user(db, USER_C) as cur:
        cur.execute("delete from jurisdiction_packs where id = %s", (VA_PACK,))
        assert cur.rowcount == 0


def test_firm_member_cannot_write_packs(db):
    """USER_A (firm attorney) also cannot write packs — writes are
    deny-by-default for every non-service role."""
    with as_user(db, USER_A) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_pack(cur)
    with as_user(db, USER_A) as cur:
        cur.execute("update jurisdiction_packs set status = 'retired' where id = %s", (XZ_PACK,))
        assert cur.rowcount == 0
    with as_user(db, USER_A) as cur:
        cur.execute("delete from jurisdiction_packs where id = %s", (XZ_PACK,))
        assert cur.rowcount == 0


def test_anonymous_cannot_write_packs(db):
    """Anonymous callers cannot insert packs either."""
    with as_user(db, None) as cur:
        with pytest.raises(errors.InsufficientPrivilege):
            _insert_pack(cur)


# --- no-write leak regression --------------------------------------------------------

def test_pack_rows_survive_deny_probes(db):
    """The seeded packs still exist after the deny probes above (the
    rolled-back transactions changed nothing)."""
    with as_user(db, USER_A) as cur:
        cur.execute("select count(*) from jurisdiction_packs where id in (%s, %s)", (VA_PACK, XZ_PACK))
        assert cur.fetchone()[0] == 2
