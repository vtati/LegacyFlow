"""Feature flags + legal-content lifecycle API tests.

Covers ``app/domains/feature_flags/{repository,service,schemas,router}.py``,
the document-assembly gate in ``app/domains/documents/service.py``
(``create_package`` / ``generate_demo`` fail closed with HTTP 409 /
``document_assembly_disabled`` unless the ``document_assembly`` flag is
effective for (firm_id, pack_id) AND the pack manifest declares the
capability), and the jurisdiction-pack lifecycle transitions in
``app/domains/review/`` (``POST /v1/admin/packs/{pack_id}/transition``).

These tests run against the real migrations (including
``20260927040000_feature_flags_pack_lifecycle.sql``), applied from the
migrations directory by the ``pg_uri`` fixture.

All fixture data is synthetic. No test presents demo documents as legally
valid.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from pypdf import PdfReader

from app.domains.feature_flags import service as flags_service
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------

ADMIN_A = "c1111111-1111-1111-1111-111111111111"
ATTORNEY_A = "c2222222-2222-2222-2222-222222222222"
STAFF_A = "c3333333-3333-3333-3333-333333333333"

USERS = {
    ADMIN_A: "admin.a@example.test",
    ATTORNEY_A: "attorney.a@example.test",
    STAFF_A: "staff.a@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

DEMO_TEXT = "DEMO ONLY — NOT LEGALLY VALID"

TRUNCATE_TABLES = (
    "feature_flags, document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " asset_flags, assets, liabilities, jurisdictions, jurisdiction_packs, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
"""

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
REPO_CONTENT_PACKS_DIR = Path(__file__).resolve().parents[3] / "content-packs"

# Minimal synthetic pack with the document_assembly capability enabled.
US_TEST_MANIFEST = {
    "pack": {
        "id": "us-test",
        "country_code": "US",
        "subdivision_code": "TT",
        "display_name": "Synthetic Test Pack",
        "semantic_version": "0.1.0",
        "status": "approved",
        "effective_from": "2026-09-27",
    },
    "capabilities": {
        "intake": True,
        "document_assembly": True,
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start pgserver once; apply auth stub + migrations + jurisdiction seed."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-feature-flags")))
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
def api_client(pg_uri, monkeypatch, tmp_path):
    """TestClient with pgserver as DATABASE_URL and the test JWT override.

    The pack loader points at a scratch ``content-packs`` dir holding only
    the synthetic ``us-test`` pack (document_assembly capability on), and
    every ``LF_FLAG_*`` override is cleared so tests start from defaults.
    """
    storage_dir = tmp_path / "documents"
    storage_dir.mkdir()
    monkeypatch.setenv("DOCUMENT_STORAGE_DIR", str(storage_dir))
    packs_dir = tmp_path / "content-packs" / "us-test" / "v0"
    packs_dir.mkdir(parents=True)
    (packs_dir / "manifest.yaml").write_text(yaml.safe_dump(US_TEST_MANIFEST), encoding="utf-8")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(tmp_path / "content-packs"))
    for key in ("LF_FLAG_DOCUMENT_ASSEMBLY", "LF_FLAG_AI_ASSISTANCE", "LF_FLAG_FUNDING_REMINDERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Rate limiting is covered by its own dedicated tests; leave it off here
    # so these tests are not order-dependent (429s).
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def create_user(db, sub: str) -> None:
    email = USERS.get(sub, f"{sub}@example.test")
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


def insert_matter(
    db,
    firm_id: str,
    household_id: str,
    pack_id: str = "us-va",
    jurisdiction_code: str = "US-VA",
    semantic_version: str = "0.1.0",
) -> str:
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {
            "pack_id": pack_id,
            "jurisdiction_code": jurisdiction_code,
            "semantic_version": semantic_version,
        }
    )
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
            snapshot,
        ),
    )
    return matter_id


def insert_flag(db, key: str, scope: str, scope_ref: str, is_enabled: bool) -> None:
    _exec(
        db,
        "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled) VALUES (%s, %s, %s, %s)",
        (key, scope, scope_ref, is_enabled),
    )


def setup_firm(db):
    """Standard synthetic tree: firm with admin + attorney + staff."""
    for sub in USERS:
        create_user(db, sub)
    firm_id = create_firm(db)
    add_firm_member(db, firm_id, ADMIN_A, "firm_admin")
    add_firm_member(db, firm_id, ATTORNEY_A, "attorney")
    add_firm_member(db, firm_id, STAFF_A, "staff")
    return firm_id


def token(sub: str) -> str:
    return mint_token(sub=sub, email=USERS.get(sub, f"{sub}@example.test"))


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub)}"}


