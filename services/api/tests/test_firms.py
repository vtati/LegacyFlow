"""Tests for the firm roster endpoints (GET /v1/firms/current, /v1/firms/members).

Runs against a local pgserver instance — never against DATABASE_URL or
live Supabase. All fixture people and firms are synthetic.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import (
    STORAGE_BUCKETS_STUB,
    TEST_AUDIENCE,
    TEST_ISSUER,
    TEST_SECRET,
    mint_token,
)

ADMIN_SUB = "aaaaaaaa-1111-1111-1111-111111111111"
STAFF_SUB = "cccccccc-3333-3333-3333-333333333333"
CLIENT_SUB = "dddddddd-4444-4444-4444-444444444444"

USERS = {
    ADMIN_SUB: "admin@example.test",
    STAFF_SUB: "staff@example.test",
    CLIENT_SUB: "client@example.test",
}

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
-- raw_user_meta_data/raw_app_meta_data: the handle_new_user trigger
-- (migration 20261001130000) reads them; mirror the real auth.users shape.
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
""" + STORAGE_BUCKETS_STUB

TRUNCATE_TABLES = (
    "audit_events, escalations, intake_answers, matter_status_history, matters,"
    " invitations, nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles, auth.users"
)


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start pgserver once; apply auth stub + migrations + seeds."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-firms")))
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
    """Open connection; truncate all non-reference tables for isolation."""
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def api_client(pg_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient for the full app with pgserver as DATABASE_URL."""
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# DB setup helpers (synthetic data only)
# ---------------------------------------------------------------------------
def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _create_user(db, sub: str) -> None:
    email = USERS[sub]
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
        (sub, email, f"Synthetic {email}"),
    )


def _setup(db) -> str:
    for sub in (ADMIN_SUB, STAFF_SUB, CLIENT_SUB):
        _create_user(db, sub)
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, "Synthetic Firm", "Synthetic Firm"),
    )
    for sub, role in ((ADMIN_SUB, "firm_admin"), (STAFF_SUB, "staff")):
        _exec(
            db,
            "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
            " VALUES (%s, %s, %s, 'active')",
            (firm_id, sub, role),
        )
    return firm_id


def _auth(sub: str, email: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub, email=email or USERS[sub])}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_get_current_firm(api_client, db) -> None:
    _setup(db)
    resp = api_client.get("/v1/firms/current", headers=_auth(ADMIN_SUB))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Synthetic Firm"
    assert body["status"] == "active"


def test_get_current_firm_requires_membership(api_client, db) -> None:
    _setup(db)
    outsider = "99999999-9999-9999-9999-999999999999"
    _exec(
        db,
        "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
        (outsider, "outsider@example.test"),
    )
    resp = api_client.get("/v1/firms/current", headers=_auth(outsider, "outsider@example.test"))
    assert resp.status_code in (401, 403), resp.text


def test_list_members_as_staff(api_client, db) -> None:
    _setup(db)
    resp = api_client.get("/v1/firms/members", headers=_auth(STAFF_SUB))
    assert resp.status_code == 200, resp.text
    members = resp.json()
    roles = {m["role"] for m in members}
    assert {"firm_admin", "staff"} <= roles
    admin = next(m for m in members if m["role"] == "firm_admin")
    assert admin["display_name"] == "Synthetic admin@example.test"
    assert admin["email"] == "admin@example.test"


def test_list_members_forbidden_for_client(api_client, db) -> None:
    _setup(db)
    resp = api_client.get("/v1/firms/members", headers=_auth(CLIENT_SUB))
    assert resp.status_code == 403, resp.text


def test_list_members_other_firm_isolated(api_client, db) -> None:
    _setup(db)
    other_admin = "22222222-2222-2222-2222-222222222222"
    _exec(
        db,
        "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
        (other_admin, "other@example.test"),
    )
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
        (other_admin, "other@example.test", "Other"),
    )
    other_firm = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (other_firm, "Firm B", "Firm B"),
    )
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, 'firm_admin', 'active')",
        (other_firm, other_admin),
    )
    resp = api_client.get("/v1/firms/members", headers=_auth(other_admin, "other@example.test"))
    assert resp.status_code == 200, resp.text
    members = resp.json()
    assert len(members) == 1
    assert members[0]["user_id"] == other_admin
