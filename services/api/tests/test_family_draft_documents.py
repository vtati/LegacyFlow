"""Family self-serve draft documents (2026-10-26 plan, Tasks 1.4/1.7).

Exercises the family draft-set surface:
``GET/POST /v1/matters/{id}/draft-documents/{state,generate,submit}``
plus ``compute_source_fingerprint`` staleness semantics (D4).

Runs against pgserver with the full migration sweep (including the
``source_fingerprint`` column migration) and a scratch copy of the real
us-va packs — the v2 manifest already declares ``document_assembly``.
The ``document_assembly`` flag is controlled per firm via firm-scope
rows (no env override), exactly like production (D7).

All fixture data is synthetic.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from pypdf import PdfReader

from app.domains.documents import service as documents_service
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities
# ---------------------------------------------------------------------------

ADMIN = "c1111111-1111-1111-1111-111111111111"  # firm_admin (staff path)
STAFF = "c2222222-2222-2222-2222-222222222222"  # firm staff
CLIENT = "c3333333-3333-3333-3333-333333333333"  # household primary_client
COLLAB = "c4444444-4444-4444-4444-444444444444"  # household collaborator
DEPENDENT = "c5555555-5555-5555-5555-555555555555"  # household dependent (read-only)
DUAL = "c6666666-6666-6666-6666-666666666666"  # firm_admin AND household primary_client
LAWYER = "c7777777-7777-7777-7777-777777777777"  # routable dummy VA attorney
OUTSIDER = "c8888888-8888-8888-8888-888888888888"  # no access at all

USERS = {
    ADMIN: "admin@example.test",
    STAFF: "staff@example.test",
    CLIENT: "client@example.test",
    COLLAB: "collab@example.test",
    DEPENDENT: "dependent@example.test",
    DUAL: "dual@example.test",
    LAWYER: "lawyer@example.test",
    OUTSIDER: "outsider@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

DEMO_TEXT = "DRAFT — NOT LEGALLY VALID"

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Start pgserver once; apply auth stub + migrations + jurisdiction seed."""
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-family-drafts")))
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
    """TestClient with pgserver as DATABASE_URL and the real packs copied in."""
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


def insert_matter(db, firm_id: str, household_id: str, status: str = "INTAKE_IN_PROGRESS") -> str:
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "2.0.0"}
    )
    _exec(
        db,
        "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
        " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
        " completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', %s, %s, %s::jsonb, 0)",
        (
            matter_id,
            firm_id,
            household_id,
            US_VA_JURISDICTION_ID,
            US_VA_PACK_ID,
            status,
            "Sharma Estate Plan",
            snapshot,
        ),
    )
    return matter_id


def setup_world(db, *, with_attorney: bool = True):
    """Firm + household + v2 matter; optionally the routable dummy attorney."""
    for sub in USERS:
        create_user(db, sub)
    firm_id = create_firm(db)
    add_firm_member(db, firm_id, ADMIN, "firm_admin")
    add_firm_member(db, firm_id, STAFF, "staff")
    add_firm_member(db, firm_id, DUAL, "firm_admin")
    household_id = create_household(db, firm_id, ADMIN)
    add_household_member(db, household_id, CLIENT, "primary_client")
    add_household_member(db, household_id, COLLAB, "collaborator")
    add_household_member(db, household_id, DEPENDENT, "dependent")
    add_household_member(db, household_id, DUAL, "primary_client")
    matter_id = insert_matter(db, firm_id, household_id)
    if with_attorney:
        enroll_attorney(db, firm_id)
    return firm_id, household_id, matter_id


def enroll_attorney(db, firm_id: str) -> None:
    """Approved VA application + active attorney membership for LAWYER."""
    _exec(
        db,
        "INSERT INTO attorney_applications"
        " (firm_id, user_id, full_name, email, bar_number, jurisdictions, status)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'approved')",
        (
            firm_id,
            LAWYER,
            "E2E Attorney Test",
            USERS[LAWYER],
            "VA-99999-E2E",
            ["US-VA"],
        ),
    )
    add_firm_member(db, firm_id, LAWYER, "attorney")


