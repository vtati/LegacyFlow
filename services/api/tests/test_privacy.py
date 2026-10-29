"""Privacy domain API tests.

Exercises ``app/domains/privacy/{repository,service,schemas,router}.py``
against the real privacy migration
(``supabase/migrations/20260927050000_reminders_privacy.sql``), which the
``pg_uri`` fixture applies along with all earlier migrations.

Covered:
  * GET/PUT /v1/me/notification-preferences (own only, upsert semantics)
  * POST /v1/me/unsubscribe (idempotent)
  * GET /v1/me/export (two-user isolation: only the caller's own data)
  * POST/GET /v1/me/deletion-requests (create + list own, audit event)
  * POST /v1/admin/reminders/dispatch (firm_admin only; 403 otherwise;
    501 without REDIS_URL; enqueues the worker actor when configured)
  * RLS allow/deny behavior for notification_preferences and
    data_deletion_requests (via a swapped auth.uid() stub)

All fixture data is synthetic. Nothing here touches main.py or
core/* wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, STORAGE_BUCKETS_STUB, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------

ADMIN_A = "c1111111-1111-1111-1111-111111111111"
ATTORNEY_A = "c2222222-2222-2222-2222-222222222222"
STAFF_A = "c3333333-3333-3333-3333-333333333333"
CLIENT_A = "c4444444-4444-4444-4444-444444444444"
CLIENT_B = "c5555555-5555-5555-5555-555555555555"

USERS = {
    ADMIN_A: "privacy.admin@example.test",
    ATTORNEY_A: "privacy.attorney@example.test",
    STAFF_A: "privacy.staff@example.test",
    CLIENT_A: "privacy.client.a@example.test",
    CLIENT_B: "privacy.client.b@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

TRUNCATE_TABLES = (
    "notification_preferences, data_deletion_requests,"
    " document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " asset_flags, assets, liabilities, auth.users"
)

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

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start pgserver once; apply auth stub + all migrations + seed."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-privacy")))
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute(SEED_FILE.read_text())
    yield uri
    server.cleanup()


@pytest.fixture()
def db(pg_uri: str):  # type: ignore[no-untyped-def]
    """Open connection; truncate all non-reference tables for isolation."""
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def api_client(pg_uri, monkeypatch):  # type: ignore[no-untyped-def]
    """TestClient with pgserver as DATABASE_URL and the test JWT override."""
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    # Rate limiting is covered by its own dedicated tests; leave it off here.
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _exec(db, sql: str, params: tuple = ()) -> None:  # type: ignore[no-untyped-def]
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def create_user(db, sub: str) -> None:  # type: ignore[no-untyped-def]
    email = USERS[sub]
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
        (sub, email, f"Synthetic {email}"),
    )


def create_firm(db, name: str = "Synthetic Privacy Firm") -> str:  # type: ignore[no-untyped-def]
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, name, name),
    )
    return firm_id


def add_firm_member(db, firm_id: str, sub: str, role: str) -> None:  # type: ignore[no-untyped-def]
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, %s, 'active')",
        (firm_id, sub, role),
    )


def create_household(db, firm_id: str, created_by: str) -> str:  # type: ignore[no-untyped-def]
    household_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, firm_id, "Synthetic Household", created_by),
    )
    return household_id


def add_household_member(db, household_id: str, sub: str, role: str) -> None:  # type: ignore[no-untyped-def]
    _exec(
        db,
        "INSERT INTO household_members (household_id, user_id, relationship_role, access_status)"
        " VALUES (%s, %s, %s, 'active')",
        (household_id, sub, role),
    )


def insert_matter(db, firm_id: str, household_id: str) -> str:  # type: ignore[no-untyped-def]
    matter_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
        " completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 0)",
        (
            matter_id,
            firm_id,
            household_id,
            US_VA_JURISDICTION_ID,
            US_VA_PACK_ID,
            "Synthetic Matter",
            json.dumps({"pack_id": "us-va", "jurisdiction_code": "US-VA"}),
        ),
    )
    return matter_id


def insert_person(db, household_id: str, full_name: str) -> str:  # type: ignore[no-untyped-def]
    person_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO persons (id, household_id, full_name, email, kind)"
        " VALUES (%s, %s, %s, %s, 'adult')",
        (person_id, household_id, full_name, f"{full_name}@example.test"),
    )
    return person_id


def insert_decision(db, matter_id: str, decided_by: str) -> str:  # type: ignore[no-untyped-def]
    decision_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO planning_decisions (id, matter_id, decision_key, value, decided_by)"
        " VALUES (%s, %s, 'guardian_choice', %s::jsonb, %s)",
        (decision_id, matter_id, json.dumps({"choice": "synthetic"}), decided_by),
    )
    return decision_id


def insert_asset(db, firm_id: str, household_id: str, created_by: str, label: str) -> str:  # type: ignore[no-untyped-def]
    asset_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO assets (id, firm_id, household_id, asset_type, label,"
        " beneficiary_designation, created_by_user_id)"
        " VALUES (%s, %s, %s, 'bank_account', %s, '{}'::jsonb, %s)",
        (asset_id, firm_id, household_id, label, created_by),
    )
    return asset_id


def auth_headers(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub, email=USERS[sub])}"}


def setup_two_client_tree(db):  # type: ignore[no-untyped-def]
    """Two firms' worth of isolation: firm A (admin+attorney+staff+client A
    with household/matter/person/decision/asset) and firm B (client B with
    their own household/matter/person/asset)."""
    for sub in USERS:
        create_user(db, sub)

    firm_a = create_firm(db, "Firm A")
    add_firm_member(db, firm_a, ADMIN_A, "firm_admin")
    add_firm_member(db, firm_a, ATTORNEY_A, "attorney")
    add_firm_member(db, firm_a, STAFF_A, "staff")
    household_a = create_household(db, firm_a, ADMIN_A)
    add_household_member(db, household_a, CLIENT_A, "primary_client")
    matter_a = insert_matter(db, firm_a, household_a)
    person_a = insert_person(db, household_a, "Synthetic Person A")
    decision_a = insert_decision(db, matter_a, CLIENT_A)
    asset_a = insert_asset(db, firm_a, household_a, CLIENT_A, "Synthetic Asset A")

    firm_b = create_firm(db, "Firm B")
    household_b = create_household(db, firm_b, CLIENT_B)
    add_household_member(db, household_b, CLIENT_B, "primary_client")
    matter_b = insert_matter(db, firm_b, household_b)
    person_b = insert_person(db, household_b, "Synthetic Person B")
    asset_b = insert_asset(db, firm_b, household_b, CLIENT_B, "Synthetic Asset B")

    return {
        "firm_a": firm_a,
        "household_a": household_a,
        "matter_a": matter_a,
        "person_a": person_a,
        "decision_a": decision_a,
        "asset_a": asset_a,
        "firm_b": firm_b,
        "household_b": household_b,
        "matter_b": matter_b,
        "person_b": person_b,
        "asset_b": asset_b,
    }


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


def test_preferences_get_returns_defaults(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    resp = api_client.get("/v1/me/notification-preferences", headers=auth_headers(CLIENT_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == CLIENT_A
    assert body["email_enabled"] is True
    assert body["reminder_enabled"] is True
    assert body["unsubscribed_at"] is None


def test_preferences_update_upserts(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    headers = auth_headers(CLIENT_A)

    resp = api_client.put(
        "/v1/me/notification-preferences",
        json={"reminder_enabled": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reminder_enabled"] is False
    assert resp.json()["email_enabled"] is True  # untouched

    resp = api_client.put(
        "/v1/me/notification-preferences",
        json={"email_enabled": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_enabled"] is False
    assert resp.json()["reminder_enabled"] is False  # preserved across patches

    # GET reflects the stored row (no longer defaults).
    resp = api_client.get("/v1/me/notification-preferences", headers=headers)
    assert resp.json()["email_enabled"] is False


def test_preferences_update_requires_a_field(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    resp = api_client.put(
        "/v1/me/notification-preferences", json={}, headers=auth_headers(CLIENT_A)
    )
    assert resp.status_code == 422, resp.text


def test_unsubscribe_is_idempotent(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    headers = auth_headers(CLIENT_A)

    first = api_client.post("/v1/me/unsubscribe", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["unsubscribed_at"] is not None

    second = api_client.post("/v1/me/unsubscribe", headers=headers)
    assert second.status_code == 200, second.text
    # Idempotent: the original timestamp is preserved, not overwritten.
    assert second.json()["unsubscribed_at"] == first.json()["unsubscribed_at"]


# ---------------------------------------------------------------------------
# Data export (two-user isolation)
# ---------------------------------------------------------------------------


def test_export_returns_only_own_data(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    ids = setup_two_client_tree(db)

    resp = api_client.get("/v1/me/export", headers=auth_headers(CLIENT_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["profile"]["id"] == CLIENT_A
    assert body["profile"]["email"] == USERS[CLIENT_A]

    household_ids = {h["id"] for h in body["households"]}
    assert household_ids == {ids["household_a"]}
    # Other users' contact details are never exported on members.
    for household in body["households"]:
        for member in household["members"]:
            assert "email" not in member
            assert set(member) <= {
                "id",
                "user_id",
                "relationship_role",
                "access_status",
                "created_at",
            }

    assert {m["id"] for m in body["matters"]} == {ids["matter_a"]}
    assert {p["id"] for p in body["persons"]} == {ids["person_a"]}
    assert {d["id"] for d in body["decisions"]} == {ids["decision_a"]}
    assert {a["id"] for a in body["assets"]} == {ids["asset_a"]}
    assert body["truncated"] is False

    # Client B's export contains only client B's data.
    resp_b = api_client.get("/v1/me/export", headers=auth_headers(CLIENT_B))
    assert resp_b.status_code == 200, resp_b.text
    body_b = resp_b.json()
    assert {h["id"] for h in body_b["households"]} == {ids["household_b"]}
    assert {m["id"] for m in body_b["matters"]} == {ids["matter_b"]}
    assert {p["id"] for p in body_b["persons"]} == {ids["person_b"]}
    assert body_b["decisions"] == []
    assert {a["id"] for a in body_b["assets"]} == {ids["asset_b"]}


# ---------------------------------------------------------------------------
# Deletion requests
# ---------------------------------------------------------------------------


def test_deletion_request_create_and_list(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    tree = setup_two_client_tree(db)
    headers = auth_headers(CLIENT_A)

    resp = api_client.post(
        "/v1/me/deletion-requests",
        json={"notes": "Please delete my intake answers."},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user_id"] == CLIENT_A
    assert body["status"] == "open"
    assert body["notes"] == "Please delete my intake answers."
    assert body["requested_at"] is not None
    assert body["processed_at"] is None
    # CLIENT_A is a household-only client (no firm membership): the request
    # is still attributed to the household's firm for the triage queue.
    assert body["firm_id"] == tree["firm_a"]

    listed = api_client.get("/v1/me/deletion-requests", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [r["id"] for r in listed.json()] == [body["id"]]

    # Another user sees none of client A's requests.
    other = api_client.get("/v1/me/deletion-requests", headers=auth_headers(CLIENT_B))
    assert other.json() == []

    # An audit event was recorded for the request.
    with db.cursor() as cur:
        cur.execute(
            "SELECT action, actor_user_id, resource_type FROM audit_events"
            " WHERE action = 'privacy.deletion_requested'"
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert str(rows[0]["actor_user_id"]) == CLIENT_A
    assert rows[0]["resource_type"] == "data_deletion_requests"


# ---------------------------------------------------------------------------
# Admin reminder dispatch
# ---------------------------------------------------------------------------


def test_dispatch_requires_firm_admin(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    for sub in (STAFF_A, ATTORNEY_A, CLIENT_A):
        resp = api_client.post("/v1/admin/reminders/dispatch", headers=auth_headers(sub))
        assert resp.status_code == 403, (sub, resp.text)


def test_dispatch_without_broker_is_501(db, api_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    resp = api_client.post("/v1/admin/reminders/dispatch", headers=auth_headers(ADMIN_A))
    assert resp.status_code == 501, resp.text
    assert resp.json()["code"] == "worker_broker_unavailable"


def test_dispatch_enqueues_worker_actor(
    db, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    setup_two_client_tree(db)
    calls: list[str] = []

    def fake_enqueue(redis_url: str) -> None:
        calls.append(redis_url)

    monkeypatch.setenv("REDIS_URL", "redis://synthetic:6379/0")
    monkeypatch.setattr("app.domains.privacy.service.enqueue_reminder_dispatch", fake_enqueue)

    resp = api_client.post("/v1/admin/reminders/dispatch", headers=auth_headers(ADMIN_A))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["dispatched"] is True
    assert body["actor"] == "send_due_reminders"
    assert body["schedule"] == "every 15 minutes"
    assert calls == ["redis://synthetic:6379/0"]

    # The dispatch itself is audited.
    with db.cursor() as cur:
        cur.execute("SELECT action FROM audit_events WHERE action = 'reminders.dispatch_requested'")
        assert cur.fetchone() is not None
