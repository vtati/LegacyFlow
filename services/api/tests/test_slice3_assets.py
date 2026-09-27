"""Slice 3 API tests: assets, liabilities, and the beneficiary-alignment engine.

Runs against a local pgserver instance with the Slice 1 + Slice 2 + Slice 3
(migrations through 20260927020000_assets_review_slice3) migrations and
jurisdiction seeds applied — NEVER against DATABASE_URL or live Supabase.
All fixture people, firms, and households are synthetic.

The assets router is mounted at ``/v1`` exactly as the coordinator will
mount it (see ``app/domains/assets/router.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.domains.assets import service as asset_service
from tests.conftest import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    TEST_SECRET,
    mint_token,
)

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------
ATTORNEY_A = "a1111111-1111-1111-1111-111111111111"
STAFF_A = "a2222222-2222-2222-2222-222222222222"
CLIENT_A = "a3333333-3333-3333-3333-333333333333"
COLLAB_A = "a4444444-4444-4444-4444-444444444444"
DEPENDENT_A = "a5555555-5555-5555-5555-555555555555"
AUDITOR_A = "a6666666-6666-6666-6666-666666666666"
ADMIN_B = "b1111111-1111-1111-1111-111111111111"
CLIENT_B = "b2222222-2222-2222-2222-222222222222"

USERS = {
    ATTORNEY_A: "attorney.a@example.test",
    STAFF_A: "staff.a@example.test",
    CLIENT_A: "client.a@example.test",
    COLLAB_A: "collab.a@example.test",
    DEPENDENT_A: "dependent.a@example.test",
    AUDITOR_A: "auditor.a@example.test",
    ADMIN_B: "admin.b@example.test",
    CLIENT_B: "client.b@example.test",
}

# Seed UUIDs from supabase/seeds/01_jurisdictions.sql.
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

TRUNCATE_TABLES = (
    "audit_events, escalations, intake_answers, matter_status_history, matters,"
    " invitations, nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " asset_flags, assets, liabilities, auth.users"
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
    """Start pgserver once; apply auth stub + migrations + seeds + assets DDL."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-slice3-assets")))
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
    """TestClient with pgserver as DATABASE_URL and test JWT override."""
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.domains.assets.router import router as assets_router
    from app.main import create_app

    app = create_app()
    app.include_router(assets_router, prefix="/v1")

    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# DB setup helpers (synthetic data only)
# ---------------------------------------------------------------------------
def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def create_user(db, sub: str) -> None:
    email = USERS[sub]
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


def create_person(db, household_id: str, full_name: str) -> str:
    person_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO persons (id, household_id, full_name, kind) VALUES (%s, %s, %s, 'adult')",
        (person_id, household_id, full_name),
    )
    return person_id


def insert_matter(db, firm_id: str, household_id: str) -> str:
    matter_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s)",
        (matter_id, firm_id, household_id, US_VA_JURISDICTION_ID, US_VA_PACK_ID, "Synthetic"),
    )
    return matter_id


def token(sub: str) -> str:
    return mint_token(sub=sub, email=USERS[sub])


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub)}"}


def setup_two_firms(db) -> dict[str, str]:
    """Firm A (attorney/staff/client/collab/dependent/auditor + household) and
    Firm B (admin + household). Returns ids keyed by name."""
    for sub in USERS:
        create_user(db, sub)
    firm_a = create_firm(db, "Firm A")
    add_firm_member(db, firm_a, ATTORNEY_A, "attorney")
    add_firm_member(db, firm_a, STAFF_A, "staff")
    add_firm_member(db, firm_a, AUDITOR_A, "auditor")
    household_a = create_household(db, firm_a, ATTORNEY_A, "Household A")
    add_household_member(db, household_a, CLIENT_A, "primary_client")
    add_household_member(db, household_a, COLLAB_A, "collaborator")
    add_household_member(db, household_a, DEPENDENT_A, "dependent")

    firm_b = create_firm(db, "Firm B")
    add_firm_member(db, firm_b, ADMIN_B, "firm_admin")
    household_b = create_household(db, firm_b, ADMIN_B, "Household B")
    add_household_member(db, household_b, CLIENT_B, "primary_client")
    return {
        "firm_a": firm_a,
        "household_a": household_a,
        "firm_b": firm_b,
        "household_b": household_b,
    }


