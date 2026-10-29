"""Slice 4 — documents domain API tests.

Exercises the documents API surface:
``app/domains/documents/{repository,service,schemas,router}.py``.

Demo files are synthetic and ALWAYS carry the visible banner
``DEMO ONLY — NOT LEGALLY VALID``; no test ever presents them as legally
valid. ``POST /v1/packages/{id}/release-production`` is a real gated
operation: without an approved ``document_release`` gate it fails closed
with HTTP 409.

These tests run against the real Track A4 migration
(``supabase/migrations/20260927030000_files_documents_slice4.sql``),
which the ``pg_uri`` fixture applies from the migrations directory.
Attestations live inside ``signing_checklists.checklist`` jsonb (there is
no ``signing_attestations`` table).

All fixture data is synthetic. Nothing here touches main.py or
api/v1/router.py wiring.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from pypdf import PdfReader

from app.domains.documents import service as documents_service
from app.domains.documents.service import (
    render_demo_docx,
    render_demo_pdf,
    render_template_content,
)
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, STORAGE_BUCKETS_STUB, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------

ATTORNEY_A = "a1111111-1111-1111-1111-111111111111"
STAFF_A = "a2222222-2222-2222-2222-222222222222"
CLIENT_A = "a3333333-3333-3333-3333-333333333333"
COLLAB_A = "a4444444-4444-4444-4444-444444444444"
DEPENDENT_A = "a5555555-5555-5555-5555-555555555555"
AUDITOR_A = "a6666666-6666-6666-6666-666666666666"
TRUSTED_A = "a7777777-7777-7777-7777-777777777777"
ADMIN_B = "b1111111-1111-1111-1111-111111111111"

USERS = {
    ADMIN_B: "admin.b@example.test",
    ATTORNEY_A: "attorney.a@example.test",
    STAFF_A: "staff.a@example.test",
    AUDITOR_A: "auditor.a@example.test",
    CLIENT_A: "client.a@example.test",
    COLLAB_A: "collab.a@example.test",
    DEPENDENT_A: "dependent.a@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

DEMO_TEXT = "DRAFT — NOT LEGALLY VALID"

# ---------------------------------------------------------------------------

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders, amendment_requests,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " asset_flags, assets, liabilities, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
-- raw_user_meta_data/raw_app_meta_data: the handle_new_user trigger reads
-- them, mirroring the real Supabase auth.users shape.
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
-- The storage-buckets migration inserts into storage.buckets; stub the
-- schema/table so the migration sweep can run on a bare pgserver.
""" + STORAGE_BUCKETS_STUB

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
REPO_CONTENT_PACKS_DIR = Path(__file__).resolve().parents[3] / "content-packs"


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
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-slice4-documents")))
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
    """TestClient with pgserver as DATABASE_URL, test JWT override, and an
    isolated document storage dir."""
    storage_dir = tmp_path / "documents"
    storage_dir.mkdir()
    monkeypatch.setenv("DOCUMENT_STORAGE_DIR", str(storage_dir))
    # Document-assembly gate: the feature flag fails closed by default, so
    # these document tests enable it explicitly and point the pack loader at
    # a scratch copy of the us-va pack whose manifest declares the
    # document_assembly capability.
    monkeypatch.setenv("LF_FLAG_DOCUMENT_ASSEMBLY", "1")
    scratch_packs = tmp_path / "content-packs"
    shutil.copytree(REPO_CONTENT_PACKS_DIR / "us-va" / "v0", scratch_packs / "us-va" / "v0")
    manifest_path = scratch_packs / "us-va" / "v0" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["capabilities"]["document_assembly"] = True
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setenv("CONTENT_PACKS_DIR", str(scratch_packs))
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Rate limiting is covered by its own dedicated tests; leave it off here.
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()  # force settings reload for the new env
    from app.domains.documents.router import router as documents_router
    from app.main import create_app

    app = create_app()
    app.include_router(documents_router, prefix="/v1")
    return TestClient(app)


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
    # The on_auth_user_created trigger already provisioned the profiles row
    # (the pattern test_interview.py relies on); upsert the display name.
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


def insert_matter(db, firm_id: str, household_id: str) -> str:
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {
            "pack_id": "us-va",
            "jurisdiction_code": "US-VA",
            "semantic_version": "0.1.0",
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


def insert_asset(
    db, firm_id: str, household_id: str, created_by: str, asset_type: str, label: str
) -> str:
    asset_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO assets (id, firm_id, household_id, asset_type, label,"
        " beneficiary_designation, created_by_user_id)"
        " VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s)",
        (asset_id, firm_id, household_id, asset_type, label, created_by),
    )
    return asset_id


def setup_firm_household_matter(db):
    """Standard synthetic tree: admin firm, attorney+staff, client household, matter."""
    for sub in USERS:
        create_user(db, sub)
    firm_id = create_firm(db)
    add_firm_member(db, firm_id, ADMIN_B, "firm_admin")
    add_firm_member(db, firm_id, ATTORNEY_A, "attorney")
    add_firm_member(db, firm_id, STAFF_A, "staff")
    add_firm_member(db, firm_id, AUDITOR_A, "auditor")
    household_id = create_household(db, firm_id, ADMIN_B)
    add_household_member(db, household_id, CLIENT_A, "primary_client")
    add_household_member(db, household_id, COLLAB_A, "collaborator")
    add_household_member(db, household_id, DEPENDENT_A, "dependent")
    matter_id = insert_matter(db, firm_id, household_id)
    return firm_id, household_id, matter_id


def token(sub: str) -> str:
    return mint_token(sub=sub, email=USERS.get(sub, f"{sub}@example.test"))


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub)}"}


WILL_TEMPLATE = {
    "name": "Synthetic Will Template",
    "template_type": "will",
    "pack_id": "us-va",
    "version": "1.0.0",
    "content": (
        "LAST WILL AND TESTAMENT of {{household_name}}\n\n"
        "I, {{household_name}}, being of sound mind, declare this my will.\n"
        "Signed on {{today}} for the {{jurisdiction_code}} matter {{matter_title}}.\n"
    ),
}

INSTRUCTION_TEMPLATE = {
    "name": "Synthetic Family Letter Template",
    "template_type": "instruction_letter",
    "pack_id": "us-va",
    "version": "1.0.0",
    "content": "Dear family,\n\n{{letter_text}}\n",
}