def create_jurisdiction_pack(db, *, status: str, pack_id: str = "us-zz-test") -> str:
    """Insert a jurisdiction + pack row; return the pack UUID."""
    jurisdiction_id = str(uuid4())
    pack_uuid = str(uuid4())
    _exec(
        db,
        "INSERT INTO jurisdictions (id, country_code, subdivision_code, display_name,"
        " jurisdiction_type, is_enabled)"
        " VALUES (%s, 'US', 'ZZ', 'Synthetic ZZ', 'us_state', true)",
        (jurisdiction_id,),
    )
    manifest = json.dumps(
        {
            "pack": {
                "id": pack_id,
                "country_code": "US",
                "subdivision_code": "ZZ",
                "display_name": "Synthetic ZZ",
                "semantic_version": "0.1.0",
                "status": status,
            },
            "capabilities": {"document_assembly": False},
        }
    )
    _exec(
        db,
        "INSERT INTO jurisdiction_packs (id, jurisdiction_id, version, status,"
        " effective_from, manifest, source_bundle_hash)"
        " VALUES (%s, %s, '0.1.0', %s, CURRENT_DATE, %s::jsonb, 'synthetic')",
        (pack_uuid, jurisdiction_id, status, manifest),
    )
    return pack_uuid


WILL_TEMPLATE = {
    "name": "Synthetic Will Template",
    "template_type": "will",
    "pack_id": "us-test",
    "version": "1.0.0",
    "content": "LAST WILL AND TESTAMENT of {{household_name}}\n",
}


def create_template_via_api(api_client: TestClient, sub: str) -> dict:
    response = api_client.post("/v1/document-templates", json=WILL_TEMPLATE, headers=auth(sub))
    assert response.status_code == 201, response.text
    return response.json()


def pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


# ---------------------------------------------------------------------------
# Flag resolution precedence
# ---------------------------------------------------------------------------


def test_flag_resolution_precedence(db, monkeypatch):
    firm_id = UUID(setup_firm(db))

    # Built-in defaults: document_assembly fails closed, ai_assistance on.
    assert flags_service.resolve_flag(db, "document_assembly", firm_id=firm_id) == (
        False,
        "default",
    )
    assert flags_service.resolve_flag(db, "ai_assistance", firm_id=firm_id) == (True, "default")
    # Unknown keys fail closed too.
    assert flags_service.resolve_flag(db, "not_a_flag", firm_id=firm_id) == (False, "default")

    # Environment scope beats the default.
    insert_flag(db, "ai_assistance", "environment", "", False)
    assert flags_service.resolve_flag(db, "ai_assistance", firm_id=firm_id) == (
        False,
        "environment",
    )

    # Jurisdiction scope beats environment.
    insert_flag(db, "ai_assistance", "jurisdiction", "us-test", True)
    assert flags_service.resolve_flag(db, "ai_assistance", firm_id=firm_id, pack_id="us-test") == (
        True,
        "jurisdiction",
    )
    # ... but only when a pack is in scope.
    assert flags_service.resolve_flag(db, "ai_assistance", firm_id=firm_id) == (
        False,
        "environment",
    )

    # Firm scope beats jurisdiction.
    insert_flag(db, "ai_assistance", "firm", str(firm_id), False)
    assert flags_service.resolve_flag(db, "ai_assistance", firm_id=firm_id, pack_id="us-test") == (
        False,
        "firm",
    )

    # Explicit env override beats every stored scope.
    monkeypatch.setenv("LF_FLAG_AI_ASSISTANCE", "1")
    assert flags_service.resolve_flag(db, "ai_assistance", firm_id=firm_id, pack_id="us-test") == (
        True,
        "env",
    )
    monkeypatch.setenv("LF_FLAG_AI_ASSISTANCE", "off")
    assert (
        flags_service.is_enabled(db, "ai_assistance", firm_id=firm_id, pack_id="us-test") is False
    )

    # A garbage explicit override is a loud configuration error, never silent.
    monkeypatch.setenv("LF_FLAG_AI_ASSISTANCE", "maybe")
    with pytest.raises(ValueError, match="LF_FLAG_AI_ASSISTANCE"):
        flags_service.is_enabled(db, "ai_assistance", firm_id=firm_id)


# ---------------------------------------------------------------------------
# Flag administration endpoints
# ---------------------------------------------------------------------------


