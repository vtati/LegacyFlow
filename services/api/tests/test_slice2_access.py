"""Slice 2 API access tests: invitations, matters, household, nominations.

Runs against a local pgserver instance with the Slice 1 + Slice 2
migrations and jurisdiction seeds applied — NEVER against DATABASE_URL or
live Supabase. All fixture people, firms, and households are synthetic.

The four Slice 2 routers are mounted at ``/v1`` exactly as the coordinator
will mount them (see each domain's ``router.py`` for the import path).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    TEST_SECRET,
    mint_token,
)

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------
ADMIN_SUB = "aaaaaaaa-1111-1111-1111-111111111111"
ATTORNEY_SUB = "bbbbbbbb-2222-2222-2222-222222222222"
STAFF_SUB = "cccccccc-3333-3333-3333-333333333333"
CLIENT_SUB = "dddddddd-4444-4444-4444-444444444444"
COLLAB_SUB = "eeeeeeee-5555-5555-5555-555555555555"
DEPENDENT_SUB = "ffffffff-6666-6666-6666-666666666666"
FIRMB_ADMIN_SUB = "11111111-aaaa-bbbb-cccc-000000000001"

USERS = {
    ADMIN_SUB: "admin@example.test",
    ATTORNEY_SUB: "attorney@example.test",
    STAFF_SUB: "staff@example.test",
    CLIENT_SUB: "client@example.test",
    COLLAB_SUB: "collab@example.test",
    DEPENDENT_SUB: "dependent@example.test",
    FIRMB_ADMIN_SUB: "firmb.admin@example.test",
}

# Seed UUIDs from supabase/seeds/01_jurisdictions.sql.
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

TRUNCATE_TABLES = (
    "audit_events, escalations, intake_answers, matter_status_history, matters,"
    " invitations, nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start pgserver once; apply auth stub + Slice 1/2 migrations + seeds."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-slice2")))
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
    """Open connection; truncate all non-reference tables for isolation.

    jurisdiction_packs.approved_by_user_id references profiles(id), so the
    CASCADE truncate wipes the seeded packs; the seed is idempotent, re-apply it.
    """
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def api_client(pg_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with pgserver as DATABASE_URL and test JWT override."""
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Rate limiting is covered by its own dedicated tests; leave it off here
    # so invitation-flow tests are not order-dependent (429s).
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.domains.household.router import router as household_router
    from app.domains.invitations.router import router as invitations_router
    from app.domains.matters.router import router as matters_router
    from app.domains.nominations.router import router as nominations_router
    from app.main import create_app

    app = create_app()
    app.include_router(invitations_router, prefix="/v1")
    app.include_router(matters_router, prefix="/v1")
    app.include_router(household_router, prefix="/v1")
    app.include_router(nominations_router, prefix="/v1")

    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# DB setup helpers (synthetic data only)
# ---------------------------------------------------------------------------
def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def create_user(db, sub: str, email: str | None = None) -> None:
    email = email or USERS[sub]
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)",
        (sub, email, f"Synthetic {email}"),
    )


def create_firm(db, name: str = "Synthetic Firm") -> str:
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, name, name),
    )
    return firm_id


def add_firm_member(db, firm_id: str, sub: str, role: str) -> None:
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, %s, 'active')",
        (firm_id, sub, role),
    )


def create_household(db, firm_id: str, created_by: str, name: str = "Synthetic Household") -> str:
    household_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, firm_id, name, created_by),
    )
    return household_id


def add_household_member(
    db, household_id: str, sub: str, relationship_role: str, access_status: str = "active"
) -> None:
    _exec(
        db,
        "INSERT INTO household_members (household_id, user_id, relationship_role, access_status)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, sub, relationship_role, access_status),
    )


def create_person(db, household_id: str, full_name: str = "Synthetic Person") -> str:
    person_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO persons (id, household_id, full_name, kind) VALUES (%s, %s, %s, 'adult')",
        (person_id, household_id, full_name),
    )
    return person_id


def token(sub: str, email: str | None = None) -> str:
    return mint_token(sub=sub, email=email or USERS[sub])


def auth(sub: str, email: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub, email)}"}