def create_template_via_api(api_client: TestClient, sub: str, payload: dict) -> dict:
    response = api_client.post("/v1/document-templates", json=payload, headers=auth(sub))
    assert response.status_code == 201, response.text
    return response.json()


def create_package_via_api(
    api_client: TestClient, sub: str, matter_id: str, template_id: str
) -> dict:
    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": template_id},
        headers=auth(sub),
    )
    assert response.status_code == 201, response.text
    return response.json()


def pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


def docx_texts(content: bytes) -> tuple[str, str]:
    """Return (body text, header XML) from a DOCX byte string."""
    import zipfile

    from docx import Document as ReadDocx

    doc = ReadDocx(io.BytesIO(content))
    body = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        header_xml = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/header")
        )
    return body, header_xml


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_template_create_list_get_firm_admin_only(db, api_client):
    setup_firm_household_matter(db)

    # firm_admin can create
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)
    assert template["name"] == WILL_TEMPLATE["name"]
    assert template["template_type"] == "will"

    # staff (non-admin) cannot create
    response = api_client.post("/v1/document-templates", json=WILL_TEMPLATE, headers=auth(STAFF_A))
    assert response.status_code == 403

    # household client cannot create
    response = api_client.post("/v1/document-templates", json=WILL_TEMPLATE, headers=auth(CLIENT_A))
    assert response.status_code == 403

    # staff can list and get
    response = api_client.get("/v1/document-templates", headers=auth(STAFF_A))
    assert response.status_code == 200
    assert len(response.json()) == 1
    response = api_client.get(f"/v1/document-templates/{template['id']}", headers=auth(STAFF_A))
    assert response.status_code == 200
    assert response.json()["id"] == template["id"]


