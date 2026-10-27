"""Self-serve provisioning + jurisdiction-default routing (Phase 2).

T2: ``POST /v1/matters`` — the household_id path resolves the firm from
the household (household-only primary_client/collaborator may create;
other members 403; non-members 404) and the omitted-household_id path
provisions a self-serve household under the jurisdiction's default firm
(``jurisdiction_default_firms``), failing 422 with code
``no_default_firm_for_jurisdiction`` when no default firm is registered.

T3: a self-serve matter completes the family draft flow (Sharma-complete
answers -> generate 5 packages -> submit -> ATTORNEY_REVIEW with the
default firm's enrolled attorney + a review packet); a default firm
without an enrolled attorney submits 409 (NoAttorneyAvailableError).

Runs against pgserver with the full migration sweep (including
20261027150000_self_serve_provisioning.sql) and a scratch copy of the
real us-va packs. Fixture data is synthetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities
# ---------------------------------------------------------------------------

ADMIN_A = "e1111111-1111-1111-1111-111111111111"  # firm_admin of Firm A (default)
STAFF_A = "e2222222-2222-2222-2222-222222222222"  # staff of Firm A
CLIENT = "e3333333-3333-3333-3333-333333333333"  # household-only primary_client
DEPENDENT = "e4444444-4444-4444-4444-444444444444"  # household dependent
FIRMLESS = "e5555555-5555-5555-5555-555555555555"  # no firm, no household
DUAL = "e6666666-6666-6666-6666-666666666666"  # firm_admin of Firm B only
LAWYER = "e7777777-7777-7777-7777-777777777777"  # routable VA attorney (Firm A)
OUTSIDER = "e8888888-8888-8888-8888-888888888888"  # no access at all
ADMIN_B = "e9999999-9999-9999-9999-999999999999"  # firm_admin of Firm B

USERS = {
    ADMIN_A: "admin-a@example.test",
    STAFF_A: "staff-a@example.test",
    CLIENT: "client@example.test",
    DEPENDENT: "dependent@example.test",
    FIRMLESS: "firmless@example.test",
    DUAL: "dual@example.test",
    LAWYER: "lawyer@example.test",
    OUTSIDER: "outsider@example.test",
    ADMIN_B: "admin-b@example.test",
}

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, jurisdiction_default_firms,"
    " firm_memberships, firms, profiles,"
    " feature_flags, attorney_applications, review_packets, approval_gates,"
    " comments, clarification_requests,"
    " asset_flags, assets, liabilities, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY, name text, public boolean
);
"""

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
API_CONTENT_PACKS_DIR = Path(__file__).resolve().parents[1] / "content-packs"
V2_QUESTION_SETS_DIR = API_CONTENT_PACKS_DIR / "us-va" / "v2" / "question-sets"

SHARMA_PROFILE = {
    "has_minor_children": True,
    "wants_revocable_trust": True,
    "wants_poa_document": True,
    "wants_healthcare_directive": True,
}

TEMPLATE_BODIES = {
    "will": "LAST WILL AND TESTAMENT of {{household_name}}\nMarker WILL.",
    "poa": "DURABLE POWER OF ATTORNEY for {{household_name}}\nMarker POA.",
    "advance_directive": "ADVANCE MEDICAL DIRECTIVE for {{household_name}}\nMarker AMD.",
    "guardianship": "GUARDIANSHIP DESIGNATION for {{household_name}}\nMarker GUARDIAN.",
    "trust": "REVOCABLE LIVING TRUST for {{household_name}}\nMarker TRUST.",
}

TEMPLATE_NAMES = {
    "will": "Last Will and Testament (Virginia)",
    "poa": "Durable Power of Attorney (Virginia)",
    "advance_directive": "Advance Medical Directive (Virginia)",
    "guardianship": "Designation of Guardian for Minor Children (Virginia)",
    "trust": "Revocable Living Trust (Virginia)",
}


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_family_draft_documents.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-self-serve")))
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
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def api_client(pg_uri, monkeypatch, tmp_path):
    storage_dir = tmp_path / "documents"
    storage_dir.mkdir()
    monkeypatch.setenv("DOCUMENT_STORAGE_DIR", str(storage_dir))
    scratch_packs = tmp_path / "content-packs"
    shutil.copytree(API_CONTENT_PACKS_DIR, scratch_packs)
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(scratch_packs))
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.delenv("LF_FLAG_DOCUMENT_ASSEMBLY", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.domains.feature_flags import service as flags_service

    flags_service._flag_cache.clear()
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _one(db, sql: str, params: tuple = ()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def create_user(db, sub: str) -> None:
    email = USERS.get(sub, f"{sub}@example.test")
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    _exec(
        db,
        "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
        (sub, email, f"Synthetic {email}"),
    )


def create_all_users(db) -> None:
    for sub in USERS:
        create_user(db, sub)


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


def create_household(db, firm_id: str, created_by: str, name: str = "Sharma Household") -> str:
    household_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, firm_id, name, created_by),
    )
    return household_id


