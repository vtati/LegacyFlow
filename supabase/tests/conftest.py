"""Pytest integration suite for LegacyFlow Slice 1 RLS policies.

Database selection:
  * If the DATABASE_URL env var is set, tests run against it (CI path with a
    postgres service). The database must be THROWAWAY: the session fixture
    drops and recreates the public schema before applying migrations.
  * Otherwise, tests boot a user-space Postgres via pgserver
    (pgserver.get_server('/tmp/lf-pgdata')).
  * If neither is available, the suite skips with "no database available".

Auth model: Supabase's auth.uid() is shimmed as a SQL function reading the
session GUC ``app.user_id``. Each test runs inside an explicit transaction
with ``SET ROLE lf_app`` (a non-owner, non-superuser role, so RLS is actually
enforced) and the GUC set to the synthetic user under test; the transaction
is rolled back afterwards, so tests never leak state.

All fixture data is synthetic. Nothing sensitive is printed.
"""

import contextlib
import os
import pathlib
import uuid

import pytest

SUPABASE_DIR = pathlib.Path(__file__).resolve().parent.parent
MIGRATION_FILE = SUPABASE_DIR / "migrations" / "20260926220000_foundation_slice1.sql"
SEED_FILE = SUPABASE_DIR / "seeds" / "01_jurisdictions.sql"

# ---------------------------------------------------------------------------
# Synthetic fixture identities (fixed UUIDs; never real data)
# ---------------------------------------------------------------------------
FIRM_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
FIRM_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

USER_A = uuid.UUID("10000000-0000-0000-0000-000000000000")  # firm A attorney
USER_B = uuid.UUID("20000000-0000-0000-0000-000000000000")  # firm B attorney
USER_C = uuid.UUID("30000000-0000-0000-0000-000000000000")  # client: household member only

HOUSEHOLD_A = uuid.UUID("40000000-0000-0000-0000-000000000000")
HOUSEHOLD_B = uuid.UUID("50000000-0000-0000-0000-000000000000")

MATTER_A = uuid.UUID("60000000-0000-0000-0000-000000000000")
MATTER_B = uuid.UUID("70000000-0000-0000-0000-000000000000")