def set_assembly_flag(db, firm_id: str, enabled: bool) -> None:
    _exec(
        db,
        "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled, created_by_user_id)"
        " VALUES ('document_assembly', 'firm', %s, %s, %s)"
        " ON CONFLICT (key, scope, scope_ref) DO UPDATE SET is_enabled = EXCLUDED.is_enabled",
        (firm_id, enabled, ADMIN),
    )
    from app.domains.feature_flags import service as flags_service

    flags_service._flag_cache.clear()


def insert_template(db, firm_id: str, template_type: str, *, pack_id: str | None = "us-va",
                    created_at: str | None = None, body: str | None = None) -> str:
    template_id = str(uuid4())
    content = json.dumps({"body": body or TEMPLATE_BODIES[template_type]})
    if created_at is None:
        _exec(
            db,
            "INSERT INTO document_templates"
            " (id, firm_id, name, template_type, pack_id, version, content, created_by_user_id)"
            " VALUES (%s, %s, %s, %s, %s, '1.0.0', %s::jsonb, %s)",
            (template_id, firm_id, TEMPLATE_NAMES[template_type], template_type,
             pack_id, content, ADMIN),
        )
    else:
        _exec(
            db,
            "INSERT INTO document_templates"
            " (id, firm_id, name, template_type, pack_id, version, content, created_by_user_id,"
            "  created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, '1.0.0', %s::jsonb, %s, %s, %s)",
            (template_id, firm_id, TEMPLATE_NAMES[template_type], template_type,
             pack_id, content, ADMIN, created_at, created_at),
        )
    return template_id


def seed_all_templates(db, firm_id: str) -> None:
    for template_type in TEMPLATE_BODIES:
        insert_template(db, firm_id, template_type)


def _all_pack_questions() -> list[dict]:
    questions: list[dict] = []
    for path in sorted(V2_QUESTION_SETS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for section in data.get("sections", []):
            questions.extend(section.get("questions", []))
    return questions


def answer_everything(db, matter_id: str, profile: dict, *, skip_keys: frozenset = frozenset()) -> None:
    """Answer every v2 pack question; gate keys take profile values."""
    for question in _all_pack_questions():
        key = str(question["key"])
        if key in skip_keys:
            continue
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


def set_answer(db, matter_id: str, key: str, value) -> None:
    _exec(
        db,
        "INSERT INTO intake_answers (matter_id, question_key, answer)"
        " VALUES (%s, %s, %s::jsonb)"
        " ON CONFLICT (matter_id, question_key) DO UPDATE SET answer = EXCLUDED.answer",
        (matter_id, key, json.dumps(value)),
    )


def insert_person(db, household_id: str, full_name: str, kind: str = "adult") -> str:
    person_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO persons (id, household_id, full_name, kind) VALUES (%s, %s, %s, %s)",
        (person_id, household_id, full_name, kind),
    )
    return person_id


def token(sub: str) -> str:
    return mint_token(sub=sub, email=USERS.get(sub, f"{sub}@example.test"))


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token(sub)}


def state(api_client: TestClient, sub: str, matter_id: str):
    return api_client.get(f"/v1/matters/{matter_id}/draft-documents/state", headers=auth(sub))


def generate(api_client: TestClient, sub: str, matter_id: str):
    return api_client.post(f"/v1/matters/{matter_id}/draft-documents/generate", headers=auth(sub))


def submit(api_client: TestClient, sub: str, matter_id: str):
    return api_client.post(
        f"/v1/matters/{matter_id}/draft-documents/submit", headers=auth(sub)
    )


def ready_matter(db, api_client, *, profile=SHARMA_PROFILE, with_attorney: bool = True):
    """World + flag on + all templates + complete answers. Returns ids."""
    firm_id, household_id, matter_id = setup_world(db, with_attorney=with_attorney)
    set_assembly_flag(db, firm_id, True)
    seed_all_templates(db, firm_id)
    answer_everything(db, matter_id, profile)
    return firm_id, household_id, matter_id