def add_household_member(db, household_id: str, sub: str, relationship_role: str) -> None:
    _exec(
        db,
        "INSERT INTO household_members (household_id, user_id, relationship_role, access_status)"
        " VALUES (%s, %s, %s, 'active')",
        (household_id, sub, relationship_role),
    )


def register_default_firm(db, jurisdiction_code: str, firm_id: str) -> None:
    _exec(
        db,
        "INSERT INTO jurisdiction_default_firms (jurisdiction_code, firm_id)"
        " VALUES (%s, %s)",
        (jurisdiction_code, firm_id),
    )


def enroll_attorney(db, firm_id: str) -> None:
    _exec(
        db,
        "INSERT INTO attorney_applications"
        " (firm_id, user_id, full_name, email, bar_number, jurisdictions, status)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'approved')",
        (firm_id, LAWYER, "E2E Attorney Test", USERS[LAWYER], "VA-99999-E2E", ["US-VA"]),
    )
    add_firm_member(db, firm_id, LAWYER, "attorney")


def set_assembly_flag(db, firm_id: str, enabled: bool) -> None:
    _exec(
        db,
        "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled, created_by_user_id)"
        " VALUES ('document_assembly', 'firm', %s, %s, %s)"
        " ON CONFLICT (key, scope, scope_ref) DO UPDATE SET is_enabled = EXCLUDED.is_enabled",
        (firm_id, enabled, ADMIN_A),
    )
    from app.domains.feature_flags import service as flags_service

    flags_service._flag_cache.clear()


def seed_all_templates(db, firm_id: str) -> None:
    for template_type, body in TEMPLATE_BODIES.items():
        _exec(
            db,
            "INSERT INTO document_templates"
            " (id, firm_id, name, template_type, pack_id, version, content, created_by_user_id)"
            " VALUES (%s, %s, %s, %s, 'us-va', '1.0.0', %s::jsonb, %s)",
            (
                str(uuid4()),
                firm_id,
                TEMPLATE_NAMES[template_type],
                template_type,
                json.dumps({"body": body}),
                ADMIN_A,
            ),
        )


def token(sub: str) -> str:
    return mint_token(sub=sub, email=USERS.get(sub, f"{sub}@example.test"))


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token(sub)}


def create_matter_api(api_client, sub: str, payload: dict):
    return api_client.post("/v1/matters", json=payload, headers=auth(sub))


def setup_default_firm(db, *, with_attorney: bool = False) -> str:
    """Firm A registered as the US-VA default firm."""
    create_all_users(db)
    firm_id = create_firm(db, "Virginia Default Firm")
    add_firm_member(db, firm_id, ADMIN_A, "firm_admin")
    add_firm_member(db, firm_id, STAFF_A, "staff")
    register_default_firm(db, "US-VA", firm_id)
    if with_attorney:
        enroll_attorney(db, firm_id)
    return firm_id


def setup_household_world(db) -> tuple[str, str]:
    """Firm A (default) + a firm-origin household with CLIENT/DEPENDENT."""
    firm_id = setup_default_firm(db)
    household_id = create_household(db, firm_id, ADMIN_A)
    add_household_member(db, household_id, CLIENT, "primary_client")
    add_household_member(db, household_id, DEPENDENT, "dependent")
    return firm_id, household_id


# ---------------------------------------------------------------------------
# T2 — self-serve path (household_id omitted)
# ---------------------------------------------------------------------------