def asset_payload(**overrides) -> dict:
    payload = {
        "asset_type": "bank_account",
        "label": "Synthetic checking",
        "estimated_value_cents": 250000,
        "ownership": "joint",
    }
    payload.update(overrides)
    return payload


def liability_payload(**overrides) -> dict:
    payload = {
        "liability_type": "mortgage",
        "label": "Synthetic mortgage",
        "balance_cents": 18000000,
        "monthly_payment_cents": 120000,
    }
    payload.update(overrides)
    return payload


def create_asset_api(api_client: TestClient, household_id: str, sub: str, **overrides) -> dict:
    resp = api_client.post(
        f"/v1/households/{household_id}/assets", json=asset_payload(**overrides), headers=auth(sub)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_liability_api(api_client: TestClient, household_id: str, sub: str, **overrides) -> dict:
    resp = api_client.post(
        f"/v1/households/{household_id}/liabilities",
        json=liability_payload(**overrides),
        headers=auth(sub),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def audit_events_for(db, resource_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT action, resource_type, metadata FROM audit_events"
            " WHERE resource_id = %s ORDER BY created_at",
            (resource_id,),
        )
        rows = cur.fetchall()
    db.commit()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Alignment engine unit tests (pure function: no DB)
# ---------------------------------------------------------------------------
def _engine_asset(asset_type: str = "bank_account", designation: dict | None = None) -> dict:
    return {"asset_type": asset_type, "beneficiary_designation": designation}


def test_engine_no_designation_flags_unknown_beneficiary() -> None:
    flags = asset_service.evaluate_alignment_flags(_engine_asset(), [])
    assert flags == [
        {
            "flag_type": "unknown_beneficiary",
            "severity": "review",
            "message": "No beneficiary designation is recorded for this asset.",
        }
    ]


def test_engine_empty_primary_flags_unknown_beneficiary() -> None:
    asset = _engine_asset(
        "retirement",
        {"primary": [], "contingent": [{"name": "Synthetic Spouse", "relationship": "spouse"}]},
    )
    flags = asset_service.evaluate_alignment_flags(asset, [{"full_name": "Synthetic Spouse"}])
    assert [f["flag_type"] for f in flags] == ["unknown_beneficiary"]


def test_engine_non_household_beneficiary() -> None:
    asset = _engine_asset(
        "insurance",
        {
            "primary": [{"name": "Zed Outsider", "relationship": "friend"}],
            "contingent": [{"name": "Synthetic Spouse", "relationship": "spouse"}],
        },
    )
    persons = [{"full_name": "Synthetic Spouse"}, {"full_name": "Synthetic Child"}]
    flags = asset_service.evaluate_alignment_flags(asset, persons)
    assert len(flags) == 1
    assert flags[0]["flag_type"] == "non_household_beneficiary"
    assert flags[0]["severity"] == "review"
    assert "Zed Outsider" in flags[0]["message"]


def test_engine_missing_contingent_on_retirement() -> None:
    asset = _engine_asset(
        "retirement",
        {"primary": [{"name": "Synthetic Spouse", "relationship": "spouse"}], "contingent": []},
    )
    flags = asset_service.evaluate_alignment_flags(asset, [{"full_name": "Synthetic Spouse"}])
    assert [f["flag_type"] for f in flags] == ["missing_contingent"]
    assert flags[0]["severity"] == "info"


def test_engine_missing_contingent_not_raised_for_bank_account() -> None:
    asset = _engine_asset(
        "bank_account",
        {"primary": [{"name": "Synthetic Spouse", "relationship": "spouse"}]},
    )
    flags = asset_service.evaluate_alignment_flags(asset, [{"full_name": "Synthetic Spouse"}])
    assert flags == []


def test_engine_clean_asset_has_no_flags() -> None:
    asset = _engine_asset(
        "insurance",
        {
            "primary": [{"name": "Synthetic Spouse", "relationship": "spouse"}],
            "contingent": [{"name": "Synthetic Child", "relationship": "child"}],
        },
    )
    persons = [{"full_name": "Synthetic Spouse"}, {"full_name": "Synthetic Child"}]
    assert asset_service.evaluate_alignment_flags(asset, persons) == []


def test_engine_name_match_is_case_insensitive_and_trims() -> None:
    asset = _engine_asset(
        "bank_account",
        {"primary": [{"name": "  synthetic spouse ", "relationship": "spouse"}]},
    )
    flags = asset_service.evaluate_alignment_flags(asset, [{"full_name": "Synthetic Spouse"}])
    assert flags == []


def test_engine_multiple_unmatched_names_sorted() -> None:
    asset = _engine_asset(
        "bank_account",
        {
            "primary": [
                {"name": "Zed Outsider", "relationship": "friend"},
                {"name": "Amy Outsider", "relationship": "sister"},
            ]
        },
    )
    flags = asset_service.evaluate_alignment_flags(asset, [])
    unmatched = [f for f in flags if f["flag_type"] == "non_household_beneficiary"]
    assert [f["message"] for f in unmatched] == [
        "Beneficiary 'Amy Outsider' is not recorded as a person in this household.",
        "Beneficiary 'Zed Outsider' is not recorded as a person in this household.",
    ]


# ---------------------------------------------------------------------------
# Asset CRUD + authorization
# ---------------------------------------------------------------------------
def test_create_asset_as_attorney(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    body = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    assert body["asset_type"] == "bank_account"
    assert body["label"] == "Synthetic checking"
    assert body["estimated_value_cents"] == 250000
    assert body["firm_id"] == ids["firm_a"]
    assert body["household_id"] == ids["household_a"]
    assert body["matter_id"] is None
    # beneficiary_designation is NOT NULL in the DB: an empty designation.
    assert body["beneficiary_designation"] == {"primary": [], "contingent": []}


def test_create_asset_as_household_collaborator(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    body = create_asset_api(api_client, ids["household_a"], COLLAB_A)
    assert body["household_id"] == ids["household_a"]


def test_create_asset_as_dependent_forbidden(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    resp = api_client.post(
        f"/v1/households/{ids['household_a']}/assets",
        json=asset_payload(),
        headers=auth(DEPENDENT_A),
    )
    assert resp.status_code == 403, resp.text


def test_create_asset_cross_firm_household_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    resp = api_client.post(
        f"/v1/households/{ids['household_a']}/assets",
        json=asset_payload(),
        headers=auth(ADMIN_B),
    )
    assert resp.status_code == 404, resp.text


def test_create_asset_rejects_unknown_household(api_client: TestClient, db) -> None:
    setup_two_firms(db)
    resp = api_client.post(
        f"/v1/households/{uuid4()}/assets", json=asset_payload(), headers=auth(ATTORNEY_A)
    )
    assert resp.status_code == 404, resp.text


def test_create_asset_rejects_invalid_asset_type(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    resp = api_client.post(
        f"/v1/households/{ids['household_a']}/assets",
        json=asset_payload(asset_type="yacht"),
        headers=auth(ATTORNEY_A),
    )
    assert resp.status_code == 422, resp.text


def test_create_asset_with_matter_id(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    matter_id = insert_matter(db, ids["firm_a"], ids["household_a"])
    body = create_asset_api(api_client, ids["household_a"], ATTORNEY_A, matter_id=matter_id)
    assert body["matter_id"] == matter_id


def test_create_asset_rejects_matter_from_other_household(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    other_matter = insert_matter(db, ids["firm_b"], ids["household_b"])
    resp = api_client.post(
        f"/v1/households/{ids['household_a']}/assets",
        json=asset_payload(matter_id=other_matter),
        headers=auth(ATTORNEY_A),
    )
    assert resp.status_code == 404, resp.text


def test_list_assets_pagination(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    for i in range(3):
        create_asset_api(api_client, ids["household_a"], ATTORNEY_A, label=f"Asset {i}")
    resp = api_client.get(
        f"/v1/households/{ids['household_a']}/assets?page=1&page_size=2",
        headers=auth(ATTORNEY_A),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2
    resp2 = api_client.get(
        f"/v1/households/{ids['household_a']}/assets?page=2&page_size=2",
        headers=auth(ATTORNEY_A),
    )
    assert len(resp2.json()["items"]) == 1


def test_list_assets_as_dependent_read_allowed(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.get(f"/v1/households/{ids['household_a']}/assets", headers=auth(DEPENDENT_A))
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


def test_get_asset_as_primary_client(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.get(f"/v1/assets/{asset['id']}", headers=auth(CLIENT_A))
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == asset["id"]


def test_get_asset_as_auditor_forbidden(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.get(f"/v1/assets/{asset['id']}", headers=auth(AUDITOR_A))
    assert resp.status_code == 403, resp.text


def test_get_asset_cross_tenant_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    # Firm B's admin sees firm A's asset as "not found".
    resp = api_client.get(f"/v1/assets/{asset['id']}", headers=auth(ADMIN_B))
    assert resp.status_code == 404, resp.text
    # Firm B's household client also sees "not found".
    resp = api_client.get(f"/v1/assets/{asset['id']}", headers=auth(CLIENT_B))
    assert resp.status_code == 404, resp.text
    # Firm A's own household list is untouched.
    resp = api_client.get(f"/v1/households/{ids['household_a']}/assets", headers=auth(ADMIN_B))
    assert resp.status_code == 404, resp.text


def test_update_asset(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.patch(
        f"/v1/assets/{asset['id']}",
        json={"label": "Renamed checking", "estimated_value_cents": 300000},
        headers=auth(CLIENT_A),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == "Renamed checking"
    assert body["estimated_value_cents"] == 300000
    assert body["asset_type"] == "bank_account"


def test_update_asset_cross_tenant_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.patch(
        f"/v1/assets/{asset['id']}", json={"label": "Hijacked"}, headers=auth(ADMIN_B)
    )
    assert resp.status_code == 404, resp.text


def test_delete_asset(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.delete(f"/v1/assets/{asset['id']}", headers=auth(STAFF_A))
    assert resp.status_code == 204, resp.text
    assert api_client.get(f"/v1/assets/{asset['id']}", headers=auth(ATTORNEY_A)).status_code == 404


def test_delete_asset_cross_tenant_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.delete(f"/v1/assets/{asset['id']}", headers=auth(ADMIN_B))
    assert resp.status_code == 404, resp.text
    # The asset still exists for its own firm.
    assert api_client.get(f"/v1/assets/{asset['id']}", headers=auth(ATTORNEY_A)).status_code == 200


# ---------------------------------------------------------------------------
# Beneficiary designation
# ---------------------------------------------------------------------------
def test_update_beneficiary(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    create_person(db, ids["household_a"], "Synthetic Spouse")
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    designation = {
        "primary": [{"name": "Synthetic Spouse", "relationship": "spouse"}],
        "contingent": [{"name": "Synthetic Child", "relationship": "child"}],
    }
    resp = api_client.patch(
        f"/v1/assets/{asset['id']}/beneficiary", json=designation, headers=auth(CLIENT_A)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["beneficiary_designation"]["primary"][0]["name"] == "Synthetic Spouse"
    assert body["beneficiary_designation"]["contingent"][0]["relationship"] == "child"

    events = audit_events_for(db, asset["id"])
    beneficiary_events = [e for e in events if e["action"] == "assets.beneficiary_updated"]
    assert len(beneficiary_events) == 1
    metadata = beneficiary_events[0]["metadata"]
    assert metadata["primary_count"] == 1 and metadata["contingent_count"] == 1
    # Never beneficiary names in audit metadata.
    assert "Synthetic Spouse" not in json.dumps(metadata)


def test_update_beneficiary_cross_tenant_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.patch(
        f"/v1/assets/{asset['id']}/beneficiary",
        json={"primary": [{"name": "Intruder"}]},
        headers=auth(ADMIN_B),
    )
    assert resp.status_code == 404, resp.text


def test_update_beneficiary_dependent_forbidden(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.patch(
        f"/v1/assets/{asset['id']}/beneficiary",
        json={"primary": [{"name": "Synthetic Spouse"}]},
        headers=auth(DEPENDENT_A),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Alignment evaluation endpoint
# ---------------------------------------------------------------------------
def test_evaluate_alignment_regenerates_flags(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    create_person(db, ids["household_a"], "Synthetic Spouse")
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A, asset_type="retirement")

    # No designation: unknown_beneficiary (review) + missing_contingent (info).
    resp = api_client.post(f"/v1/assets/{asset['id']}/evaluate-alignment", headers=auth(ATTORNEY_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["asset_id"] == asset["id"]
    assert [f["flag_type"] for f in body["flags"]] == ["unknown_beneficiary", "missing_contingent"]
    assert body["flag_counts"] == {"unknown_beneficiary": 1, "missing_contingent": 1}

    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM asset_flags WHERE asset_id = %s", (asset["id"],))
        assert int(cur.fetchone()["n"]) == 2
    db.commit()

    # Add a clean designation (primary matches, contingent present): no flags.
    api_client.patch(
        f"/v1/assets/{asset['id']}/beneficiary",
        json={
            "primary": [{"name": "Synthetic Spouse", "relationship": "spouse"}],
            "contingent": [{"name": "synthetic spouse", "relationship": "spouse"}],
        },
        headers=auth(CLIENT_A),
    )
    resp = api_client.post(f"/v1/assets/{asset['id']}/evaluate-alignment", headers=auth(ATTORNEY_A))
    body = resp.json()
    assert body["flags"] == []
    assert body["flag_counts"] == {}
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM asset_flags WHERE asset_id = %s", (asset["id"],))
        assert int(cur.fetchone()["n"]) == 0
    db.commit()

    # Non-household beneficiary on a retirement asset: both flags regenerate.
    api_client.patch(
        f"/v1/assets/{asset['id']}/beneficiary",
        json={"primary": [{"name": "Zed Outsider", "relationship": "friend"}]},
        headers=auth(CLIENT_A),
    )
    resp = api_client.post(f"/v1/assets/{asset['id']}/evaluate-alignment", headers=auth(ATTORNEY_A))
    body = resp.json()
    assert [f["flag_type"] for f in body["flags"]] == [
        "non_household_beneficiary",
        "missing_contingent",
    ]
    # Regeneration is idempotent: re-evaluating yields the same flag set.
    again = api_client.post(
        f"/v1/assets/{asset['id']}/evaluate-alignment", headers=auth(ATTORNEY_A)
    )
    assert [f["flag_type"] for f in again.json()["flags"]] == [
        "non_household_beneficiary",
        "missing_contingent",
    ]

    # Audit carries summary counts only — never values or beneficiary names.
    events = audit_events_for(db, asset["id"])
    evaluated = [e for e in events if e["action"] == "assets.alignment_evaluated"]
    assert evaluated, "expected an assets.alignment_evaluated audit event"
    metadata = evaluated[-1]["metadata"]
    assert metadata["flag_count"] == 2
    assert metadata["flag_types"] == {"non_household_beneficiary": 1, "missing_contingent": 1}
    blob = json.dumps(metadata)
    assert "Zed Outsider" not in blob
    assert "estimated_value_cents" not in blob


def test_evaluate_alignment_cross_tenant_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.post(f"/v1/assets/{asset['id']}/evaluate-alignment", headers=auth(ADMIN_B))
    assert resp.status_code == 404, resp.text


def test_evaluate_alignment_dependent_forbidden(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    resp = api_client.post(
        f"/v1/assets/{asset['id']}/evaluate-alignment", headers=auth(DEPENDENT_A)
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
def test_asset_audit_trail_summary_only(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    asset = create_asset_api(api_client, ids["household_a"], ATTORNEY_A)
    api_client.patch(f"/v1/assets/{asset['id']}", json={"label": "Renamed"}, headers=auth(CLIENT_A))
    api_client.delete(f"/v1/assets/{asset['id']}", headers=auth(ATTORNEY_A))

    events = audit_events_for(db, asset["id"])
    actions = [e["action"] for e in events]
    assert actions == ["assets.created", "assets.updated", "assets.deleted"]
    for event in events:
        blob = json.dumps(event["metadata"])
        # Never values, labels, or notes in audit summaries.
        assert "Synthetic checking" not in blob
        assert "Renamed" not in blob
        assert "250000" not in blob


# ---------------------------------------------------------------------------
# Liabilities
# ---------------------------------------------------------------------------
def test_liability_crud(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    liability = create_liability_api(api_client, ids["household_a"], STAFF_A)
    assert liability["liability_type"] == "mortgage"
    assert liability["balance_cents"] == 18000000
    assert liability["monthly_payment_cents"] == 120000
    assert liability["firm_id"] == ids["firm_a"]

    resp = api_client.get(
        f"/v1/households/{ids['household_a']}/liabilities", headers=auth(CLIENT_A)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1

    resp = api_client.get(f"/v1/liabilities/{liability['id']}", headers=auth(DEPENDENT_A))
    assert resp.status_code == 200, resp.text

    resp = api_client.patch(
        f"/v1/liabilities/{liability['id']}",
        json={"balance_cents": 17500000, "monthly_payment_cents": None},
        headers=auth(COLLAB_A),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["balance_cents"] == 17500000
    assert resp.json()["monthly_payment_cents"] is None

    resp = api_client.delete(f"/v1/liabilities/{liability['id']}", headers=auth(CLIENT_A))
    assert resp.status_code == 204, resp.text
    assert (
        api_client.get(f"/v1/liabilities/{liability['id']}", headers=auth(ATTORNEY_A)).status_code
        == 404
    )


def test_liability_cross_tenant_404(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    liability = create_liability_api(api_client, ids["household_a"], ATTORNEY_A)
    lid = liability["id"]
    assert api_client.get(f"/v1/liabilities/{lid}", headers=auth(ADMIN_B)).status_code == 404
    assert (
        api_client.patch(
            f"/v1/liabilities/{lid}", json={"label": "Hijacked"}, headers=auth(ADMIN_B)
        ).status_code
        == 404
    )
    assert api_client.delete(f"/v1/liabilities/{lid}", headers=auth(ADMIN_B)).status_code == 404
    resp = api_client.post(
        f"/v1/households/{ids['household_a']}/liabilities",
        json=liability_payload(),
        headers=auth(ADMIN_B),
    )
    assert resp.status_code == 404, resp.text
    resp = api_client.get(
        f"/v1/households/{ids['household_a']}/liabilities", headers=auth(CLIENT_B)
    )
    assert resp.status_code == 404, resp.text


def test_liability_audit_trail(api_client: TestClient, db) -> None:
    ids = setup_two_firms(db)
    liability = create_liability_api(api_client, ids["household_a"], ATTORNEY_A)
    api_client.patch(
        f"/v1/liabilities/{liability['id']}", json={"balance_cents": 1}, headers=auth(ATTORNEY_A)
    )
    api_client.delete(f"/v1/liabilities/{liability['id']}", headers=auth(ATTORNEY_A))
    events = audit_events_for(db, liability["id"])
    assert [e["action"] for e in events] == [
        "liabilities.created",
        "liabilities.updated",
        "liabilities.deleted",
    ]