# ---------------------------------------------------------------------------
# Authz matrix (D3: dual-path write scope)
# ---------------------------------------------------------------------------


def test_state_authz_matrix(db, api_client):
    _firm, _household, matter_id = setup_world(db)
    # household primary_client + collaborator: OK
    assert state(api_client, CLIENT, matter_id).status_code == 200
    assert state(api_client, COLLAB, matter_id).status_code == 200
    # firm staff: OK
    assert state(api_client, STAFF, matter_id).status_code == 200
    # dual-hatted firm_admin + household primary_client: OK (pins D3 —
    # a household-only check resolves firm-first and would still pass,
    # but a firm-only check would 403 the family; both paths must work)
    assert state(api_client, DUAL, matter_id).status_code == 200
    # dependent is read-only: 403
    response = state(api_client, DEPENDENT, matter_id)
    assert response.status_code == 403
    # no access at all: 404 (existence-oracle rule)
    response = state(api_client, OUTSIDER, matter_id)
    assert response.status_code == 404


def test_generate_and_submit_reject_dependent_and_outsider(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, DEPENDENT, matter_id).status_code == 403
    assert generate(api_client, OUTSIDER, matter_id).status_code == 404
    assert submit(api_client, DEPENDENT, matter_id).status_code == 403
    assert submit(api_client, OUTSIDER, matter_id).status_code == 404


# ---------------------------------------------------------------------------
# State + enablement
# ---------------------------------------------------------------------------


def test_state_flag_off_reports_enabled_false_without_error(db, api_client):
    firm_id, _household, matter_id = setup_world(db)
    set_assembly_flag(db, firm_id, False)
    response = state(api_client, CLIENT, matter_id)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["can_generate"] is False
    assert body["can_submit"] is False
    # all five types are listed with their signals even when disabled
    assert [d["template_type"] for d in body["documents"]] == [
        "will",
        "poa",
        "advance_directive",
        "guardianship",
        "trust",
    ]


def test_state_flag_never_set_defaults_disabled(db, api_client):
    _firm, _household, matter_id = setup_world(db)
    body = state(api_client, CLIENT, matter_id).json()
    assert body["enabled"] is False


def test_generate_with_flag_off_is_409_document_assembly_disabled(db, api_client):
    firm_id, _household, matter_id = setup_world(db)
    set_assembly_flag(db, firm_id, False)
    seed_all_templates(db, firm_id)
    answer_everything(db, matter_id, SHARMA_PROFILE)
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "document_assembly_disabled"


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


def test_generate_incomplete_matter_is_409_with_missing_labels(db, api_client):
    firm_id, _household, matter_id = setup_world(db)
    set_assembly_flag(db, firm_id, True)
    seed_all_templates(db, firm_id)
    # no answers at all -> incomplete, and the detail names actual
    # missing question labels from the v2 pack (not just a code).
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "intake_incomplete"
    assert "What is your full legal name?" in body["detail"]
    assert "What best describes your household?" in body["detail"]


def test_generate_sharma_complete_creates_five_watermarked_packages(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["packages"]) == 5
    assert all(p["status"] == "demo_released" for p in body["packages"])
    assert len(body["versions"]) == 10  # DOCX + PDF per package
    assert all(v["watermark_text"] == DEMO_TEXT for v in body["versions"])
    assert all(v["is_demo"] is True for v in body["versions"])
    formats = sorted(v["file_format"] for v in body["versions"])
    assert formats == ["docx"] * 5 + ["pdf"] * 5
    assert body["fingerprint"]
    # fingerprints stamped on every package row
    row = _one(
        db,
        "SELECT count(*) AS n FROM document_packages"
        " WHERE matter_id = %s AND source_fingerprint = %s",
        (matter_id, body["fingerprint"]),
    )
    assert row["n"] == 5
    # state agrees: everything generated, fresh, submittable
    body = state(api_client, CLIENT, matter_id).json()
    assert body["enabled"] is True
    assert body["applicable"]["complete"] is True
    assert body["applicable"]["required_total"] == body["applicable"]["required_answered"]
    assert body["applicable"]["required_total"] > 0
    included = [d for d in body["documents"] if d["included"]]
    assert [d["template_type"] for d in included] == [
        "will",
        "poa",
        "advance_directive",
        "guardianship",
        "trust",
    ]
    assert all(d["generated"] and not d["stale"] for d in included)
    assert body["can_submit"] is True
    assert body["submit_blockers"] == []