def test_firmless_create_without_household_id_uses_default_firm(db, api_client):
    firm_id = setup_default_firm(db)
    response = create_matter_api(
        api_client,
        FIRMLESS,
        {
            "jurisdiction_code": "US-VA",
            "household_name": "Firmless Household",
            "title": "Firmless Plan",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["firm_id"] == firm_id
    household_id = body["household_id"]
    household = _one(db, "SELECT * FROM households WHERE id = %s", (household_id,))
    assert household is not None
    assert household["origin"] == "self_serve"
    assert household["display_name"] == "Firmless Household"
    assert str(household["firm_id"]) == firm_id
    member = _one(
        db,
        "SELECT * FROM household_members WHERE household_id = %s AND user_id = %s",
        (household_id, FIRMLESS),
    )
    assert member is not None
    assert member["relationship_role"] == "primary_client"
    assert member["access_status"] == "active"


def test_second_self_serve_create_reuses_same_household(db, api_client):
    setup_default_firm(db)
    first = create_matter_api(api_client, FIRMLESS, {"jurisdiction_code": "US-VA"})
    assert first.status_code == 201, first.text
    second = create_matter_api(
        api_client, FIRMLESS, {"jurisdiction_code": "US-VA", "title": "Second plan"}
    )
    assert second.status_code == 201, second.text
    assert second.json()["household_id"] == first.json()["household_id"]
    row = _one(
        db,
        "SELECT count(*) AS n FROM households WHERE created_by_user_id = %s",
        (FIRMLESS,),
    )
    assert row["n"] == 1


def test_self_serve_unregistered_jurisdiction_is_422_no_default_firm(db, api_client):
    setup_default_firm(db)  # registers US-VA only; US-XZ stays unregistered
    response = create_matter_api(api_client, FIRMLESS, {"jurisdiction_code": "US-XZ"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "no_default_firm_for_jurisdiction"
    assert "US-XZ" in body["detail"]


def test_self_serve_unknown_jurisdiction_is_422_unknown_jurisdiction(db, api_client):
    # D4 ordering pin: pack resolution runs BEFORE the registry lookup,
    # so a jurisdiction with no usable pack at all (US-QQ has neither a
    # pack nor a registry row) fails as unknown_jurisdiction — never as
    # no_default_firm_for_jurisdiction.
    setup_default_firm(db)
    response = create_matter_api(api_client, FIRMLESS, {"jurisdiction_code": "US-QQ"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "unknown_jurisdiction"
    assert body["code"] != "no_default_firm_for_jurisdiction"


def test_self_serve_does_not_reuse_firm_origin_household(db, api_client):
    # D3 reuse-guard pin: the caller's pre-existing household in the
    # default firm with origin='firm' (even with an active primary_client
    # member row) must NOT be reused by the self-serve path — a fresh
    # origin='self_serve' household is provisioned instead.
    firm_id = setup_default_firm(db)
    firm_household_id = create_household(db, firm_id, FIRMLESS, "Firm-Origin Household")
    add_household_member(db, firm_household_id, FIRMLESS, "primary_client")
    response = create_matter_api(api_client, FIRMLESS, {"jurisdiction_code": "US-VA"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["household_id"] != firm_household_id
    household = _one(db, "SELECT * FROM households WHERE id = %s", (body["household_id"],))
    assert household is not None
    assert household["origin"] == "self_serve"
    row = _one(
        db,
        "SELECT count(*) AS n FROM households WHERE created_by_user_id = %s",
        (FIRMLESS,),
    )
    assert row["n"] == 2


def test_dual_hat_user_omitted_household_creates_via_self_serve(db, api_client):
    firm_id = setup_default_firm(db)
    other_firm = create_firm(db, "Other Firm")
    add_firm_member(db, other_firm, DUAL, "firm_admin")
    response = create_matter_api(api_client, DUAL, {"jurisdiction_code": "US-VA"})
    assert response.status_code == 201, response.text
    body = response.json()
    # Anchored to the default firm, not the caller's own firm.
    assert body["firm_id"] == firm_id
    household = _one(db, "SELECT * FROM households WHERE id = %s", (body["household_id"],))
    assert household["origin"] == "self_serve"


# ---------------------------------------------------------------------------
# T2 — household_id path (firm resolved from the household)
# ---------------------------------------------------------------------------


def test_household_path_primary_client_can_create(db, api_client):
    firm_id, household_id = setup_household_world(db)
    response = create_matter_api(
        api_client, CLIENT, {"household_id": household_id, "jurisdiction_code": "US-VA"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["firm_id"] == firm_id
    assert response.json()["household_id"] == household_id


def test_household_path_dependent_is_403(db, api_client):
    _firm_id, household_id = setup_household_world(db)
    response = create_matter_api(
        api_client, DEPENDENT, {"household_id": household_id, "jurisdiction_code": "US-VA"}
    )
    assert response.status_code == 403, response.text


def test_household_path_outsider_is_404(db, api_client):
    _firm_id, household_id = setup_household_world(db)
    response = create_matter_api(
        api_client, OUTSIDER, {"household_id": household_id, "jurisdiction_code": "US-VA"}
    )
    assert response.status_code == 404, response.text


def test_household_path_cross_firm_staff_is_404(db, api_client):
    _firm_id, household_id = setup_household_world(db)
    other_firm = create_firm(db, "Firm B")
    add_firm_member(db, other_firm, ADMIN_B, "firm_admin")
    response = create_matter_api(
        api_client, ADMIN_B, {"household_id": household_id, "jurisdiction_code": "US-VA"}
    )
    assert response.status_code == 404, response.text


def test_household_path_staff_own_firm_unchanged(db, api_client):
    firm_id, household_id = setup_household_world(db)
    response = create_matter_api(
        api_client, STAFF_A, {"household_id": household_id, "jurisdiction_code": "US-VA"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["firm_id"] == firm_id


# ---------------------------------------------------------------------------
# T3 — routing proof through the family draft flow
# ---------------------------------------------------------------------------


def _all_pack_questions() -> list[dict]:
    questions: list[dict] = []
    for path in sorted(V2_QUESTION_SETS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for section in data.get("sections", []):
            questions.extend(section.get("questions", []))
    return questions


def answer_everything(db, matter_id: str, profile: dict) -> None:
    for question in _all_pack_questions():
        key = str(question["key"])
        if key in profile:
            value = profile[key]
        elif question.get("type") == "boolean":
            value = True
        elif question.get("type") == "single_select":
            options = question.get("options") or []
            value = options[0]["value"] if options else "synthetic"
        elif question.get("type") == "multi_select":
            options = question.get("options") or []
            value = [options[0]["value"]] if options else ["synthetic"]
        else:
            value = f"Synthetic answer for {key}"
        _exec(
            db,
            "INSERT INTO intake_answers (matter_id, question_key, answer)"
            " VALUES (%s, %s, %s::jsonb)"
            " ON CONFLICT (matter_id, question_key) DO UPDATE SET answer = EXCLUDED.answer",
            (matter_id, key, json.dumps(value)),
        )


def _ready_self_serve_matter(db, api_client, *, with_attorney: bool) -> tuple[str, str]:
    firm_id = setup_default_firm(db, with_attorney=with_attorney)
    set_assembly_flag(db, firm_id, True)
    seed_all_templates(db, firm_id)
    response = create_matter_api(
        api_client,
        FIRMLESS,
        {"jurisdiction_code": "US-VA", "household_name": "Sharma Household"},
    )
    assert response.status_code == 201, response.text
    matter_id = response.json()["id"]
    answer_everything(db, matter_id, SHARMA_PROFILE)
    return firm_id, matter_id


def test_self_serve_matter_routes_to_default_firm_attorney(db, api_client):
    _firm_id, matter_id = _ready_self_serve_matter(db, api_client, with_attorney=True)
    generated = api_client.post(
        f"/v1/matters/{matter_id}/draft-documents/generate", headers=auth(FIRMLESS)
    )
    assert generated.status_code == 201, generated.text
    assert len(generated.json()["packages"]) == 5

    submitted = api_client.post(
        f"/v1/matters/{matter_id}/draft-documents/submit", headers=auth(FIRMLESS)
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["matter_status"] == "ATTORNEY_REVIEW"
    assert body["assigned_attorney"]["user_id"] == LAWYER

    matter = _one(db, "SELECT status, assigned_attorney_id FROM matters WHERE id = %s", (matter_id,))
    assert matter["status"] == "ATTORNEY_REVIEW"
    assert str(matter["assigned_attorney_id"]) == LAWYER
    packet = _one(db, "SELECT count(*) AS n FROM review_packets WHERE matter_id = %s", (matter_id,))
    assert packet["n"] == 1


def test_self_serve_submit_default_firm_without_attorney_is_409(db, api_client):
    _firm_id, matter_id = _ready_self_serve_matter(db, api_client, with_attorney=False)
    generated = api_client.post(
        f"/v1/matters/{matter_id}/draft-documents/generate", headers=auth(FIRMLESS)
    )
    assert generated.status_code == 201, generated.text
    submitted = api_client.post(
        f"/v1/matters/{matter_id}/draft-documents/submit", headers=auth(FIRMLESS)
    )
    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["code"] == "no_attorney_available"