def test_flag_admin_endpoints(db, api_client):
    firm_id = setup_firm(db)

    # Staff may not read or write flags.
    response = api_client.get("/v1/admin/feature-flags", headers=auth(STAFF_A))
    assert response.status_code == 403
    response = api_client.put(
        "/v1/admin/feature-flags/document_assembly",
        json={"is_enabled": True},
        headers=auth(STAFF_A),
    )
    assert response.status_code == 403

    # Unauthenticated callers are rejected.
    assert api_client.get("/v1/admin/feature-flags").status_code == 401

    # Firm admin reads effective flags: defaults apply, document_assembly off.
    response = api_client.get("/v1/admin/feature-flags", headers=auth(ADMIN_A))
    assert response.status_code == 200, response.text
    by_key = {flag["key"]: flag for flag in response.json()}
    assert set(by_key) == {"document_assembly", "ai_assistance", "funding_reminders", "ai_interviewer"}
    assert by_key["document_assembly"]["is_enabled"] is False
    assert by_key["document_assembly"]["source"] == "default"
    assert by_key["ai_assistance"]["is_enabled"] is True

    # Firm admin sets a firm-scope flag.
    response = api_client.put(
        "/v1/admin/feature-flags/document_assembly",
        json={"is_enabled": True},
        headers=auth(ADMIN_A),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == "document_assembly"
    assert body["scope"] == "firm"
    assert body["scope_ref"] == firm_id
    assert body["is_enabled"] is True

    # The effective listing now reflects the firm scope.
    response = api_client.get("/v1/admin/feature-flags", headers=auth(ADMIN_A))
    by_key = {flag["key"]: flag for flag in response.json()}
    assert by_key["document_assembly"] == {
        "key": "document_assembly",
        "is_enabled": True,
        "source": "firm",
    }

    # Unknown keys are rejected at the API boundary.
    response = api_client.put(
        "/v1/admin/feature-flags/not_a_flag",
        json={"is_enabled": True},
        headers=auth(ADMIN_A),
    )
    assert response.status_code == 422

    # The write emitted an audit event.
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM audit_events WHERE action = 'feature_flags.updated'")
        assert cur.fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# Document-assembly gate
# ---------------------------------------------------------------------------


def test_assembly_fail_closed_when_flag_off(db, api_client):
    """Default flag state (document_assembly off) blocks assembly with 409."""
    firm_id = setup_firm(db)
    household_id = create_household(db, firm_id, ADMIN_A)
    matter_id = insert_matter(
        db, firm_id, household_id, pack_id="us-test", jurisdiction_code="US-TT"
    )
    template = create_template_via_api(api_client, ADMIN_A)

    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": template["id"]},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "document_assembly_disabled"

    # generate_demo is gated too: insert a package directly, then render.
    package_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO document_packages (id, firm_id, matter_id, template_id,"
        " created_by_user_id) VALUES (%s, %s, %s, %s, %s)",
        (package_id, firm_id, matter_id, template["id"], ADMIN_A),
    )
    response = api_client.post(f"/v1/packages/{package_id}/generate-demo", headers=auth(ATTORNEY_A))
    assert response.status_code == 409
    assert response.json()["code"] == "document_assembly_disabled"


def test_assembly_allowed_when_flag_on_and_capability_true(db, api_client, monkeypatch):
    """Flag on + pack manifest capability true -> assembly proceeds."""
    monkeypatch.setenv("LF_FLAG_DOCUMENT_ASSEMBLY", "1")
    firm_id = setup_firm(db)
    household_id = create_household(db, firm_id, ADMIN_A)
    matter_id = insert_matter(
        db, firm_id, household_id, pack_id="us-test", jurisdiction_code="US-TT"
    )
    template = create_template_via_api(api_client, ADMIN_A)

    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": template["id"]},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 201, response.text
    package_id = response.json()["id"]

    response = api_client.post(f"/v1/packages/{package_id}/generate-demo", headers=auth(ATTORNEY_A))
    assert response.status_code == 201, response.text
    versions = response.json()["versions"]
    assert len(versions) == 2
    # The demo watermark behavior is intact.
    pdf_version = next(v for v in versions if v["file_format"] == "pdf")
    assert pdf_version["watermark_text"] == DEMO_TEXT
    assert pdf_version["is_demo"] is True
    download = api_client.get(pdf_version["download_url"], headers=auth(ATTORNEY_A))
    assert download.status_code == 200
    assert DEMO_TEXT in pdf_text(download.content)


def test_assembly_blocked_when_capability_false(db, api_client, monkeypatch):
    """Flag on but pack manifest capability false -> still 409 (fail closed)."""
    monkeypatch.setenv("LF_FLAG_DOCUMENT_ASSEMBLY", "1")
    # Point the loader at the real content-packs: us-va declares
    # document_assembly: false.
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(REPO_CONTENT_PACKS_DIR))
    firm_id = setup_firm(db)
    household_id = create_household(db, firm_id, ADMIN_A)
    matter_id = insert_matter(db, firm_id, household_id, pack_id="us-va", jurisdiction_code="US-VA")
    template = create_template_via_api(api_client, ADMIN_A)

    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": template["id"]},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "document_assembly_disabled"