def test_generate_pruned_profile_creates_will_only(db, api_client):
    pruned = {
        "has_minor_children": False,
        "wants_revocable_trust": False,
        "wants_poa_document": False,
        "wants_healthcare_directive": False,
        "has_specific_bequests": False,
    }
    _firm, _household, matter_id = ready_matter(db, api_client, profile=pruned)
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 201, response.text
    assert len(response.json()["packages"]) == 1
    body = state(api_client, CLIENT, matter_id).json()
    # pruned required questions never block: complete on the applicable count
    assert body["applicable"]["complete"] is True
    excluded = {d["template_type"]: d for d in body["documents"] if not d["included"]}
    assert set(excluded) == {"poa", "advance_directive", "guardianship", "trust"}
    assert excluded["trust"]["signal"] == "wants_revocable_trust != true"
    assert excluded["guardianship"]["signal"] == "has_minor_children != true"


def test_generate_missing_template_fails_closed_before_rendering(db, api_client):
    firm_id, _household, matter_id = setup_world(db)
    set_assembly_flag(db, firm_id, True)
    insert_template(db, firm_id, "will")  # only the will template exists
    answer_everything(db, matter_id, SHARMA_PROFILE)
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "draft_template_missing"
    assert set(body["missing_types"]) == {"poa", "advance_directive", "guardianship", "trust"}
    # nothing rendered, no packages created
    row = _one(db, "SELECT count(*) AS n FROM document_packages WHERE matter_id = %s", (matter_id,))
    assert row["n"] == 0


def test_generate_prefers_the_matters_pack_template(db, api_client):
    firm_id, _household, matter_id = setup_world(db)
    set_assembly_flag(db, firm_id, True)
    seed_all_templates(db, firm_id)
    # A NEWER will template from another pack must not win over the us-va one.
    insert_template(
        db, firm_id, "will", pack_id="other-pack",
        created_at="2999-01-01T00:00:00Z", body="OTHER PACK WILL {{household_name}}",
    )
    answer_everything(db, matter_id, SHARMA_PROFILE)
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 201, response.text
    will_package = next(
        p for p in response.json()["packages"]
        if _one(db, "SELECT template_type FROM document_templates WHERE id = %s",
                (p["template_id"],))["template_type"] == "will"
    )
    template_row = _one(
        db, "SELECT pack_id FROM document_templates WHERE id = %s", (will_package["template_id"],)
    )
    assert template_row["pack_id"] == "us-va"


# ---------------------------------------------------------------------------
# Production release: finals carry no draft marking in any carrier (R3)
# ---------------------------------------------------------------------------


def _docx_parts(content: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        }


def _pdf_text(content: bytes) -> str:
    return "\n".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages
    )


def _download(api_client: TestClient, sub: str, version: dict) -> bytes:
    response = api_client.get(version["download_url"], headers=auth(sub))
    assert response.status_code == 200, response.text
    return response.content


def _assert_marked(api_client: TestClient, version: dict) -> None:
    content = _download(api_client, CLIENT, version)
    if version["file_format"] == "docx":
        parts = _docx_parts(content)
        assert any(DEMO_TEXT in xml for xml in parts.values())
        footers = [xml for name, xml in parts.items() if "footer" in name]
        assert any(DEMO_TEXT in xml for xml in footers)
        assert DEMO_TEXT in parts["word/document.xml"]
    else:
        assert DEMO_TEXT in _pdf_text(content)


def _assert_clean(api_client: TestClient, version: dict) -> None:
    content = _download(api_client, LAWYER, version)
    if version["file_format"] == "docx":
        parts = _docx_parts(content)
        assert parts
        for name, xml in parts.items():
            assert "DRAFT" not in xml, name
            assert "NOT LEGALLY VALID" not in xml, name
    else:
        text = _pdf_text(content)
        assert "DRAFT" not in text
        assert "NOT LEGALLY VALID" not in text