def setup_firm_a(db) -> tuple[str, str]:
    """Firm A with admin/attorney/staff users; returns (firm_id, household_id)."""
    for sub in (ADMIN_SUB, ATTORNEY_SUB, STAFF_SUB, CLIENT_SUB, COLLAB_SUB, DEPENDENT_SUB):
        create_user(db, sub)
    firm_id = create_firm(db, "Firm A")
    add_firm_member(db, firm_id, ADMIN_SUB, "firm_admin")
    add_firm_member(db, firm_id, ATTORNEY_SUB, "attorney")
    add_firm_member(db, firm_id, STAFF_SUB, "staff")
    household_id = create_household(db, firm_id, ADMIN_SUB)
    add_household_member(db, household_id, CLIENT_SUB, "primary_client")
    add_household_member(db, household_id, COLLAB_SUB, "collaborator")
    add_household_member(db, household_id, DEPENDENT_SUB, "dependent")
    return firm_id, household_id


def create_matter_via_api(api_client: TestClient, household_id: str, sub: str = ADMIN_SUB) -> dict:
    resp = api_client.post(
        "/v1/matters",
        json={
            "household_id": household_id,
            "jurisdiction_code": "US-VA",
            "title": "Synthetic will",
        },
        headers=auth(sub),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def insert_matter_sql(
    db, firm_id: str, household_id: str, status: str = "DRAFT", title: str = "Synthetic"
) -> str:
    matter_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title)"
        " VALUES (%s, %s, %s, %s, %s, 'will', %s, %s)",
        (matter_id, firm_id, household_id, US_VA_JURISDICTION_ID, US_VA_PACK_ID, status, title),
    )
    return matter_id


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
def test_invitation_create_returns_token_in_dev(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    resp = api_client.post(
        "/v1/firms/invitations",
        json={
            "email": "invitee@example.test",
            "role": "client_primary",
            "household_id": household_id,
        },
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invitation"]["status"] == "pending"
    assert body["invitation"]["role"] == "client_primary"
    assert body["accept_token"], "dev must return the raw accept token"
    assert "token_hash" not in body["invitation"]


def test_invitation_create_hidden_in_production(api_client: TestClient, db, monkeypatch) -> None:
    _, household_id = setup_firm_a(db)
    from app.core.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    resp = api_client.post(
        "/v1/firms/invitations",
        json={"email": "prod@example.test", "role": "firm_staff", "household_id": None},
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["accept_token"] is None


def test_invitation_create_role_validation(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    resp = api_client.post(
        "/v1/firms/invitations",
        json={"email": "x@example.test", "role": "superadmin"},
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 422, resp.text


def test_invitation_create_requires_admin_or_attorney(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    # staff (firm role) cannot create invitations
    resp = api_client.post(
        "/v1/firms/invitations",
        json={"email": "x@example.test", "role": "firm_staff"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 403, resp.text
    # household client cannot create invitations
    resp = api_client.post(
        "/v1/firms/invitations",
        json={"email": "x@example.test", "role": "firm_staff"},
        headers=auth(CLIENT_SUB),
    )
    assert resp.status_code == 403, resp.text
    # unauthenticated
    resp = api_client.post(
        "/v1/firms/invitations", json={"email": "x@example.test", "role": "firm_staff"}
    )
    assert resp.status_code == 401, resp.text


def test_invitation_list_and_status_filter(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    for email, role in (("a@example.test", "firm_staff"), ("b@example.test", "client_primary")):
        resp = api_client.post(
            "/v1/firms/invitations",
            json={"email": email, "role": role},
            headers=auth(ATTORNEY_SUB),
        )
        assert resp.status_code == 201, resp.text
    resp = api_client.get("/v1/firms/invitations", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2
    resp = api_client.get("/v1/firms/invitations?status=pending", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200 and len(resp.json()) == 2
    resp = api_client.get("/v1/firms/invitations?status=accepted", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200 and resp.json() == []


def test_invitation_resend_rotates_token(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "r@example.test", "role": "firm_staff"},
        headers=auth(ADMIN_SUB),
    ).json()
    invitation_id = created["invitation"]["id"]
    old_token = created["accept_token"]
    resp = api_client.post(f"/v1/firms/invitations/{invitation_id}/resend", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["invitation"]["status"] == "pending"
    assert body["accept_token"] and body["accept_token"] != old_token
    # the old token no longer resolves
    resp = api_client.post(
        "/v1/invitations/accept", json={"token": old_token}, headers=auth(CLIENT_SUB)
    )
    assert resp.status_code == 404, resp.text


def test_invitation_revoke(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "v@example.test", "role": "firm_staff"},
        headers=auth(ADMIN_SUB),
    ).json()
    invitation_id = created["invitation"]["id"]
    token_value = created["accept_token"]
    resp = api_client.post(f"/v1/firms/invitations/{invitation_id}/revoke", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"
    # revoke is idempotent
    resp = api_client.post(f"/v1/firms/invitations/{invitation_id}/revoke", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200
    # accepting a revoked invitation is gone
    resp = api_client.post(
        "/v1/invitations/accept", json={"token": token_value}, headers=auth(CLIENT_SUB)
    )
    assert resp.status_code == 410, resp.text


def test_invitation_accept_client_joins_household(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={
            "email": "newclient@example.test",
            "role": "client_collaborator",
            "household_id": household_id,
        },
        headers=auth(ADMIN_SUB),
    ).json()
    new_sub = "22222222-2222-2222-2222-222222222222"
    create_user(db, new_sub, "newclient@example.test")
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": created["accept_token"]},
        headers=auth(new_sub, "newclient@example.test"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["invitation"]["status"] == "accepted"
    assert body["invitation"]["accepted_user_id"] == new_sub
    assert body["membership_type"] == "household"
    with db.cursor() as cur:
        cur.execute(
            "SELECT relationship_role, access_status, user_id FROM household_members WHERE id = %s",
            (body["membership_id"],),
        )
        row = cur.fetchone()
    assert row["relationship_role"] == "collaborator"
    assert row["access_status"] == "active"
    assert str(row["user_id"]) == new_sub


def test_invitation_accept_firm_role_creates_membership(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "counsel@example.test", "role": "firm_attorney"},
        headers=auth(ADMIN_SUB),
    ).json()
    new_sub = "33333333-3333-3333-3333-333333333333"
    create_user(db, new_sub, "counsel@example.test")
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": created["accept_token"]},
        headers=auth(new_sub, "counsel@example.test"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["membership_type"] == "firm"
    with db.cursor() as cur:
        cur.execute(
            "SELECT role, status FROM firm_memberships WHERE id = %s",
            (body["membership_id"],),
        )
        row = cur.fetchone()
    assert row["role"] == "attorney"  # firm_attorney maps to attorney
    assert row["status"] == "active"


def test_invitation_accept_expired_token(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "late@example.test", "role": "firm_staff"},
        headers=auth(ADMIN_SUB),
    ).json()
    invitation_id = created["invitation"]["id"]
    _exec(
        db,
        "UPDATE invitations SET expires_at = now() - interval '1 hour' WHERE id = %s",
        (invitation_id,),
    )
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": created["accept_token"]},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 410, resp.text
    with db.cursor() as cur:
        cur.execute("SELECT status FROM invitations WHERE id = %s", (invitation_id,))
        assert cur.fetchone()["status"] == "expired"


def test_invitation_accept_wrong_token(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": "wrong-token-that-matches-nothing"},
        headers=auth(CLIENT_SUB),
    )
    assert resp.status_code == 404, resp.text


def test_invitation_double_accept_idempotent(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "twice@example.test", "role": "firm_staff"},
        headers=auth(ADMIN_SUB),
    ).json()
    first = api_client.post(
        "/v1/invitations/accept",
        json={"token": created["accept_token"]},
        headers=auth(STAFF_SUB),
    )
    assert first.status_code == 200, first.text
    second = api_client.post(
        "/v1/invitations/accept",
        json={"token": created["accept_token"]},
        headers=auth(STAFF_SUB),
    )
    assert second.status_code == 200, second.text
    assert second.json()["membership_id"] == first.json()["membership_id"]


def test_invitation_accept_client_primary_creates_household(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "fresh@example.test", "role": "client_primary"},
        headers=auth(ADMIN_SUB),
    ).json()
    new_sub = "44444444-4444-4444-4444-444444444444"
    create_user(db, new_sub, "fresh@example.test")
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": created["accept_token"]},
        headers=auth(new_sub, "fresh@example.test"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["membership_type"] == "household"
    with db.cursor() as cur:
        cur.execute(
            "SELECT household_id, relationship_role FROM household_members WHERE id = %s",
            (body["membership_id"],),
        )
        row = cur.fetchone()
    assert row["relationship_role"] == "primary_client"


# ---------------------------------------------------------------------------
# Matters
# ---------------------------------------------------------------------------
def test_matter_create_with_va_pack_snapshot(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    assert matter["status"] == "INTAKE_IN_PROGRESS"
    assert matter["plan_type"] == "will"
    assert matter["title"] == "Synthetic will"
    snapshot = matter["jurisdiction_snapshot"]
    assert snapshot["pack_id"] == "us-va"
    assert snapshot["jurisdiction_code"] == "US-VA"
    assert snapshot["semantic_version"] == "0.1.0"
    assert isinstance(snapshot["rule_ids"], list) and snapshot["rule_ids"]
    assert snapshot["question_set_versions"]["household"] == "1.0.0"
    assert matter["completeness_pct"] == 9  # lifecycle position of INTAKE_IN_PROGRESS
    with db.cursor() as cur:
        cur.execute(
            "SELECT to_status, from_status FROM matter_status_history WHERE matter_id = %s",
            (matter["id"],),
        )
        history = cur.fetchone()
    assert history["from_status"] is None
    assert history["to_status"] == "INTAKE_IN_PROGRESS"


def test_matter_create_unknown_jurisdiction(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    resp = api_client.post(
        "/v1/matters",
        json={"household_id": household_id, "jurisdiction_code": "US-ZZ"},
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "urn:legacyflow:error:unknown_jurisdiction"


def test_matter_create_cross_firm_household_404(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    create_user(db, FIRMB_ADMIN_SUB)
    firm_b = create_firm(db, "Firm B")
    add_firm_member(db, firm_b, FIRMB_ADMIN_SUB, "firm_admin")
    household_b = create_household(db, firm_b, FIRMB_ADMIN_SUB)
    # Firm A admin tries to create a matter for Firm B's household
    resp = api_client.post(
        "/v1/matters",
        json={"household_id": household_b, "jurisdiction_code": "US-VA"},
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 404, resp.text


def test_matter_invalid_transition_422(api_client: TestClient, db) -> None:
    firm_id, household_id = setup_firm_a(db)
    matter_id = insert_matter_sql(db, firm_id, household_id, status="DRAFT")
    resp = api_client.post(
        f"/v1/matters/{matter_id}/transition",
        json={"to_status": "ATTORNEY_REVIEW"},
        headers=auth(ATTORNEY_SUB),
    )
    assert resp.status_code == 422, resp.text


def test_matter_gate_transition_409(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    matter_id = matter["id"]
    for to_status in ("INTAKE_SUBMITTED", "ATTORNEY_REVIEW"):
        resp = api_client.post(
            f"/v1/matters/{matter_id}/transition",
            json={"to_status": to_status},
            headers=auth(ATTORNEY_SUB),
        )
        assert resp.status_code == 200, resp.text
    # ATTORNEY_REVIEW -> APPROVED_FOR_ASSEMBLY needs the intake_approval gate (Slice 3)
    resp = api_client.post(
        f"/v1/matters/{matter_id}/transition",
        json={"to_status": "APPROVED_FOR_ASSEMBLY"},
        headers=auth(ATTORNEY_SUB),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "urn:legacyflow:error:approval_gate_required"


def test_matter_cross_firm_access_404(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    create_user(db, FIRMB_ADMIN_SUB)
    firm_b = create_firm(db, "Firm B")
    add_firm_member(db, firm_b, FIRMB_ADMIN_SUB, "firm_admin")
    resp = api_client.get(f"/v1/matters/{matter['id']}", headers=auth(FIRMB_ADMIN_SUB))
    assert resp.status_code == 404, resp.text  # existence oracle, not 403


def test_household_client_can_create_matter(api_client: TestClient, db) -> None:
    # Phase 2 (D4): the household_id path resolves the firm from the
    # household, so a household-only primary_client CAN create a matter
    # in their own household (previously 403 — the firmless dead end).
    _, household_id = setup_firm_a(db)
    resp = api_client.post(
        "/v1/matters",
        json={"household_id": household_id, "jurisdiction_code": "US-VA"},
        headers=auth(CLIENT_SUB),
    )
    assert resp.status_code == 201, resp.text


def test_household_client_can_read_own_matter(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    resp = api_client.get(f"/v1/matters/{matter['id']}", headers=auth(CLIENT_SUB))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matter"]["id"] == matter["id"]
    assert body["household"]["id"] == household_id
    assert body["household"]["name"] == body["household"]["display_name"]
    assert body["progress"]["status"] == "INTAKE_IN_PROGRESS"


def test_matter_list_pagination_and_scoping(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    for i in range(3):
        resp = api_client.post(
            "/v1/matters",
            json={
                "household_id": household_id,
                "jurisdiction_code": "US-VA",
                "title": f"Synthetic matter {i}",
            },
            headers=auth(ADMIN_SUB),
        )
        assert resp.status_code == 201, resp.text
    resp = api_client.get("/v1/matters?page=1&page_size=2", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert body["page"] == 1 and body["page_size"] == 2
    resp = api_client.get("/v1/matters?page=2&page_size=2", headers=auth(ADMIN_SUB))
    assert len(resp.json()["items"]) == 1
    # page_size over the cap is rejected
    resp = api_client.get("/v1/matters?page_size=101", headers=auth(ADMIN_SUB))
    assert resp.status_code == 422, resp.text
    # household client sees only their household's matters
    resp = api_client.get("/v1/matters", headers=auth(CLIENT_SUB))
    assert resp.status_code == 200 and resp.json()["total"] == 3, resp.text


def test_matter_transition_requires_staff(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    resp = api_client.post(
        f"/v1/matters/{matter['id']}/transition",
        json={"to_status": "INTAKE_SUBMITTED"},
        headers=auth(CLIENT_SUB),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Household / persons / relationships
# ---------------------------------------------------------------------------
def test_household_create_and_list(api_client: TestClient, db) -> None:
    setup_firm_a(db)
    resp = api_client.post(
        "/v1/households", json={"name": "Synthetic New Household"}, headers=auth(STAFF_SUB)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Synthetic New Household"
    assert body["display_name"] == "Synthetic New Household"
    resp = api_client.get("/v1/households", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200 and len(resp.json()) == 2, resp.text
    # household clients don't get the firm household list
    resp = api_client.get("/v1/households", headers=auth(CLIENT_SUB))
    assert resp.status_code == 403, resp.text


def test_matter_household_detail(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    person_id = create_person(db, household_id)
    resp = api_client.get(f"/v1/matters/{matter['id']}/household", headers=auth(ADMIN_SUB))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["household"]["id"] == household_id
    assert len(body["members"]) == 3  # primary_client, collaborator, dependent
    assert any(p["id"] == person_id for p in body["persons"])
    assert body["relationships"] == []


def test_person_crud_access(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    url = f"/v1/matters/{matter['id']}/persons"
    # staff creates
    resp = api_client.post(
        url,
        json={"full_name": "Synthetic Spouse", "kind": "adult"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 201, resp.text
    person_id = resp.json()["id"]
    # primary_client creates too
    resp = api_client.post(
        url, json={"full_name": "Synthetic Kid", "kind": "minor"}, headers=auth(CLIENT_SUB)
    )
    assert resp.status_code == 201, resp.text
    # dependent cannot
    resp = api_client.post(
        url, json={"full_name": "Synthetic X", "kind": "adult"}, headers=auth(DEPENDENT_SUB)
    )
    assert resp.status_code == 403, resp.text
    # patch by collaborator
    resp = api_client.patch(
        f"{url}/{person_id}", json={"notes": "Synthetic note"}, headers=auth(COLLAB_SUB)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == "Synthetic note"
    # patch a person from another household -> 404
    other_household = create_household(db, matter["firm_id"], ADMIN_SUB, "Other")
    other_person = create_person(db, other_household)
    resp = api_client.patch(f"{url}/{other_person}", json={"notes": "x"}, headers=auth(ADMIN_SUB))
    assert resp.status_code == 404, resp.text


def test_relationship_crud(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    p1 = create_person(db, household_id, "Synthetic A")
    p2 = create_person(db, household_id, "Synthetic B")
    url = f"/v1/matters/{matter['id']}/relationships"
    resp = api_client.post(
        url,
        json={"from_person_id": p1, "to_person_id": p2, "relationship_type": "spouse"},
        headers=auth(CLIENT_SUB),
    )
    assert resp.status_code == 201, resp.text
    # self-relationship is rejected
    resp = api_client.post(
        url,
        json={"from_person_id": p1, "to_person_id": p1, "relationship_type": "self"},
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 422, resp.text
    # person from another household is not found
    other_household = create_household(db, matter["firm_id"], ADMIN_SUB, "Other")
    outsider = create_person(db, other_household)
    resp = api_client.post(
        url,
        json={"from_person_id": p1, "to_person_id": outsider, "relationship_type": "friend"},
        headers=auth(ADMIN_SUB),
    )
    assert resp.status_code == 404, resp.text


def test_add_household_member_staff_only(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    url = f"/v1/matters/{matter['id']}/household/members"
    # staff adds an unclaimed member row
    resp = api_client.post(
        url, json={"relationship_role": "trusted_contact"}, headers=auth(STAFF_SUB)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_status"] == "invited"
    assert body["user_id"] is None
    # staff links an existing user by email -> active immediately
    new_sub = "55555555-5555-5555-5555-555555555555"
    create_user(db, new_sub, "linked@example.test")
    resp = api_client.post(
        url,
        json={"email": "linked@example.test", "relationship_role": "collaborator"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["access_status"] == "active"
    assert resp.json()["user_id"] == new_sub
    # household client cannot add members
    resp = api_client.post(url, json={"relationship_role": "dependent"}, headers=auth(CLIENT_SUB))
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Nominations
# ---------------------------------------------------------------------------
def _nomination_url(matter_id: str) -> str:
    return f"/v1/matters/{matter_id}/nominations"


def test_nominations_crud_access(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    person_id = create_person(db, household_id)
    url = _nomination_url(matter["id"])

    # staff creates
    resp = api_client.post(
        url,
        json={"nomination_type": "guardian", "person_id": person_id, "priority": 2},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 201, resp.text
    # collaborator creates
    resp = api_client.post(
        url,
        json={"nomination_type": "trustee", "person_id": person_id, "priority": 1},
        headers=auth(COLLAB_SUB),
    )
    assert resp.status_code == 201, resp.text
    trustee_id = resp.json()["id"]
    # dependent cannot create
    resp = api_client.post(
        url,
        json={"nomination_type": "executor", "person_id": person_id},
        headers=auth(DEPENDENT_SUB),
    )
    assert resp.status_code == 403, resp.text
    # person outside the household is not found
    other_household = create_household(db, matter["firm_id"], ADMIN_SUB, "Other")
    outsider = create_person(db, other_household)
    resp = api_client.post(
        url,
        json={"nomination_type": "executor", "person_id": outsider},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 404, resp.text

    # list ordered by type, then priority; type filter works
    resp = api_client.get(url, headers=auth(CLIENT_SUB))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert [i["nomination_type"] for i in items] == ["guardian", "trustee"]
    resp = api_client.get(url + "?type=trustee", headers=auth(CLIENT_SUB))
    assert [i["id"] for i in resp.json()] == [trustee_id]

    # collaborator cannot delete; primary_client can; staff can
    resp = api_client.delete(f"{url}/{trustee_id}", headers=auth(COLLAB_SUB))
    assert resp.status_code == 403, resp.text
    resp = api_client.delete(f"{url}/{trustee_id}", headers=auth(CLIENT_SUB))
    assert resp.status_code == 204, resp.text
    remaining = api_client.get(url, headers=auth(ADMIN_SUB)).json()
    assert len(remaining) == 1
    resp = api_client.delete(f"{url}/{remaining[0]['id']}", headers=auth(STAFF_SUB))
    assert resp.status_code == 204, resp.text
    # deleting a nomination that belongs to another matter -> 404
    other_matter = create_matter_via_api(api_client, other_household)
    other_nom = api_client.post(
        _nomination_url(other_matter["id"]),
        json={"nomination_type": "executor", "person_id": outsider},
        headers=auth(STAFF_SUB),
    )
    assert other_nom.status_code == 201, other_nom.text
    resp = api_client.delete(f"{url}/{other_nom.json()['id']}", headers=auth(ADMIN_SUB))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Regression tests for review fixes
# ---------------------------------------------------------------------------


def test_invitation_accept_by_different_user_404(api_client: TestClient, db) -> None:
    """An accepted invitation cannot be claimed by another user."""
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "solo@example.test", "role": "firm_staff"},
        headers=auth(ADMIN_SUB),
    ).json()
    token = created["accept_token"]
    new_sub = "66666666-6666-6666-6666-666666666666"
    create_user(db, new_sub, "solo@example.test")
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": token},
        headers=auth(new_sub, "solo@example.test"),
    )
    assert resp.status_code == 200, resp.text
    other_sub = "77777777-7777-7777-7777-777777777777"
    create_user(db, other_sub, "intruder@example.test")
    resp = api_client.post(
        "/v1/invitations/accept",
        json={"token": token},
        headers=auth(other_sub, "intruder@example.test"),
    )
    assert resp.status_code == 404, resp.text


def test_invitation_accept_same_user_twice_no_duplicate_household(
    api_client: TestClient, db
) -> None:
    """Primary-client accept without a household creates exactly one household."""
    setup_firm_a(db)
    created = api_client.post(
        "/v1/firms/invitations",
        json={"email": "primary2@example.test", "role": "client_primary"},
        headers=auth(ADMIN_SUB),
    ).json()
    token = created["accept_token"]
    new_sub = "88888888-8888-8888-8888-888888888888"
    create_user(db, new_sub, "primary2@example.test")
    headers = auth(new_sub, "primary2@example.test")
    first = api_client.post("/v1/invitations/accept", json={"token": token}, headers=headers)
    assert first.status_code == 200, first.text
    second = api_client.post("/v1/invitations/accept", json={"token": token}, headers=headers)
    assert second.status_code == 200, second.text
    assert first.json()["membership_id"] == second.json()["membership_id"]
    with db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM households WHERE created_by_user_id = %s",
            (new_sub,),
        )
        assert cur.fetchone()["c"] == 1
        cur.execute(
            "SELECT household_id FROM invitations WHERE id = %s",
            (created["invitation"]["id"],),
        )
        assert cur.fetchone()["household_id"] is not None


def test_person_kind_validation_422(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    resp = api_client.post(
        f"/v1/matters/{matter['id']}/persons",
        json={"full_name": "Synthetic Odd", "kind": "corporation"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 422, resp.text


def test_household_member_role_validation_422(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    resp = api_client.post(
        f"/v1/matters/{matter['id']}/household/members",
        json={"relationship_role": "owner"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 422, resp.text


def test_matter_unknown_transition_target_422(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    resp = api_client.post(
        f"/v1/matters/{matter['id']}/transition",
        json={"to_status": "NOT_A_STATUS"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 422, resp.text


def test_matter_gate_error_carries_code(api_client: TestClient, db) -> None:
    _, household_id = setup_firm_a(db)
    matter = create_matter_via_api(api_client, household_id)
    for to_status in ("INTAKE_SUBMITTED", "ATTORNEY_REVIEW"):
        resp = api_client.post(
            f"/v1/matters/{matter['id']}/transition",
            json={"to_status": to_status},
            headers=auth(STAFF_SUB),
        )
        assert resp.status_code == 200, resp.text
    resp = api_client.post(
        f"/v1/matters/{matter['id']}/transition",
        json={"to_status": "APPROVED_FOR_ASSEMBLY"},
        headers=auth(STAFF_SUB),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "approval_gate_required"


def test_audit_events_carry_correlation_id(api_client: TestClient, db) -> None:
    """The request id middleware value lands on audit_events.correlation_id."""
    _, household_id = setup_firm_a(db)
    request_id = "12345678-1234-1234-1234-123456789abc"
    resp = api_client.post(
        "/v1/households",
        json={"name": "Correlated household"},
        headers={**auth(STAFF_SUB), "X-Request-ID": request_id},
    )
    assert resp.status_code == 201, resp.text
    household_id_new = resp.json()["id"]
    with db.cursor() as cur:
        cur.execute(
            "SELECT correlation_id FROM audit_events WHERE action = 'households.created' "
            "AND resource_id = %s",
            (household_id_new,),
        )
        row = cur.fetchone()
    assert row is not None
    assert str(row["correlation_id"]) == request_id