def test_assembly_blocked_when_pack_unknown(db, api_client, monkeypatch):
    """Flag on but pack missing from the loader -> 409 (fail closed)."""
    monkeypatch.setenv("LF_FLAG_DOCUMENT_ASSEMBLY", "1")
    firm_id = setup_firm(db)
    household_id = create_household(db, firm_id, ADMIN_A)
    matter_id = insert_matter(
        db, firm_id, household_id, pack_id="no-such-pack", jurisdiction_code="US-XX"
    )
    template = create_template_via_api(api_client, ADMIN_A)

    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": template["id"]},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Pack lifecycle transitions
# ---------------------------------------------------------------------------


def _transition(api_client: TestClient, pack_ref: str, sub: str, to_status: str):
    return api_client.post(
        f"/v1/admin/packs/{pack_ref}/transition",
        json={"to_status": to_status},
        headers=auth(sub),
    )


def test_pack_transition_ordering_enforced(db, api_client):
    setup_firm(db)
    pack_uuid = create_jurisdiction_pack(db, status="draft")

    # Unknown pack -> 404.
    assert _transition(api_client, str(uuid4()), ADMIN_A, "internal_testing").status_code == 404

    # Unknown target status -> 422.
    assert _transition(api_client, pack_uuid, ADMIN_A, "bogus").status_code == 422

    # Skipping ahead is rejected; draft cannot retire directly.
    assert _transition(api_client, pack_uuid, ADMIN_A, "approved").status_code == 409
    assert _transition(api_client, pack_uuid, ADMIN_A, "retired").status_code == 409

    # Non-admin staff cannot transition at all.
    assert _transition(api_client, pack_uuid, STAFF_A, "internal_testing").status_code == 403

    # draft -> internal_testing (firm_admin).
    response = _transition(api_client, pack_uuid, ADMIN_A, "internal_testing")
    assert response.status_code == 200, response.text
    assert response.json()["from_status"] == "draft"
    assert response.json()["to_status"] == "internal_testing"

    # internal_testing -> attorney_review (firm_admin).
    response = _transition(api_client, pack_uuid, ADMIN_A, "attorney_review")
    assert response.status_code == 200, response.text

    # Backward motion is rejected.
    assert _transition(api_client, pack_uuid, ADMIN_A, "internal_testing").status_code == 409
    # Retirement is allowed from attorney_review.
    response = _transition(api_client, pack_uuid, ADMIN_A, "retired")
    assert response.status_code == 200, response.text

    # Retired is terminal.
    assert _transition(api_client, pack_uuid, ADMIN_A, "internal_testing").status_code == 409
    assert _transition(api_client, pack_uuid, ADMIN_A, "approved").status_code == 409


def test_pack_approval_requires_attorney_or_admin(db, api_client):
    setup_firm(db)
    pack_uuid = create_jurisdiction_pack(db, status="attorney_review")

    # Staff (not attorney, not admin) cannot approve.
    assert _transition(api_client, pack_uuid, STAFF_A, "approved").status_code == 403

    # An attorney who is not a firm_admin can approve.
    response = _transition(api_client, pack_uuid, ATTORNEY_A, "approved")
    assert response.status_code == 200, response.text
    assert response.json()["to_status"] == "approved"

    # The approval is attributed in the database.
    with db.cursor() as cur:
        cur.execute(
            "SELECT approved_by_user_id, approved_at, status FROM jurisdiction_packs WHERE id = %s",
            (pack_uuid,),
        )
        row = cur.fetchone()
    assert row["status"] == "approved"
    assert str(row["approved_by_user_id"]) == ATTORNEY_A
    assert row["approved_at"] is not None

    # Every transition emitted a packs.status_changed audit event.
    with db.cursor() as cur:
        cur.execute(
            "SELECT before_summary, after_summary FROM audit_events"
            " WHERE action = 'packs.status_changed' AND resource_id = %s"
            " ORDER BY created_at",
            (pack_uuid,),
        )
        events = cur.fetchall()
    assert len(events) == 1
    assert events[0]["before_summary"] == {"status": "attorney_review"}
    assert events[0]["after_summary"] == {"status": "approved"}


def test_pack_transition_by_manifest_pack_id(db, api_client):
    """The transition endpoint also accepts a manifest pack id like 'us-va'."""
    setup_firm(db)
    pack_uuid = create_jurisdiction_pack(db, status="draft", pack_id="us-zz-test")

    response = _transition(api_client, "us-zz-test", ADMIN_A, "internal_testing")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == pack_uuid
    assert response.json()["pack_id"] == "us-zz-test"