def test_release_production_finals_clean_for_all_five_templates(db, api_client):
    firm_id, _household, matter_id = ready_matter(db, api_client)
    generated = generate(api_client, CLIENT, matter_id)
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert len(body["packages"]) == 5
    assert len(body["versions"]) == 10
    # Drafts are marked in every carrier.
    for version in body["versions"]:
        _assert_marked(api_client, version)

    # Attorney approves the document_release gate (direct gate row: the
    # gate-approval API flow is covered by the review tests).
    _exec(
        db,
        "INSERT INTO approval_gates"
        " (firm_id, matter_id, gate_type, status, decided_by_user_id, decided_at)"
        " VALUES (%s, %s, 'document_release', 'approved', %s, now())",
        (firm_id, matter_id, LAWYER),
    )

    released_types = set()
    for package in body["packages"]:
        template_row = _one(
            db,
            "SELECT template_type FROM document_templates WHERE id = %s",
            (package["template_id"],),
        )
        released_types.add(template_row["template_type"])
        response = api_client.post(
            f"/v1/packages/{package['id']}/release-production", headers=auth(LAWYER)
        )
        assert response.status_code == 200, response.text
        released = response.json()
        assert released["package"]["status"] == "released"
        assert len(released["versions"]) == 2
        assert {v["file_format"] for v in released["versions"]} == {"docx", "pdf"}
        for version in released["versions"]:
            assert version["is_demo"] is False
            assert version["watermark_text"] is None
            _assert_clean(api_client, version)
    assert released_types == {"will", "poa", "advance_directive", "guardianship", "trust"}


# ---------------------------------------------------------------------------
# Staleness (D4)
# ---------------------------------------------------------------------------


