"""Stage F regression tests for D-C5-1 / D-C5-2 (production 500s, 2026-10-26).

Root cause (Stage F): the §10 files-domain fix pass (commits 3d99b2e F3
provenance + 52b14fe F7 unreadable-scan) shipped code + migrations
``20261026130000_file_analysis_provenance.sql`` and
``20261026133000_file_unreadable_scan_status.sql``, but those two
migrations were never applied to the production database. Production
``file_uploads`` therefore has no ``analysis_source`` /
``ai_suggested_tags`` columns and its ``extraction_status`` CHECK still
rejects ``'unreadable_scan'``:

* complete() on a textless scan -> update_extraction(...,
  'unreadable_scan') violates the CHECK -> HTTP 500, row stranded at
  'pending' (D-C5-1).
* analyze() on a text PDF -> update_analysis(...) writes the two
  missing columns -> HTTP 500, file never analyzed (D-C5-2).

The pre-existing coverage missed this because the provenance test used a
fake connection (asserting SQL strings) and the extraction test was
unit-level: nothing exercised complete/analyze end to end against a
real database. These tests do — twice over:

* against the full repo migration chain (the schema production MUST
  have): both flows succeed; and
* against a "stale" schema with exactly the two §10 files migrations
  withheld (the schema production actually had): both flows reproduce
  the production 500s, proving the tests are sensitive to the drift.

Fix for production = apply the two withheld migrations (coordinator
step; see stage-f-c5-log.md). No code change was needed: the code is
correct against its declared schema.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

ATTORNEY_A = "a1111111-1111-1111-1111-111111111111"
CLIENT_A = "a3333333-3333-3333-3333-333333333333"
USERS = {ATTORNEY_A: "attorney.a@example.test", CLIENT_A: "client.a@example.test"}
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " asset_flags, assets, liabilities, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text, raw_user_meta_data jsonb);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
-- The storage-buckets migration inserts into storage.buckets; stub the
-- schema/table so the migration sweep can run on a bare pgserver
-- (same stub as test_slice4_documents.py / test_interview.py).
CREATE SCHEMA IF NOT EXISTS storage;
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY, name text, public boolean
);
"""

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

# The two §10 files-domain migrations that production was missing.
PROD_MISSING_MIGRATIONS = frozenset(
    {
        "20261026130000_file_analysis_provenance.sql",
        "20261026133000_file_unreadable_scan_status.sql",
    }
)


def _start_pg(tmp_path_factory, name: str, exclude: frozenset[str]) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp(name)))
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if migration.name in exclude:
                continue
            conn.execute(migration.read_text())
        conn.execute(SEED_FILE.read_text())
    return uri


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory) -> str:
    """Full repo migration chain — the schema production must have."""
    return _start_pg(tmp_path_factory, "pgdata-c5-full", frozenset())


@pytest.fixture(scope="session")
def pg_uri_stale(tmp_path_factory) -> str:
    """Production's actual 2026-10-26 schema: §10 files migrations withheld."""
    return _start_pg(tmp_path_factory, "pgdata-c5-stale", PROD_MISSING_MIGRATIONS)


def _db(pg_uri: str):
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def db(pg_uri):
    yield from _db(pg_uri)


@pytest.fixture()
def db_stale(pg_uri_stale):
    yield from _db(pg_uri_stale)


def _client(pg_uri: str, monkeypatch, tmp_path) -> TestClient:
    storage_dir = tmp_path / "uploads"
    storage_dir.mkdir()
    monkeypatch.setenv("STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "test-only-download-key-material")
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
    # app.core.db pools connections process-wide; point it at THIS
    # fixture's database (full vs stale schema) instead of whichever
    # client was built first.
    from app.core import db as core_db

    if core_db._pool is not None:
        core_db._pool.close()
        core_db._pool = None
    from app.domains.files.router import router as files_router
    from app.main import create_app

    app = create_app()
    app.include_router(files_router, prefix="/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def api_client(pg_uri, monkeypatch, tmp_path):
    return _client(pg_uri, monkeypatch, tmp_path)


@pytest.fixture()
def api_client_stale(pg_uri_stale, monkeypatch, tmp_path):
    return _client(pg_uri_stale, monkeypatch, tmp_path)


# ---------------------------------------------------------------------------
# Synthetic builders (mirrors test_slice4_files.py)
# ---------------------------------------------------------------------------


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def setup_matter(db) -> str:
    for sub, email in USERS.items():
        _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
        _exec(
            db,
            "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING",  # auth trigger may have created it
            (sub, email, f"Synthetic {email}"),
        )
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status)"
        " VALUES (%s, %s, %s, 'active')",
        (firm_id, "Synthetic Firm", "Synthetic Firm"),
    )
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, 'attorney', 'active')",
        (firm_id, ATTORNEY_A),
    )
    household_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, firm_id, "Synthetic Household", ATTORNEY_A),
    )
    _exec(
        db,
        "INSERT INTO household_members (household_id, user_id, relationship_role,"
        " access_status) VALUES (%s, %s, 'primary_client', 'active')",
        (household_id, CLIENT_A),
    )
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "0.1.0"}
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


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub, email=USERS[sub])}"}


# ---------------------------------------------------------------------------
# Synthetic PDFs (real, parseable — reportlab, like Stage C's controls)
# ---------------------------------------------------------------------------


def _text_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Last Will and Testament - QA test document")
    c.drawString(72, 700, "The testator is Jordan Alex Miller of Richmond, Virginia.")
    c.drawString(72, 680, "I appoint Jane Miller as Executor.")
    c.save()
    return buf.getvalue()