def test_template_is_firm_scoped(db, api_client):
    _firm_a, _household_a, _matter_a = setup_firm_household_matter(db)
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)

    # a second firm is created with its own admin
    other_admin = str(uuid4())
    create_user(db, other_admin)
    other_firm = create_firm(db, "Other Synthetic Firm")
    add_firm_member(db, other_firm, other_admin, "firm_admin")

    other_token = token(other_admin)
    other_headers = {"Authorization": f"Bearer {other_token}"}
    response = api_client.get(f"/v1/document-templates/{template['id']}", headers=other_headers)
    assert response.status_code == 404  # existence-oracle: cross-firm reads as missing
    response = api_client.get("/v1/document-templates", headers=other_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_template_delete_firm_admin_only(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)

    # staff (non-admin) cannot delete
    response = api_client.delete(f"/v1/document-templates/{template['id']}", headers=auth(STAFF_A))
    assert response.status_code == 403

    # other firm's admin gets 404 (existence-oracle), not 403
    other_admin = str(uuid4())
    create_user(db, other_admin)
    other_firm = create_firm(db, "Other Synthetic Firm")
    add_firm_member(db, other_firm, other_admin, "firm_admin")
    other_headers = {"Authorization": f"Bearer {token(other_admin)}"}
    response = api_client.delete(f"/v1/document-templates/{template['id']}", headers=other_headers)
    assert response.status_code == 404

    # firm_admin can delete; template is gone afterwards
    response = api_client.delete(f"/v1/document-templates/{template['id']}", headers=auth(ADMIN_B))
    assert response.status_code == 204
    response = api_client.get(f"/v1/document-templates/{template['id']}", headers=auth(ADMIN_B))
    assert response.status_code == 404


def test_template_delete_blocked_when_packages_reference_it(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)
    create_package_via_api(api_client, ADMIN_B, matter_id, template["id"])

    response = api_client.delete(f"/v1/document-templates/{template['id']}", headers=auth(ADMIN_B))
    assert response.status_code == 409

    # template still readable
    response = api_client.get(f"/v1/document-templates/{template['id']}", headers=auth(ADMIN_B))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Packages + demo generation
# ---------------------------------------------------------------------------


def test_package_lifecycle_and_demo_generation(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    # f8786cf added the intake-completeness gate to demo generation
    # (409 intake_incomplete while applicable required answers are
    # missing); seed every required v0 answer so the gate passes, the
    # same setup test_generate_demo_blanks_unknown_tokens uses.
    for key, value in {
        "marital_status": "single",
        "primary_residence_state": "VA",
        "has_minor_children": False,
        "has_child_with_special_needs": False,
        "has_prior_marriage_or_children_outside_current_relationship": False,
        "owns_real_estate_outside_primary_jurisdiction": False,
        "owns_or_controls_business_interest": False,
        "expects_family_conflict": False,
        "primary_guardian_person": "Synthetic Guardian",
        "asset_categories_present": ["bank_account"],
        "has_debts": False,
    }.items():
        _exec(
            db,
            "INSERT INTO intake_answers (matter_id, question_key, answer)"
            " VALUES (%s, %s, %s::jsonb)",
            (matter_id, key, json.dumps(value)),
        )
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)

    # household client cannot create a package
    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": template["id"]},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 403

    # firm staff can create
    package = create_package_via_api(api_client, STAFF_A, matter_id, template["id"])
    assert package["status"] == "draft"

    # generate the demo (DOCX + PDF)
    response = api_client.post(
        f"/v1/packages/{package['id']}/generate-demo", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["package"]["status"] == "demo_released"
    assert len(body["versions"]) == 2
    formats = {version["file_format"] for version in body["versions"]}
    assert formats == {"docx", "pdf"}
    for version in body["versions"]:
        assert version["is_demo"] is True
        assert version["watermark_text"] == DEMO_TEXT
        # a6cbe7d replaced the user-visible DEMO wording with DRAFT:
        # demo versions are named *-DRAFT.<format> (suffix logic in
        # documents service: FINAL when unwatermarked, else DRAFT).
        assert version["file_name"].endswith("-DRAFT." + version["file_format"])
        assert version["download_url"].startswith("/v1/document-versions/")

    docx_version = next(v for v in body["versions"] if v["file_format"] == "docx")
    pdf_version = next(v for v in body["versions"] if v["file_format"] == "pdf")

    # the DOCX carries the visible banner, merged placeholders and footer disclaimer
    response = api_client.get(docx_version["download_url"], headers=auth(ATTORNEY_A))
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    docx_bytes = response.content
    body_text, header_xml = docx_texts(docx_bytes)
    assert DEMO_TEXT in body_text  # visible banner
    assert DEMO_TEXT in header_xml  # footer disclaimer
    assert "not legal advice" in body_text
    assert "Synthetic Household" in body_text  # {{household_name}} merged
    assert "{{" not in body_text  # no unreplaced placeholders

    # the PDF carries the same banner as extractable text
    response = api_client.get(pdf_version["download_url"], headers=auth(ATTORNEY_A))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    text = pdf_text(response.content)
    assert DEMO_TEXT in text
    assert "not legal advice" in text
    assert "Synthetic Household" in text

    # a household member can view the package and download the demo
    response = api_client.get(f"/v1/document-packages/{package['id']}", headers=auth(CLIENT_A))
    assert response.status_code == 200
    response = api_client.get(docx_version["download_url"], headers=auth(CLIENT_A))
    assert response.status_code == 200


def test_package_requires_existing_template(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    # a package can only bind a template that exists (404, not 5xx)
    response = api_client.post(
        f"/v1/matters/{matter_id}/document-packages",
        json={"template_id": str(uuid4())},
        headers=auth(STAFF_A),
    )
    assert response.status_code == 404


def test_production_release_requires_approved_document_release_gate(db, api_client):
    """Current contract (FUNC-012 superseded the always-409 stub):
    production release is a real, gated operation — without an approved
    ``document_release`` gate on the matter it fails closed with a 409
    problem response. If the gate check stops being enforced, this
    test fails (the release would proceed past the gate)."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)
    package = create_package_via_api(api_client, STAFF_A, matter_id, template["id"])

    response = api_client.post(
        f"/v1/packages/{package['id']}/release-production", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "conflict"
    assert "document release gate" in body["detail"]

    # An unknown package id is a plain 404, not a gate conflict.
    response = api_client.post(
        f"/v1/packages/{uuid4()}/release-production", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_renderers_watermark_unit():
    docx_bytes = render_demo_docx("Synthetic Title", "Synthetic body.", DEMO_TEXT)
    body_text, header_xml = docx_texts(docx_bytes)
    assert DEMO_TEXT in body_text
    assert DEMO_TEXT in header_xml  # diagonal VML watermark carries the text
    assert "not legal advice" in body_text

    pdf_bytes = render_demo_pdf("Synthetic Title", "Synthetic body.", DEMO_TEXT)
    text = pdf_text(pdf_bytes)
    assert DEMO_TEXT in text
    assert "not legal advice" in text


def _docx_parts(content: bytes) -> dict[str, str]:
    """Map every ``word/*.xml`` part name to its decoded XML text."""
    import zipfile

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        }


def test_renderers_draft_docx_footer_and_banner_carry_disclaimer():
    # Neutral body: any DRAFT marking in the output is the renderer's own.
    docx_bytes = render_demo_docx("Synthetic Title", "Synthetic body.", DEMO_TEXT)
    parts = _docx_parts(docx_bytes)
    footers = {name: xml for name, xml in parts.items() if "footer" in name}
    assert footers, "draft DOCX must have a footer part"
    assert any(DEMO_TEXT in xml for xml in footers.values())
    assert DEMO_TEXT in parts["word/document.xml"]  # visible banner


def test_renderers_final_docx_has_no_draft_marking_in_any_part():
    # R3: the final (watermark_text=None) DOCX must carry NO draft marking
    # in ANY carrier — document, headers or footers. The template body
    # passed in starts with its own DRAFT line, which _strip_draft_header
    # must keep stripping for finals.
    body = (
        "DRAFT — requires licensed attorney review before use.\n\n"
        "Synthetic final body."
    )
    docx_bytes = render_demo_docx("Synthetic Title", body, None)
    parts = _docx_parts(docx_bytes)
    assert parts, "final DOCX must have word parts to inspect"
    for name, xml in parts.items():
        assert "DRAFT" not in xml, name
        assert "NOT LEGALLY VALID" not in xml, name
    assert "Synthetic final body." in parts["word/document.xml"]


def test_renderers_final_pdf_has_no_draft_marking():
    body = (
        "DRAFT — requires licensed attorney review before use.\n\n"
        "Synthetic final body."
    )
    pdf_bytes = render_demo_pdf("Synthetic Title", body, None)
    text = pdf_text(pdf_bytes)
    assert "DRAFT" not in text
    assert "NOT LEGALLY VALID" not in text
    assert "Synthetic final body." in text

    # Mirror: the draft PDF is marked.
    draft_text = pdf_text(render_demo_pdf("Synthetic Title", "Synthetic body.", DEMO_TEXT))
    assert DEMO_TEXT in draft_text


def test_render_template_content_pure():
    merged = render_template_content(
        "Hello {{household_name}}, today is {{today}}.",
        {"household_name": "Synthetic Household", "today": "2026-09-27"},
    )
    assert merged == "Hello Synthetic Household, today is 2026-09-27."
    # Shipped contract (FUNC-021): unknown placeholders blank to ___ —
    # raw {{tokens}} never appear in generated documents.
    assert render_template_content("Keep {{unknown}}.", {}) == "Keep ___."
    # Digit-bearing tokens (e.g. the codicil's witness slots) blank too.
    assert render_template_content("Witness {{witness1_name}}.", {}) == "Witness ___."


def test_generate_demo_blanks_unknown_tokens_and_flags_escalations(db, api_client):
    """End-to-end render contract: an unknown token becomes ___ in the
    rendered output AND creates a document_unmapped_placeholder escalation
    (captured from the pre-blank token set, FUNC-021)."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    # Seed every required v0 intake answer so the completeness gate passes.
    for key, value in {
        "marital_status": "single",
        "primary_residence_state": "VA",
        "has_minor_children": False,
        "has_child_with_special_needs": False,
        "has_prior_marriage_or_children_outside_current_relationship": False,
        "owns_real_estate_outside_primary_jurisdiction": False,
        "owns_or_controls_business_interest": False,
        "expects_family_conflict": False,
        "primary_guardian_person": "Synthetic Guardian",
        "asset_categories_present": ["bank_account"],
        "has_debts": False,
    }.items():
        _exec(
            db,
            "INSERT INTO intake_answers (matter_id, question_key, answer)"
            " VALUES (%s, %s, %s::jsonb)",
            (matter_id, key, json.dumps(value)),
        )
    template = create_template_via_api(
        api_client,
        ADMIN_B,
        {
            "name": "Synthetic Gap Template",
            "template_type": "will",
            "pack_id": "us-va",
            "version": "1.0.0",
            "content": (
                "Will of {{household_name}}. Unknown: {{unknown_gap}}. "
                "Witness: {{witness1_name}}."
            ),
        },
    )
    package = create_package_via_api(api_client, STAFF_A, matter_id, template["id"])
    response = api_client.post(
        f"/v1/packages/{package['id']}/generate-demo", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 201, response.text
    versions = response.json()["versions"]
    docx_version = next(v for v in versions if v["file_format"] == "docx")
    response = api_client.get(docx_version["download_url"], headers=auth(ATTORNEY_A))
    assert response.status_code == 200
    body_text, _header_xml = docx_texts(response.content)
    assert "{{" not in body_text
    assert "Unknown: ___" in body_text
    assert "Witness: ___" in body_text
    db.rollback()  # clear any open read state before the direct query
    rows = db.execute(
        "SELECT rule_id FROM escalations WHERE matter_id = %s"
        " AND rule_id LIKE %s"
        " ORDER BY rule_id",
        (matter_id, "document_unmapped_placeholder:%"),
    ).fetchall()
    rule_ids = [row["rule_id"] for row in rows]
    assert rule_ids == [
        "document_unmapped_placeholder:Synthetic Gap Template:unknown_gap",
        "document_unmapped_placeholder:Synthetic Gap Template:witness1_name",
    ]


# ---------------------------------------------------------------------------
# Signing checklists + attestations
# ---------------------------------------------------------------------------


def test_signing_checklist_from_va_pack_two_witnesses(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    response = api_client.post(f"/v1/matters/{matter_id}/signing-checklists", headers=auth(STAFF_A))
    assert response.status_code == 201, response.text
    checklist = response.json()
    assert checklist["execution_profile_id"] == "VA-WILL-V0"
    witnesses = checklist["requirements"]["witnesses"]
    assert witnesses["required_count"] == 2
    assert witnesses["simultaneous_presence_required"] is True
    item_keys = {item["key"] for item in checklist["items"]}
    assert "witness_signatures" in item_keys
    assert len(checklist["items"]) == 4

    # household member can read it
    response = api_client.get(f"/v1/signing-checklists/{checklist['id']}", headers=auth(CLIENT_A))
    assert response.status_code == 200
    assert response.json()["id"] == checklist["id"]


def test_signing_checklist_unknown_document_type(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    response = api_client.post(
        f"/v1/matters/{matter_id}/signing-checklists",
        json={"document_type": "living_trust"},
        headers=auth(STAFF_A),
    )
    assert response.status_code == 422


def test_attestation_household_non_certifying(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    checklist = api_client.post(
        f"/v1/matters/{matter_id}/signing-checklists", headers=auth(STAFF_A)
    ).json()

    attested_at = datetime.now(UTC).isoformat()
    # FUNC-010 (Batch C3): only household writers (primary_client /
    # collaborator) and firm staff may attest — read-only household
    # roles (dependent / trusted_contact) and auditors get 403.
    for sub in (CLIENT_A, COLLAB_A):
        response = api_client.post(
            f"/v1/signing-checklists/{checklist['id']}/attest",
            json={
                "statement_key": "witness_signatures",
                "attested": True,
                "attested_at": attested_at,
            },
            headers=auth(sub),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["statement_key"] == "witness_signatures"
        assert body["attested"] is True
        assert body["attested_by_user_id"] == sub

    for sub in (DEPENDENT_A, AUDITOR_A):
        response = api_client.post(
            f"/v1/signing-checklists/{checklist['id']}/attest",
            json={
                "statement_key": "witness_signatures",
                "attested": True,
                "attested_at": attested_at,
            },
            headers=auth(sub),
        )
        assert response.status_code == 403, response.text

    # unknown statement keys are rejected
    response = api_client.post(
        f"/v1/signing-checklists/{checklist['id']}/attest",
        json={"statement_key": "not_a_statement", "attested": True, "attested_at": attested_at},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Funding tasks
# ---------------------------------------------------------------------------


def test_funding_tasks_from_playbooks(db, api_client):
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "bank_account", "Synthetic Checking")
    insert_asset(db, firm_id, household_id, ADMIN_B, "investment", "Synthetic Brokerage")
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")

    response = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 201, response.text
    tasks = response.json()
    # bank -> bank-tod-podb + general-beneficiary-alignment
    # investment -> brokerage-tod + general-beneficiary-alignment
    # real_estate -> real-property-deed
    assert len(tasks) == 5
    by_type = {task["task_type"] for task in tasks}
    assert by_type == {
        "bank-tod-podb",
        "brokerage-tod",
        "real-property-deed",
        "general-beneficiary-alignment",
    }
    deed_task = next(t for t in tasks if t["task_type"] == "real-property-deed")
    assert deed_task["evidence_type"] == "deed_copy"
    assert deed_task["attorney_review_recommended"] is True

    # generation is idempotent
    response = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 201
    assert response.json() == []

    # household member can list and update status
    response = api_client.get(f"/v1/matters/{matter_id}/funding-tasks", headers=auth(CLIENT_A))
    assert response.status_code == 200
    assert len(response.json()) == 5

    task_id = tasks[0]["id"]
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}", json={"status": "in_progress"}, headers=auth(CLIENT_A)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


def test_funding_task_evidence_and_invalid_status(db, api_client):
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    tasks = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    ).json()
    task_id = tasks[0]["id"]

    deed_bytes = b"%PDF-1.4 synthetic deed copy"
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        data={"status": "in_progress"},
        files={"evidence_file": ("deed-copy.pdf", deed_bytes, "application/pdf")},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["evidence"]["file_name"] == "deed-copy.pdf"
    assert updated["evidence"]["size_bytes"] == len(deed_bytes)
    assert updated["evidence"]["sha256"] == hashlib.sha256(deed_bytes).hexdigest()

    # invalid statuses are rejected
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        data={"status": "done"},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Vault shares
# ---------------------------------------------------------------------------


def _create_vault_share(api_client, sub, matter_id, **payload):
    response = api_client.post(
        f"/v1/matters/{matter_id}/vault-shares", json=payload, headers=auth(sub)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_vault_share_lifecycle(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    expires = (datetime.now(UTC) + timedelta(days=60)).isoformat()
    created = _create_vault_share(
        api_client, ATTORNEY_A, matter_id, label="Synthetic vault share", expires_at=expires
    )
    share_id = created["id"]
    # The create response is the web client's one-time reveal: it must carry
    # the raw token under `token` (the name the client, the /vault/access
    # payload, and the files download-token response all use) — never
    # `access_token`, which in this codebase means a Supabase JWT.
    assert "access_token" not in created
    assert created["revoked_at"] is None
    raw_token = created["token"]
    assert isinstance(raw_token, str) and len(raw_token) >= 16

    # the raw token is never stored — only a hash column exists
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'vault_shares' AND column_name = 'token_hash'"
        )
        assert cur.fetchone() is not None
        cur.execute("SELECT token_hash FROM vault_shares WHERE id = %s", (share_id,))
        stored = cur.fetchone()["token_hash"]
    assert stored != raw_token
    assert stored == hashlib.sha256(raw_token.encode()).hexdigest()

    # bearer access returns the redacted matter summary (no PII)
    response = api_client.post("/v1/vault/access", json={"token": raw_token})
    assert response.status_code == 200, response.text
    summary = response.json()
    assert set(summary) == {
        "matter_id",
        "matter_status",
        "matter_title",
        "plan_type",
        "jurisdiction_code",
    }
    assert summary["matter_id"] == matter_id
    assert summary["plan_type"] == "will"
    assert summary["jurisdiction_code"] == "US-VA"

    # unknown tokens 404
    response = api_client.post("/v1/vault/access", json={"token": "definitely-not-a-token"})
    assert response.status_code == 404

    # revoke kills access
    response = api_client.post(f"/v1/vault-shares/{share_id}/revoke", headers=auth(ATTORNEY_A))
    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
    response = api_client.post("/v1/vault/access", json={"token": raw_token})
    assert response.status_code == 410


def test_vault_share_expiry_validation(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    response = api_client.post(
        f"/v1/matters/{matter_id}/vault-shares",
        json={"label": "expired", "expires_at": past},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Family instruction letter
# ---------------------------------------------------------------------------


def test_instruction_letter_generation(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    create_template_via_api(api_client, ADMIN_B, INSTRUCTION_TEMPLATE)

    # household members generate the family letter
    response = api_client.post(
        f"/v1/matters/{matter_id}/instruction-letter",
        json={"letter_text": "Dear family, this synthetic letter is a demo."},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["package"]["status"] == "demo_released"
    pdf_version = next(v for v in body["versions"] if v["file_format"] == "pdf")
    response = api_client.get(pdf_version["download_url"], headers=auth(CLIENT_A))
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "NONBINDING" in text
    assert "Dear family" in text
    assert DEMO_TEXT in text


def test_instruction_letter_flags_unfilled_placeholders(db, api_client):
    """Batch C1 M-3: the instruction-letter merge must route through
    the same capture+flag mechanism as other documents — an unfilled
    letter token becomes ___ in the render AND escalates."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    create_template_via_api(
        api_client,
        ADMIN_B,
        {
            "name": "Synthetic Family Letter Template",
            "template_type": "instruction_letter",
            "pack_id": "us-va",
            "version": "1.0.0",
            "content": "Dear family,\n\n{{letter_text}}\n\nExecutor: {{executor_gap}}.\n",
        },
    )
    response = api_client.post(
        f"/v1/matters/{matter_id}/instruction-letter",
        json={"letter_text": "Dear family, this synthetic letter is a demo."},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 201, response.text
    pdf_version = next(
        v for v in response.json()["versions"] if v["file_format"] == "pdf"
    )
    response = api_client.get(pdf_version["download_url"], headers=auth(CLIENT_A))
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "Executor: ___" in text
    assert "{{" not in text
    db.rollback()
    rows = db.execute(
        "SELECT rule_id FROM escalations WHERE matter_id = %s"
        " AND rule_id LIKE %s ORDER BY rule_id",
        (matter_id, "document_unmapped_placeholder:%"),
    ).fetchall()
    assert [row["rule_id"] for row in rows] == [
        "document_unmapped_placeholder:Synthetic Family Letter Template:executor_gap"
    ]


def test_instruction_letter_refuses_released_package(db, api_client):
    """Batch C2 FUNC-007 residual: a RELEASED instruction-letter
    package must not be downgraded to demo_released / re-rendered —
    the same guard generate_demo carries (409 package_already_released),
    with status and versions left untouched."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    create_template_via_api(api_client, ADMIN_B, INSTRUCTION_TEMPLATE)
    response = api_client.post(
        f"/v1/matters/{matter_id}/instruction-letter",
        json={"letter_text": "Dear family, this synthetic letter is a demo."},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 201, response.text
    package_id = response.json()["package"]["id"]
    version_count = len(response.json()["versions"])
    _exec(db, "UPDATE document_packages SET status = 'released' WHERE id = %s", (package_id,))

    response = api_client.post(
        f"/v1/matters/{matter_id}/instruction-letter",
        json={"letter_text": "A second letter must not downgrade the release."},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "package_already_released"
    db.rollback()
    row = db.execute(
        "SELECT status FROM document_packages WHERE id = %s", (package_id,)
    ).fetchone()
    assert row["status"] == "released"
    count = db.execute(
        "SELECT COUNT(*) AS n FROM document_versions WHERE package_id = %s",
        (package_id,),
    ).fetchone()
    assert count["n"] == version_count


def test_instruction_letter_requires_template(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    response = api_client.post(
        f"/v1/matters/{matter_id}/instruction-letter",
        json={"letter_text": "No template exists."},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Worker actor: send_reminders
# ---------------------------------------------------------------------------


def test_send_reminders_worker(db):
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    scheduled_for = datetime.now(UTC) - timedelta(minutes=5)
    _exec(
        db,
        "INSERT INTO reminders (firm_id, matter_id, user_id, reminder_type, scheduled_for, payload)"
        " VALUES (%s, %s, %s, 'signing_deadline', %s, '{}'::jsonb)",
        (firm_id, matter_id, CLIENT_A, scheduled_for),
    )

    result = documents_service.send_reminders(db)
    assert result == {"processed": 1, "queued": 1}

    with db.cursor() as cur:
        cur.execute("SELECT sent_at FROM reminders")
        assert cur.fetchone()["sent_at"] is not None
        cur.execute("SELECT recipient_user_id, template_key, payload FROM notification_outbox")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert str(rows[0]["recipient_user_id"]) == CLIENT_A
    assert rows[0]["template_key"] == "reminder.due"
    assert rows[0]["payload"]["reminder_type"] == "signing_deadline"

    # second run is a no-op (already sent)
    result = documents_service.send_reminders(db)
    assert result == {"processed": 0, "queued": 0}


# ---------------------------------------------------------------------------
# Batch C2 regressions (deep-review 2026-10-29)
# ---------------------------------------------------------------------------


def test_generate_demo_on_released_package_is_409(db, api_client):
    """FUNC-007: generate_demo must refuse a released package (409,
    code package_already_released) instead of downgrading it to
    demo_released and appending DRAFT versions after FINALs."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    template = create_template_via_api(api_client, ADMIN_B, WILL_TEMPLATE)
    package = create_package_via_api(api_client, STAFF_A, matter_id, template["id"])
    _exec(db, "UPDATE document_packages SET status = 'released' WHERE id = %s", (package["id"],))

    response = api_client.post(
        f"/v1/packages/{package['id']}/generate-demo", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "package_already_released"

    with db.cursor() as cur:
        cur.execute("SELECT status FROM document_packages WHERE id = %s", (package["id"],))
        assert cur.fetchone()["status"] == "released"
        cur.execute(
            "SELECT COUNT(*) AS n FROM document_versions WHERE package_id = %s", (package["id"],)
        )
        assert cur.fetchone()["n"] == 0


def test_funding_tasks_other_asset_gets_general_task(db, api_client):
    """FUNC-014: an asset typed 'other' produces exactly one general
    beneficiary-alignment task — never silent zero."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "other", "Synthetic Misc Asset")

    response = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 201, response.text
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "general-beneficiary-alignment"

    # idempotent on regeneration
    response = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 201
    assert response.json() == []


def test_funding_tasks_unique_index_present(db):
    """FUNC-013: the (matter_id, asset_id, task_type) uniqueness guard
    exists in the schema the migrations build."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes"
            " WHERE tablename = 'funding_tasks' AND indexname = 'uq_funding_tasks_matter_asset_type'"
        )
        row = cur.fetchone()
    assert row is not None, "funding_tasks unique index missing"
    assert "UNIQUE" in row["indexdef"]


def test_funding_task_list_returns_stored_digest(db, api_client):
    """PERF-003: the evidence digest on the list path comes from the
    stored file_uploads.sha256 — set when the evidence was uploaded."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    tasks = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    ).json()
    task_id = tasks[0]["id"]

    deed_bytes = b"%PDF-1.4 synthetic deed copy for digest"
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        data={"status": "in_progress"},
        files={"evidence_file": ("deed-copy.pdf", deed_bytes, "application/pdf")},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 200, response.text
    expected = hashlib.sha256(deed_bytes).hexdigest()

    # the digest is persisted on the upload row at upload time
    with db.cursor() as cur:
        cur.execute(
            "SELECT sha256 FROM file_uploads WHERE id = %s",
            (response.json()["evidence_file_id"],),
        )
        assert cur.fetchone()["sha256"] == expected

    # and the list endpoint serves it from storage of the digest itself
    response = api_client.get(f"/v1/matters/{matter_id}/funding-tasks", headers=auth(CLIENT_A))
    assert response.status_code == 200, response.text
    listed = response.json()
    assert listed[0]["evidence"]["sha256"] == expected


# ---------------------------------------------------------------------------
# Batch C3 regressions (deep-review 2026-10-29): FUNC-010 / FUNC-011 /
# SEC-005 / FUNC-016
# ---------------------------------------------------------------------------


def test_attestation_server_stamps_time_ignoring_client_value(db, api_client):
    """FUNC-010: a backdated client-supplied attested_at must NOT be
    stored — the server stamps the record time."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    checklist = api_client.post(
        f"/v1/matters/{matter_id}/signing-checklists", headers=auth(STAFF_A)
    ).json()

    before = datetime.now(UTC)
    response = api_client.post(
        f"/v1/signing-checklists/{checklist['id']}/attest",
        json={
            "statement_key": "witness_signatures",
            "attested": True,
            "attested_at": "2001-01-01T00:00:00+00:00",
        },
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 201, response.text
    stamped = datetime.fromisoformat(response.json()["attested_at"])
    assert stamped >= before - timedelta(seconds=5)
    assert stamped <= datetime.now(UTC) + timedelta(seconds=5)

    # the stored checklist entry carries the server stamp too
    response = api_client.get(
        f"/v1/signing-checklists/{checklist['id']}", headers=auth(CLIENT_A)
    )
    stored = response.json()["attestations"]
    assert len(stored) == 1
    assert datetime.fromisoformat(stored[0]["attested_at"]) >= before - timedelta(seconds=5)


def test_attestation_trusted_contact_forbidden(db, api_client):
    """FUNC-010: trusted_contact is a read-only household role → 403."""
    _firm_id, household_id, matter_id = setup_firm_household_matter(db)
    create_user(db, TRUSTED_A)
    add_household_member(db, household_id, TRUSTED_A, "trusted_contact")
    checklist = api_client.post(
        f"/v1/matters/{matter_id}/signing-checklists", headers=auth(STAFF_A)
    ).json()
    response = api_client.post(
        f"/v1/signing-checklists/{checklist['id']}/attest",
        json={
            "statement_key": "witness_signatures",
            "attested": True,
            "attested_at": datetime.now(UTC).isoformat(),
        },
        headers=auth(TRUSTED_A),
    )
    assert response.status_code == 403, response.text


def _set_matter_status(db, matter_id: str, status: str) -> None:
    _exec(db, "UPDATE matters SET status = %s WHERE id = %s", (status, matter_id))


def test_amendment_requires_post_release_status(db, api_client):
    """FUNC-011: amendments only once documents are released / the plan
    is implemented; an intake-stage matter gets 409 with a clear code."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    # fixture matter is INTAKE_IN_PROGRESS
    response = api_client.post(
        f"/v1/matters/{matter_id}/amendments",
        json={"description": "Change the executor.", "affected_template_types": ["will"]},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "amendment_requires_released_plan"

    for status in ("DOCUMENTS_RELEASED", "PLAN_IMPLEMENTED"):
        _set_matter_status(db, matter_id, status)
        response = api_client.post(
            f"/v1/matters/{matter_id}/amendments",
            json={
                "description": f"Amendment while {status}.",
                "affected_template_types": ["will"],
            },
            headers=auth(CLIENT_A),
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "pending"


def test_amendment_duplicate_pending_is_idempotent(db, api_client):
    """FUNC-011: an identical pending request (same normalized
    description) returns the existing request instead of duplicating."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    _set_matter_status(db, matter_id, "PLAN_IMPLEMENTED")
    first = api_client.post(
        f"/v1/matters/{matter_id}/amendments",
        json={"description": "Change the executor.", "affected_template_types": ["will"]},
        headers=auth(CLIENT_A),
    )
    assert first.status_code == 201, first.text
    second = api_client.post(
        f"/v1/matters/{matter_id}/amendments",
        json={
            "description": "  change   THE executor. ",
            "affected_template_types": ["will"],
        },
        headers=auth(CLIENT_A),
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]

    listed = api_client.get(f"/v1/matters/{matter_id}/amendments", headers=auth(CLIENT_A))
    assert len(listed.json()) == 1


def test_amendment_dedupe_key_includes_template_types(db, api_client):
    """m-1 (Batch C3 follow-up): the dedupe key is description AND
    affected_template_types (normalized/sorted) — the same description
    aimed at different documents is a DIFFERENT request."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    _set_matter_status(db, matter_id, "PLAN_IMPLEMENTED")
    first = api_client.post(
        f"/v1/matters/{matter_id}/amendments",
        json={"description": "Update the beneficiaries.",
              "affected_template_types": ["will"]},
        headers=auth(CLIENT_A),
    )
    assert first.status_code == 201, first.text
    second = api_client.post(
        f"/v1/matters/{matter_id}/amendments",
        json={"description": "Update the beneficiaries.",
              "affected_template_types": ["trust"]},
        headers=auth(CLIENT_A),
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]

    # Same description + same types modulo order/case → idempotent.
    third = api_client.post(
        f"/v1/matters/{matter_id}/amendments",
        json={"description": "update the beneficiaries.",
              "affected_template_types": ["Will"]},
        headers=auth(CLIENT_A),
    )
    assert third.status_code == 201, third.text
    assert third.json()["id"] == first.json()["id"]

    listed = api_client.get(f"/v1/matters/{matter_id}/amendments", headers=auth(CLIENT_A))
    assert len(listed.json()) == 2


def test_amendment_idempotent_return_writes_audit_event(db, api_client):
    """m-1: when the idempotent path serves an existing request, an
    audit event records that the return happened (the create path's
    'requested' event alone would leave the replay invisible)."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    _set_matter_status(db, matter_id, "PLAN_IMPLEMENTED")
    payload = {"description": "Change the trustee.",
               "affected_template_types": ["trust"]}
    first = api_client.post(
        f"/v1/matters/{matter_id}/amendments", json=payload, headers=auth(CLIENT_A)
    )
    assert first.status_code == 201, first.text

    def _audit_count(action: str) -> int:
        with db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE action = %s AND matter_id = %s",
                (action, matter_id),
            )
            return cur.fetchone()["n"]

    assert _audit_count("document_amendment.idempotent_return") == 0
    second = api_client.post(
        f"/v1/matters/{matter_id}/amendments", json=payload, headers=auth(CLIENT_A)
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    db.commit()
    assert _audit_count("document_amendment.idempotent_return") == 1


def test_vault_share_expiry_bounds(db, api_client):
    """SEC-005/FUNC-016: >90 days → 422; exactly 90 days OK; naive
    datetime → 422; omitted still defaults to ~30 days."""
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)

    too_far = (datetime.now(UTC) + timedelta(days=91)).isoformat()
    response = api_client.post(
        f"/v1/matters/{matter_id}/vault-shares",
        json={"label": "too far", "expires_at": too_far},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 422, response.text

    exactly = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    created = _create_vault_share(
        api_client, ATTORNEY_A, matter_id, label="exactly 90", expires_at=exactly
    )
    assert created["expires_at"] is not None

    naive = (datetime.now(UTC) + timedelta(days=10)).replace(tzinfo=None).isoformat()
    response = api_client.post(
        f"/v1/matters/{matter_id}/vault-shares",
        json={"label": "naive", "expires_at": naive},
        headers=auth(ATTORNEY_A),
    )
    assert response.status_code == 422, response.text

    created = _create_vault_share(api_client, ATTORNEY_A, matter_id, label="defaulted")
    expires = datetime.fromisoformat(created["expires_at"])
    delta = expires - datetime.now(UTC)
    assert timedelta(days=29) < delta <= timedelta(days=30)


def test_vault_share_service_rejects_naive_and_overlong(db):
    """SEC-005 defense in depth: the service itself rejects naive /
    >90-day expiries even when the schema is bypassed (model_construct)."""
    from app.core.errors import ValidationAppError
    from app.domains.documents import schemas as documents_schemas

    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    naive_payload = documents_schemas.VaultShareCreate.model_construct(
        label="naive", expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=5)
    )
    with pytest.raises(ValidationAppError):
        documents_service.create_vault_share(
            db, actor_sub=ATTORNEY_A, matter_id=matter_id, payload=naive_payload
        )
    db.rollback()
    far_payload = documents_schemas.VaultShareCreate.model_construct(
        label="far", expires_at=datetime.now(UTC) + timedelta(days=365)
    )
    with pytest.raises(ValidationAppError):
        documents_service.create_vault_share(
            db, actor_sub=ATTORNEY_A, matter_id=matter_id, payload=far_payload
        )
    db.rollback()


# ---------------------------------------------------------------------------
# Batch C3: SEC-011 — funding-task evidence role model + scan pipeline
# ---------------------------------------------------------------------------


def _make_funding_task(db, api_client, matter_id):
    tasks = api_client.post(
        f"/v1/matters/{matter_id}/funding-tasks/generate", headers=auth(ATTORNEY_A)
    ).json()
    return tasks[0]["id"]


def test_funding_task_update_auditor_forbidden(db, api_client):
    """SEC-011(a): a read-only firm auditor must not write funding tasks."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    task_id = _make_funding_task(db, api_client, matter_id)

    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        json={"status": "in_progress"},
        headers=auth(AUDITOR_A),
    )
    assert response.status_code == 403, response.text

    # firm staff still can
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        json={"status": "in_progress"},
        headers=auth(STAFF_A),
    )
    assert response.status_code == 200, response.text


def test_funding_evidence_pending_scan_and_sniffed_mime(db, api_client):
    """SEC-011(b/c): evidence enters the scan pipeline as 'pending'
    (never hardcoded 'clean'), its MIME is derived from magic bytes
    (not the client's claim), and the PERF-003 digest is recorded."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    task_id = _make_funding_task(db, api_client, matter_id)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"synthetic-png-evidence" * 4
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        data={"status": "in_progress"},
        # spoofed: PNG bytes claiming to be a PDF
        files={"evidence_file": ("deed-copy.pdf", png_bytes, "application/pdf")},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 200, response.text
    evidence_id = response.json()["evidence_file_id"]
    assert response.json()["evidence"]["sha256"] == hashlib.sha256(png_bytes).hexdigest()

    with db.cursor() as cur:
        cur.execute(
            "SELECT scan_status, mime_type, sha256 FROM file_uploads WHERE id = %s",
            (evidence_id,),
        )
        row = cur.fetchone()
    assert row["scan_status"] == "pending"
    assert row["mime_type"] == "image/png"
    assert row["sha256"] == hashlib.sha256(png_bytes).hexdigest()


def test_funding_evidence_oversize_rejected(db, api_client):
    """SEC-011(c): the files-domain 25 MB cap applies to evidence."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    task_id = _make_funding_task(db, api_client, matter_id)

    oversize = b"%PDF" + b"x" * (25 * 1024 * 1024)
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        data={"status": "in_progress"},
        files={"evidence_file": ("big.pdf", oversize, "application/pdf")},
        headers=auth(CLIENT_A),
    )
    # SEC-014's router cap rejects at 413 before the service's own
    # 422 cap (defense in depth) is reached.
    assert response.status_code == 413, response.text


def test_funding_evidence_unknown_magic_rejected(db, api_client):
    """SEC-011(c): bytes the scanner cannot identify fail closed (422)."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    task_id = _make_funding_task(db, api_client, matter_id)

    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        data={"status": "in_progress"},
        files={"evidence_file": ("mystery.pdf", b"not-a-real-file-at-all", "application/pdf")},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Batch C3: FUNC-017 — send_reminders counters track reality
# ---------------------------------------------------------------------------


def test_send_reminders_counts_failed_enqueue_honestly(db, monkeypatch):
    """FUNC-017: processed counts rows examined; queued counts only
    successful enqueues — a failing enqueue yields processed > queued."""
    from app.domains.documents import repository as documents_repo
    from app.domains.review import repository as review_repo

    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    scheduled_for = datetime.now(UTC) - timedelta(minutes=5)
    reminder_ids = []
    for _ in range(2):
        rid = str(uuid4())
        reminder_ids.append(rid)
        _exec(
            db,
            "INSERT INTO reminders (id, firm_id, matter_id, user_id, reminder_type,"
            " scheduled_for, payload) VALUES (%s, %s, %s, %s, 'signing_deadline', %s, '{}'::jsonb)",
            (rid, firm_id, matter_id, CLIENT_A, scheduled_for),
        )

    real_enqueue = review_repo.enqueue_notification
    calls = {"n": 0}

    def flaky_enqueue(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("broker unavailable (synthetic)")
        return real_enqueue(conn, **kwargs)

    monkeypatch.setattr(review_repo, "enqueue_notification", flaky_enqueue)
    result = documents_service.send_reminders(db)
    db.commit()
    assert result == {"processed": 2, "queued": 1}
    # the failed reminder was NOT marked sent; the successful one was
    with db.cursor() as cur:
        cur.execute("SELECT id, sent_at FROM reminders ORDER BY created_at")
        rows = cur.fetchall()
    sent = [r for r in rows if r["sent_at"] is not None]
    assert len(sent) == 1


def test_send_reminders_failure_logs_at_error_level(db, monkeypatch, caplog):
    """m-3 (Batch C3 follow-up): a per-reminder failure in the API copy
    of send_reminders must log at ERROR (logger.exception), matching
    the worker copy, so ERROR-level alerting fires."""
    import logging as _logging

    from app.domains.review import repository as review_repo

    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    scheduled_for = datetime.now(UTC) - timedelta(minutes=5)
    _exec(
        db,
        "INSERT INTO reminders (firm_id, matter_id, user_id, reminder_type, scheduled_for, payload)"
        " VALUES (%s, %s, %s, 'signing_deadline', %s, '{}'::jsonb)",
        (firm_id, matter_id, CLIENT_A, scheduled_for),
    )

    def boom(conn, **kwargs):
        raise RuntimeError("broker unavailable (synthetic)")

    monkeypatch.setattr(review_repo, "enqueue_notification", boom)
    with caplog.at_level(_logging.ERROR, logger="app.domains.documents.service"):
        result = documents_service.send_reminders(db)
    db.commit()
    assert result == {"processed": 1, "queued": 0}
    errors = [
        r for r in caplog.records
        if r.name == "app.domains.documents.service" and r.levelno >= _logging.ERROR
    ]
    assert errors, "per-reminder failure must produce an ERROR-level log record"
    assert errors[0].exc_info is not None


def test_funding_evidence_file_id_must_belong_to_same_matter(db, api_client):
    """m-4 (Batch C3 follow-up): update_funding_task's evidence_file_id
    branch previously accepted ANY upload from the same firm. The
    referenced upload must belong to the funding task's own matter —
    a cross-matter (same-firm) id reads as not-found (404), matching
    the codebase's existence-oracle convention."""
    firm_id, household_id, matter_id = setup_firm_household_matter(db)
    insert_asset(db, firm_id, household_id, ADMIN_B, "real_estate", "Synthetic Cabin")
    task_id = _make_funding_task(db, api_client, matter_id)
    other_matter_id = insert_matter(db, firm_id, household_id)

    def _upload(for_matter: str) -> str:
        upload_id = str(uuid4())
        _exec(
            db,
            "INSERT INTO file_uploads (id, firm_id, household_id, matter_id,"
            " uploader_user_id, original_filename, stored_path, mime_type,"
            " size_bytes, scan_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'clean')",
            (upload_id, firm_id, household_id, for_matter, CLIENT_A,
             "deed.pdf", f"/tmp/{upload_id}.pdf", "application/pdf", 10),
        )
        return upload_id

    other_upload = _upload(other_matter_id)
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        json={"evidence_file_id": other_upload},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 404, response.text

    own_upload = _upload(matter_id)
    response = api_client.patch(
        f"/v1/funding-tasks/{task_id}",
        json={"evidence_file_id": own_upload},
        headers=auth(CLIENT_A),
    )
    assert response.status_code == 200, response.text
    assert response.json()["evidence_file_id"] == own_upload