def test_edit_after_generate_marks_stale_and_blocks_submit(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    set_answer(db, matter_id, "testator_full_name", "Priya Sharma-Edited")
    body = state(api_client, CLIENT, matter_id).json()
    assert all(d["stale"] for d in body["documents"] if d["included"])
    assert body["can_submit"] is False
    assert body["submit_blockers"] == ["drafts_stale"]
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "drafts_stale"
    # regenerate -> fresh again
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    body = state(api_client, CLIENT, matter_id).json()
    assert body["can_submit"] is True
    assert body["submit_blockers"] == []


def test_submit_without_generate_is_409_drafts_missing(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "drafts_missing"
    body = state(api_client, CLIENT, matter_id).json()
    assert body["submit_blockers"] == ["drafts_missing"]


# ---------------------------------------------------------------------------
# Submit (D6)
# ---------------------------------------------------------------------------


def test_submit_routes_to_attorney_and_freezes_packet(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    set_answer(db, matter_id, "testator_full_name", "Priya Kapoor")
    assert generate(api_client, CLIENT, matter_id).status_code == 201

    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matter_status"] == "ATTORNEY_REVIEW"
    assert body["packet_version"] == 1
    assert body["assigned_attorney"]["user_id"] == LAWYER
    assert body["assigned_attorney"]["full_name"] == "E2E Attorney Test"

    matter = _one(db, "SELECT status, assigned_attorney_id FROM matters WHERE id = %s", (matter_id,))
    assert matter["status"] == "ATTORNEY_REVIEW"
    assert str(matter["assigned_attorney_id"]) == LAWYER

    packet = _one(db, "SELECT snapshot FROM review_packets WHERE matter_id = %s", (matter_id,))
    snapshot = packet["snapshot"]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    # the frozen snapshot carries the routed attorney + the corrected answer
    assert snapshot["matter"]["assigned_attorney_id"] == LAWYER
    assert snapshot["intake_answers"]["testator_full_name"] == "Priya Kapoor"
    assert snapshot["notes"] == "Family self-serve submission"

    body = state(api_client, CLIENT, matter_id).json()
    assert body["matter_status"] == "ATTORNEY_REVIEW"
    assert body["assigned_attorney"]["user_id"] == LAWYER
    assert body["latest_packet"]["version"] == 1
    assert body["can_submit"] is False
    assert body["submit_blockers"] == ["already_submitted"]


def test_submit_twice_is_409_already_submitted(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    assert submit(api_client, CLIENT, matter_id).status_code == 200
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "already_submitted"


def test_submit_without_routable_attorney_rolls_back(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client, with_attorney=False)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "no_attorney_available"
    # rollback proven: the intake submit inside the orchestration did not stick
    matter = _one(db, "SELECT status FROM matters WHERE id = %s", (matter_id,))
    assert matter["status"] == "INTAKE_IN_PROGRESS"
    row = _one(db, "SELECT count(*) AS n FROM review_packets WHERE matter_id = %s", (matter_id,))
    assert row["n"] == 0


def test_submit_after_legacy_intake_submit_completes_the_chain(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    # family uses the pre-existing submit endpoint first
    legacy = api_client.post(f"/v1/matters/{matter_id}/submit", headers=auth(CLIENT))
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["status"] == "INTAKE_SUBMITTED"
    # the draft submit then skips the intake hop and finishes the chain
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 200, response.text
    assert response.json()["matter_status"] == "ATTORNEY_REVIEW"


def test_dual_hat_account_can_run_the_whole_flow(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, DUAL, matter_id).status_code == 201
    response = submit(api_client, DUAL, matter_id)
    assert response.status_code == 200, response.text


def test_state_cross_firm_staff_is_404(db, api_client):
    _firm, _household, matter_id = ready_matter(db, api_client)
    # a firm_admin of a DIFFERENT firm has no path to this matter
    other_admin = str(uuid4())
    create_user(db, other_admin)
    other_firm = create_firm(db, "Other Firm")
    add_firm_member(db, other_firm, other_admin, "firm_admin")
    other_headers = {"Authorization": "Bearer " + mint_token(sub=other_admin, email="other@example.test")}
    response = api_client.get(
        f"/v1/matters/{matter_id}/draft-documents/state", headers=other_headers
    )
    assert response.status_code == 404


def test_submit_with_flag_revoked_after_generate_is_409(db, api_client):
    firm_id, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    set_assembly_flag(db, firm_id, False)
    # state agrees the flow is off...
    body = state(api_client, CLIENT, matter_id).json()
    assert body["enabled"] is False
    assert body["can_submit"] is False
    # ...and the endpoint itself refuses (no live path behind the state)
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "document_assembly_disabled"
    matter = _one(db, "SELECT status FROM matters WHERE id = %s", (matter_id,))
    assert matter["status"] == "INTAKE_IN_PROGRESS"


def test_newer_template_after_generate_makes_drafts_unsubmittable(db, api_client):
    firm_id, _household, matter_id = ready_matter(db, api_client)
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    # the firm publishes a newer will template (same pack): the drafts
    # were rendered from the superseded template and must not submit.
    insert_template(db, firm_id, "will", created_at="2999-01-01T00:00:00Z")
    body = state(api_client, CLIENT, matter_id).json()
    will = next(d for d in body["documents"] if d["template_type"] == "will")
    assert will["generated"] is True
    assert will["stale"] is True
    assert body["can_submit"] is False
    assert body["submit_blockers"] == ["drafts_stale"]
    response = submit(api_client, CLIENT, matter_id)
    assert response.status_code == 409
    assert response.json()["code"] == "drafts_missing"
    # regenerate binds to the new template; submit then succeeds
    assert generate(api_client, CLIENT, matter_id).status_code == 201
    body = state(api_client, CLIENT, matter_id).json()
    will = next(d for d in body["documents"] if d["template_type"] == "will")
    assert will["stale"] is False
    assert submit(api_client, CLIENT, matter_id).status_code == 200


# ---------------------------------------------------------------------------
# Fingerprint semantics (service level, D4)
# ---------------------------------------------------------------------------


def _matter_row(db, matter_id: str) -> dict:
    row = _one(db, "SELECT * FROM matters WHERE id = %s", (matter_id,))
    assert row is not None
    return row


def test_fingerprint_stable_and_sensitive(db):
    _firm, household_id, matter_id = setup_world(db)
    answer_everything(db, matter_id, SHARMA_PROFILE)
    person_id = insert_person(db, household_id, "Aarav Sharma", "minor")
    _exec(
        db,
        "INSERT INTO nominations (matter_id, nomination_type, person_id, priority, status)"
        " VALUES (%s, 'guardian', %s, 1, 'proposed')",
        (matter_id, person_id),
    )
    matter = _matter_row(db, matter_id)
    first = documents_service.compute_source_fingerprint(db, matter)
    # stable across calls with unchanged data
    assert documents_service.compute_source_fingerprint(db, matter) == first

    # flips on an answer edit
    set_answer(db, matter_id, "testator_full_name", "Priya Sharma-Edited")
    after_answer = documents_service.compute_source_fingerprint(db, matter)
    assert after_answer != first

    # flips on a person rename
    _exec(db, "UPDATE persons SET full_name = %s WHERE id = %s", ("Aarav Sharma-Jr", person_id))
    after_person = documents_service.compute_source_fingerprint(db, matter)
    assert after_person != after_answer

    # flips on a nomination change
    _exec(
        db,
        "UPDATE nominations SET priority = 2 WHERE matter_id = %s AND person_id = %s",
        (matter_id, person_id),
    )
    after_nomination = documents_service.compute_source_fingerprint(db, matter)
    assert after_nomination != after_person

    # flips on a nomination STATUS change (D4 names status an input)
    _exec(
        db,
        "UPDATE nominations SET status = 'confirmed'"
        " WHERE matter_id = %s AND person_id = %s",
        (matter_id, person_id),
    )
    after_status = documents_service.compute_source_fingerprint(db, matter)
    assert after_status != after_nomination

    # flips on a person date-of-birth change
    _exec(db, "UPDATE persons SET date_of_birth = %s WHERE id = %s", ("2016-05-04", person_id))
    after_dob = documents_service.compute_source_fingerprint(db, matter)
    assert after_dob != after_status

    # flips on a household rename and on a matter-title change
    _exec(
        db,
        "UPDATE households SET display_name = 'Sharma-Kapoor Household' WHERE id = %s",
        (matter["household_id"],),
    )
    after_household = documents_service.compute_source_fingerprint(db, matter)
    assert after_household != after_dob
    _exec(db, "UPDATE matters SET title = 'Sharma Family Plan' WHERE id = %s", (matter_id,))
    renamed = _matter_row(db, matter_id)
    assert documents_service.compute_source_fingerprint(db, renamed) != after_household


def test_fingerprint_ignores_the_volatile_today(db, monkeypatch):
    """The render context injects a volatile ``today``; the fingerprint
    must NOT depend on it (D4) — otherwise drafts would go stale daily.
    Patching the module's datetime to a different year must not move the
    fingerprint."""
    _firm, _household, matter_id = setup_world(db)
    answer_everything(db, matter_id, SHARMA_PROFILE)
    matter = _matter_row(db, matter_id)
    first = documents_service.compute_source_fingerprint(db, matter)

    class _NextYearDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 1, 1, tzinfo=tz or UTC)

    monkeypatch.setattr(documents_service, "datetime", _NextYearDateTime)
    assert documents_service.compute_source_fingerprint(db, matter) == first


def test_generate_survives_attorney_review_recorder_failure(db, api_client, monkeypatch):
    """Plan R3 / review H2: snapshot recording is best-effort — a
    recorder failure must never fail the family's generate."""
    from app.domains.attorney_review import service as review_service

    _firm, _household, matter_id = ready_matter(db, api_client)

    def boom(*args, **kwargs):
        raise RuntimeError("recorder exploded")

    monkeypatch.setattr(review_service, "record_render_snapshot", boom)
    response = generate(api_client, CLIENT, matter_id)
    assert response.status_code == 201, response.text
    assert len(response.json()["packages"]) == 5