def _textless_scan_pdf() -> bytes:
    """A page that parses but holds no text: rect art only, no Tj operators."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFillGray(0.92)
    c.rect(36, 36, 540, 720, stroke=0, fill=1)
    c.showPage()
    c.save()
    return buf.getvalue()


def _intent(client, matter_id: str, filename: str, size: int):
    return client.post(
        f"/v1/matters/{matter_id}/files/intent",
        json={
            "filename": filename,
            "mime_type": "application/pdf",
            "size_bytes": size,
            "purpose": "document",
        },
        headers=auth(ATTORNEY_A),
    )


def _complete(client, upload_id: str, content: bytes, filename: str):
    return client.post(
        f"/v1/files/{upload_id}/complete",
        files={"file": (filename, content, "application/pdf")},
        headers=auth(ATTORNEY_A),
    )


def _upload(client, matter_id: str, content: bytes, filename: str) -> str:
    intent = _intent(client, matter_id, filename, len(content))
    assert intent.status_code == 201, intent.text
    return intent.json()["upload_id"]


# ---------------------------------------------------------------------------
# The contract production must satisfy (full migration chain)
# ---------------------------------------------------------------------------


def test_complete_textless_scan_records_unreadable_scan(db, api_client):
    """D-C5-1: a parsed-but-textless PDF completes 200 as 'unreadable_scan'."""
    matter_id = setup_matter(db)
    content = _textless_scan_pdf()
    upload_id = _upload(api_client, matter_id, content, "scan.pdf")

    response = _complete(api_client, upload_id, content, "scan.pdf")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction_status"] == "unreadable_scan"
    assert body["scan_status"] == "pending"  # worker scan is a separate step

    with db.cursor() as cur:
        cur.execute(
            "SELECT extraction_status FROM file_uploads WHERE id = %s", (upload_id,)
        )
        assert cur.fetchone()["extraction_status"] == "unreadable_scan"

    # ...and analyze refuses it cleanly (422), never a 500.
    response = api_client.post(
        f"/v1/files/{upload_id}/analyze", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 422, response.text


def test_analyze_text_pdf_persists_analysis_with_provenance(db, api_client):
    """D-C5-2: analyze on an extracted text PDF succeeds and persists F3 fields."""
    matter_id = setup_matter(db)
    content = _text_pdf()
    upload_id = _upload(api_client, matter_id, content, "will.pdf")

    response = _complete(api_client, upload_id, content, "will.pdf")
    assert response.status_code == 200, response.text
    assert response.json()["extraction_status"] == "done"
    assert "Jordan Alex Miller" in (response.json()["extracted_text"] or "")

    response = api_client.post(
        f"/v1/files/{upload_id}/analyze", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_type"] == "will"
    # Test env has no OpenAI provider configured -> deterministic rules.
    assert body["analysis_source"] == "rules"
    assert body["suggested_tags"]

    with db.cursor() as cur:
        cur.execute(
            "SELECT document_type, analysis_source, ai_suggested_tags, tags,"
            " analyzed_at FROM file_uploads WHERE id = %s",
            (upload_id,),
        )
        row = cur.fetchone()
    assert row["document_type"] == "will"
    assert row["analysis_source"] == "rules"
    assert list(row["ai_suggested_tags"]) == body["suggested_tags"]
    assert set(body["suggested_tags"]) <= set(row["tags"])  # merged into tags
    assert row["analyzed_at"] is not None


def test_schema_contract_columns_and_unreadable_scan_value(db):
    """Pin the production contract: columns exist; CHECK admits the status."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'file_uploads'"
        )
        columns = {r["column_name"] for r in cur.fetchall()}
        cur.execute(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid = 'file_uploads'::regclass AND contype = 'c'"
            " AND conname = 'file_uploads_extraction_status_check'"
        )
        constraint = cur.fetchone()
    assert {"analysis_source", "ai_suggested_tags"} <= columns
    assert constraint is not None and "unreadable_scan" in constraint["def"]


# ---------------------------------------------------------------------------
# Sensitivity proof: production's stale schema reproduces both 500s.
# (If these ever PASS, the endpoints stopped depending on the §10 schema
# and the production fix instructions must be revisited.)
# ---------------------------------------------------------------------------


def test_stale_schema_reproduces_d_c5_1(db_stale, api_client_stale):
    matter_id = setup_matter(db_stale)
    content = _textless_scan_pdf()
    upload_id = _upload(api_client_stale, matter_id, content, "scan.pdf")

    response = _complete(api_client_stale, upload_id, content, "scan.pdf")
    assert response.status_code == 500, response.text
    with db_stale.cursor() as cur:
        cur.execute(
            "SELECT extraction_status FROM file_uploads WHERE id = %s", (upload_id,)
        )
        # Stranded at 'pending' — the CHECK violation rolled the update back.
        assert cur.fetchone()["extraction_status"] == "pending"


def test_stale_schema_reproduces_d_c5_2(db_stale, api_client_stale):
    matter_id = setup_matter(db_stale)
    content = _text_pdf()
    upload_id = _upload(api_client_stale, matter_id, content, "will.pdf")

    response = _complete(api_client_stale, upload_id, content, "will.pdf")
    assert response.status_code == 200, response.text
    assert response.json()["extraction_status"] == "done"

    response = api_client_stale.post(
        f"/v1/files/{upload_id}/analyze", headers=auth(ATTORNEY_A)
    )
    assert response.status_code == 500, response.text
    with db_stale.cursor() as cur:
        cur.execute(
            "SELECT document_type, analyzed_at FROM file_uploads WHERE id = %s",
            (upload_id,),
        )
        row = cur.fetchone()
    assert row["document_type"] is None and row["analyzed_at"] is None