# Seed-file fixed IDs (duplicated here for assertions; seeds are the source)
VA_JURISDICTION = uuid.UUID("11111111-1111-1111-1111-111111111111")
VA_PACK = uuid.UUID("22222222-2222-2222-2222-222222222222")
XZ_JURISDICTION = uuid.UUID("33333333-3333-3333-3333-333333333333")
XZ_PACK = uuid.UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture(scope="session")
def db_url():
    """Connection string: DATABASE_URL (CI) or a local pgserver instance."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        import pgserver
    except ImportError:
        pytest.skip("no database available (set DATABASE_URL or install pgserver)")
    server = pgserver.get_server("/tmp/lf-pgdata")
    return server.get_uri()


@pytest.fixture(scope="session")
def db(db_url):
    """Fresh database with auth stub, migration, seeds, roles, fixtures."""
    import psycopg

    conn = psycopg.connect(db_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            # --- fresh-database reset (tests require a throwaway database)
            # NOTE: both public and the auth shim schema are reset so reruns
            # against a reused pgserver data dir stay hermetic.
            cur.execute("drop schema if exists public cascade")
            cur.execute("create schema public")
            cur.execute("drop schema if exists auth cascade")
            cur.execute("create schema auth")
            # --- Supabase auth stub (auth.users + auth.uid() shim)
            cur.execute("create table auth.users (id uuid primary key)")
            cur.execute(
                """
                create or replace function auth.uid() returns uuid
                language sql stable
                as $$ select nullif(current_setting('app.user_id', true), '')::uuid $$;
                """
            )
            # --- migration + seeds (seeds applied twice: idempotency check)
            cur.execute(MIGRATION_FILE.read_text())
            cur.execute(SEED_FILE.read_text())
            cur.execute(SEED_FILE.read_text())
            cur.execute(
                "select count(*) from jurisdictions where id in "
                "('11111111-1111-1111-1111-111111111111','33333333-3333-3333-3333-333333333333')"
            )
            assert cur.fetchone()[0] == 2, "seed jurisdictions missing after double apply"
            cur.execute(
                "select count(*) from jurisdiction_packs where id in "
                "('22222222-2222-2222-2222-222222222222','44444444-4444-4444-4444-444444444444')"
            )
            assert cur.fetchone()[0] == 2, "seed packs missing after double apply"

            # --- least-privilege test role (RLS is enforced for non-owners)
            cur.execute("drop role if exists lf_app")
            cur.execute("create role lf_app nologin")
            cur.execute("grant usage on schema public to lf_app")
            cur.execute("grant usage on schema auth to lf_app")
            cur.execute("grant select, insert, update, delete on all tables in schema public to lf_app")
            cur.execute("grant execute on all functions in schema public to lf_app")
            cur.execute("grant execute on function auth.uid() to lf_app")

            # --- synthetic fixtures (inserted as owner: RLS bypassed)
            cur.execute(
                "insert into auth.users (id) values (%s), (%s), (%s)",
                (USER_A, USER_B, USER_C),
            )
            cur.execute(
                "insert into profiles (id, display_name, email) values "
                "(%s, 'Synthetic Attorney A', 'attorney-a@example.test'), "
                "(%s, 'Synthetic Attorney B', 'attorney-b@example.test'), "
                "(%s, 'Synthetic Client C', 'client-c@example.test')",
                (USER_A, USER_B, USER_C),
            )
            cur.execute(
                "insert into firms (id, legal_name, display_name, status) values "
                "(%s, 'Synthetic Firm A PLLC', 'Synthetic Firm A', 'active'), "
                "(%s, 'Synthetic Firm B PLLC', 'Synthetic Firm B', 'active')",
                (FIRM_A, FIRM_B),
            )
            cur.execute(
                "insert into firm_memberships (firm_id, user_id, role, status) values "
                "(%s, %s, 'attorney', 'active'), "
                "(%s, %s, 'attorney', 'active')",
                (FIRM_A, USER_A, FIRM_B, USER_B),
            )
            cur.execute(
                "insert into households (id, firm_id, display_name, created_by_user_id) values "
                "(%s, %s, 'Synthetic Household A', %s), "
                "(%s, %s, 'Synthetic Household B', %s)",
                (HOUSEHOLD_A, FIRM_A, USER_A, HOUSEHOLD_B, FIRM_B, USER_B),
            )
            # USER_C is a client with NO firm membership: household path only
            cur.execute(
                "insert into household_members (household_id, user_id, relationship_role, access_status) values "
                "(%s, %s, 'primary_client', 'active')",
                (HOUSEHOLD_A, USER_C),
            )
            cur.execute(
                "insert into matters (id, firm_id, household_id, primary_jurisdiction_id, "
                "jurisdiction_pack_id, plan_type, status, assigned_attorney_id) values "
                "(%s, %s, %s, %s, %s, 'will', 'intake', %s), "
                "(%s, %s, %s, %s, %s, 'will', 'intake', %s)",
                (
                    MATTER_A, FIRM_A, HOUSEHOLD_A, VA_JURISDICTION, VA_PACK, USER_A,
                    MATTER_B, FIRM_B, HOUSEHOLD_B, VA_JURISDICTION, VA_PACK, USER_B,
                ),
            )
            cur.execute(
                "insert into matter_status_history (matter_id, from_status, to_status, reason_code, actor_user_id) values "
                "(%s, null, 'intake', 'created', %s), "
                "(%s, null, 'intake', 'created', %s)",
                (MATTER_A, USER_A, MATTER_B, USER_B),
            )
            cur.execute(
                "insert into audit_events (firm_id, matter_id, actor_user_id, actor_type, action, resource_type, resource_id) values "
                "(%s, %s, %s, 'attorney', 'matter.created', 'matter', %s), "
                "(%s, %s, %s, 'attorney', 'matter.created', 'matter', %s)",
                (FIRM_A, MATTER_A, USER_A, MATTER_A, FIRM_B, MATTER_B, USER_B, MATTER_B),
            )
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def as_user(conn, user_id):
    """Run queries as the least-privilege role with auth.uid() = user_id.

    user_id=None simulates an unauthenticated caller. Everything runs in a
    transaction that is always rolled back, so tests are isolated.
    """
    with conn.cursor() as cur:
        cur.execute("begin")
        cur.execute("set role lf_app")
        if user_id is None:
            cur.execute("reset app.user_id")
        else:
            cur.execute("select set_config('app.user_id', %s, false)", (str(user_id),))
        try:
            yield cur
        finally:
            cur.execute("rollback")
            cur.execute("reset role")
            cur.execute("reset app.user_id")
