"""Slice 4 — files domain API tests (Track B4).

Exercises the files API surface:
``app/domains/files/{repository,service,schemas,router}.py`` plus the
worker scanner (``services/worker/worker/scanning.py``) and the
``scan_file`` actor core.

Lifecycle under test: intent (validated) -> complete (bytes persisted,
``pending``) -> worker scan (``clean`` / ``quarantined``) -> HMAC
download token -> token-gated download (``clean`` only).

These tests run against the real Track A4 migration
(``supabase/migrations/20260927030000_files_documents_slice4.sql``),
which the ``pg_uri`` fixture applies from the migrations directory.
The worker's ``worker.scanning`` module is imported via an explicit
``sys.path`` insertion (the API venv does not install the worker
package); it needs no dramatiq import at module level.

All fixture data is synthetic. Nothing here touches main.py or
api/v1/router.py wiring.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.domains.files import service as files_service
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

# ---------------------------------------------------------------------------
# Synthetic identities (fixed UUIDs so tests read deterministically)
# ---------------------------------------------------------------------------

ATTORNEY_A = "a1111111-1111-1111-1111-111111111111"
CLIENT_A = "a3333333-3333-3333-3333-333333333333"
DEPENDENT_A = "a5555555-5555-5555-5555-555555555555"
AUDITOR_A = "a6666666-6666-6666-6666-666666666666"
ADMIN_B = "b1111111-1111-1111-1111-111111111111"

USERS = {
    ADMIN_B: "admin.b@example.test",
    ATTORNEY_A: "attorney.a@example.test",
    AUDITOR_A: "auditor.a@example.test",
    CLIENT_A: "client.a@example.test",
    DEPENDENT_A: "dependent.a@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

DOWNLOAD_SECRET = "synthetic-download-secret"

# ---------------------------------------------------------------------------

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
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
"""

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
WORKER_DIR = Path(__file__).resolve().parents[3] / "services" / "worker"


def _worker_scanning():
    """Import the worker scan core (API venv does not install the worker package)."""
    if str(WORKER_DIR) not in sys.path:
        sys.path.insert(0, str(WORKER_DIR))
    from worker import scanning

    return scanning


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
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-slice4-files")))
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
    """TestClient with pgserver as DATABASE_URL, test JWT override, an
    isolated upload storage dir, and a configured download-token secret."""
    storage_dir = tmp_path / "uploads"
    storage_dir.mkdir()
    monkeypatch.setenv("STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", DOWNLOAD_SECRET)
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()  # force settings reload for the new env
    from app.domains.files.router import router as files_router
    from app.main import create_app

    app = create_app()
    app.include_router(files_router, prefix="/v1")
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


def setup_firm_household_matter(db):
    """Standard synthetic tree: firm, attorney+auditor, client household, matter."""
    for sub in USERS:
        create_user(db, sub)
    firm_id = create_firm(db)
    add_firm_member(db, firm_id, ADMIN_B, "firm_admin")
    add_firm_member(db, firm_id, ATTORNEY_A, "attorney")
    add_firm_member(db, firm_id, AUDITOR_A, "auditor")
    household_id = create_household(db, firm_id, ADMIN_B)
    add_household_member(db, household_id, CLIENT_A, "primary_client")
    add_household_member(db, household_id, DEPENDENT_A, "dependent")
    matter_id = insert_matter(db, firm_id, household_id)
    return firm_id, household_id, matter_id


def token(sub: str) -> str:
    return mint_token(sub=sub, email=USERS.get(sub, f"{sub}@example.test"))


def auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub)}"}


# ---------------------------------------------------------------------------
# Synthetic file bytes (magic-byte-correct)
# ---------------------------------------------------------------------------

PDF_BYTES = b"%PDF-1.4\n%synthetic\n1 0 obj<</>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
DOCX_BYTES = b"PK\x03\x04" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _intent(
    api_client,
    sub,
    matter_id,
    filename="report.pdf",
    mime_type="application/pdf",
    size_bytes=None,
    purpose="evidence",
):
    payload = {
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": len(PDF_BYTES) if size_bytes is None else size_bytes,
        "purpose": purpose,
    }
    return api_client.post(f"/v1/matters/{matter_id}/files/intent", json=payload, headers=auth(sub))


def _complete(
    api_client,
    sub,
    upload_id,
    content=PDF_BYTES,
    filename="report.pdf",
    mime_type="application/pdf",
):
    return api_client.post(
        f"/v1/files/{upload_id}/complete",
        files={"file": (filename, content, mime_type)},
        headers=auth(sub),
    )


def _scan(db, upload_id: str) -> dict:
    scanning = _worker_scanning()
    outcome = scanning.perform_file_scan(db, UUID(upload_id))
    db.commit()
    return outcome


def _upload_clean_file(
    db,
    api_client,
    sub,
    matter_id,
    content=PDF_BYTES,
    filename="report.pdf",
    mime_type="application/pdf",
) -> dict:
    """Intent -> complete -> scan; assert the scan verdict is clean."""
    intent = _intent(
        api_client,
        sub,
        matter_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(content),
    ).json()
    upload_id = intent["upload_id"]
    response = _complete(
        api_client, sub, upload_id, content=content, filename=filename, mime_type=mime_type
    )
    assert response.status_code == 200, response.text
    outcome = _scan(db, upload_id)
    assert outcome == {"scanned": 1, "status": "clean"}, outcome
    return intent


# ---------------------------------------------------------------------------
# Intent validation
# ---------------------------------------------------------------------------


def test_upload_intent_validation(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)

    # bad MIME -> 422
    response = _intent(api_client, ATTORNEY_A, matter_id, mime_type="application/x-msdownload")
    assert response.status_code == 422

    # oversize -> 422
    response = _intent(api_client, ATTORNEY_A, matter_id, size_bytes=26 * 1024 * 1024)
    assert response.status_code == 422

    # path traversal -> 422
    response = _intent(api_client, ATTORNEY_A, matter_id, filename="../../etc/passwd")
    assert response.status_code == 422
    response = _intent(api_client, ATTORNEY_A, matter_id, filename="..\\windows\\secret")
    assert response.status_code == 422

    # empty filename -> 422
    response = _intent(api_client, ATTORNEY_A, matter_id, filename="   ")
    assert response.status_code == 422

    # filename over 255 chars -> 422
    response = _intent(api_client, ATTORNEY_A, matter_id, filename="a" * 256 + ".pdf")
    assert response.status_code == 422

    # unknown purpose -> 422
    response = _intent(api_client, ATTORNEY_A, matter_id, purpose="ransomware")
    assert response.status_code == 422

    # happy path -> 201, pending row, UUID-based stored path
    response = _intent(api_client, ATTORNEY_A, matter_id)
    assert response.status_code == 201, response.text
    body = response.json()
    upload_id = body["upload_id"]
    assert body["status"] == "pending"
    assert body["stored_path"].endswith(".pdf")
    assert "report.pdf" not in body["stored_path"]  # original name never hits the fs

    with db.cursor() as cur:
        cur.execute(
            "SELECT scan_status, mime_type, size_bytes, original_filename FROM file_uploads"
            " WHERE id = %s",
            (upload_id,),
        )
        row = cur.fetchone()
    assert row["scan_status"] == "pending"
    assert row["mime_type"] == "application/pdf"
    assert row["size_bytes"] == len(PDF_BYTES)
    assert row["original_filename"] == "report.pdf"


def test_upload_intent_roles(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)

    # household primary_client may upload
    response = _intent(api_client, CLIENT_A, matter_id)
    assert response.status_code == 201, response.text

    # dependent is read-only -> 403
    response = _intent(api_client, DEPENDENT_A, matter_id)
    assert response.status_code == 403

    # auditor is excluded from staff write -> 403
    response = _intent(api_client, AUDITOR_A, matter_id)
    assert response.status_code == 403

    # dependent may still list
    response = api_client.get(f"/v1/matters/{matter_id}/files", headers=auth(DEPENDENT_A))
    assert response.status_code == 200
    assert len(response.json()) == 1


# ---------------------------------------------------------------------------
# Complete -> scan -> clean -> download flow
# ---------------------------------------------------------------------------


def test_complete_scan_clean_download_flow(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)
    upload_id = intent["upload_id"]

    # mint a download token
    response = api_client.post(f"/v1/files/{upload_id}/download-token", headers=auth(ATTORNEY_A))
    assert response.status_code == 200, response.text
    token_body = response.json()
    assert token_body["token"]
    expires_at = datetime.fromisoformat(token_body["expires_at"])
    assert expires_at > datetime.now(UTC)

    # the download endpoint is public (no auth header) and streams the bytes
    response = api_client.get(f"/v1/files/download?token={token_body['token']}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert 'attachment; filename="report.pdf"' in response.headers["content-disposition"]
    assert response.content == PDF_BYTES

    # a household member can list the matter's files
    response = api_client.get(f"/v1/matters/{matter_id}/files", headers=auth(CLIENT_A))
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == upload_id
    assert items[0]["scan_status"] == "clean"


def test_complete_validates_size_and_state(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _intent(api_client, ATTORNEY_A, matter_id).json()
    upload_id = intent["upload_id"]

    # byte count must match the intent's declared size -> 422
    response = _complete(api_client, ATTORNEY_A, upload_id, content=b"%PDF-short")
    assert response.status_code == 422

    # correct bytes complete the upload
    response = _complete(api_client, ATTORNEY_A, upload_id)
    assert response.status_code == 200
    assert response.json()["scan_status"] == "pending"

    # retrying while still pending is idempotent (same bytes -> 200)
    response = _complete(api_client, ATTORNEY_A, upload_id)
    assert response.status_code == 200

    # once scanned, re-completing is a conflict (409), not a silent overwrite
    assert _scan(db, upload_id) == {"scanned": 1, "status": "clean"}
    response = _complete(api_client, ATTORNEY_A, upload_id)
    assert response.status_code == 409


def test_pending_file_cannot_download(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _intent(api_client, ATTORNEY_A, matter_id).json()
    upload_id = intent["upload_id"]
    assert _complete(api_client, ATTORNEY_A, upload_id).status_code == 200

    # token minting works, but the unscanned file itself is not downloadable
    token_body = api_client.post(
        f"/v1/files/{upload_id}/download-token", headers=auth(ATTORNEY_A)
    ).json()
    response = api_client.get(f"/v1/files/download?token={token_body['token']}")
    assert response.status_code == 403


def test_quarantined_file_cannot_download(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    # claim PNG, deliver PDF bytes -> magic-byte mismatch -> quarantined
    intent = _intent(
        api_client,
        ATTORNEY_A,
        matter_id,
        filename="photo.png",
        mime_type="image/png",
        size_bytes=len(PDF_BYTES),
    ).json()
    upload_id = intent["upload_id"]
    assert _complete(api_client, ATTORNEY_A, upload_id).status_code == 200

    outcome = _scan(db, upload_id)
    assert outcome == {"scanned": 1, "status": "quarantined"}, outcome
    with db.cursor() as cur:
        cur.execute("SELECT scan_detail FROM file_uploads WHERE id = %s", (upload_id,))
        assert "magic bytes" in cur.fetchone()["scan_detail"]

    token_body = api_client.post(
        f"/v1/files/{upload_id}/download-token", headers=auth(ATTORNEY_A)
    ).json()
    response = api_client.get(f"/v1/files/download?token={token_body['token']}")
    assert response.status_code == 403

    # the quarantine verdict is visible in the listing
    items = api_client.get(f"/v1/matters/{matter_id}/files", headers=auth(ATTORNEY_A)).json()
    assert items[0]["scan_status"] == "quarantined"


# ---------------------------------------------------------------------------
# Download token edge cases
# ---------------------------------------------------------------------------


def test_expired_token_rejected(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)
    upload_id = intent["upload_id"]

    expired_token, _ = files_service.build_download_token(
        UUID(upload_id), secret=DOWNLOAD_SECRET, ttl_seconds=-60
    )
    response = api_client.get(f"/v1/files/download?token={expired_token}")
    assert response.status_code == 401


def test_tampered_and_unknown_tokens_rejected(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)

    token_body = api_client.post(
        f"/v1/files/{intent['upload_id']}/download-token", headers=auth(ATTORNEY_A)
    ).json()
    token = token_body["token"]

    # flip the last signature character -> 401
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert api_client.get(f"/v1/files/download?token={tampered}").status_code == 401

    # garbage token -> 401
    assert api_client.get("/v1/files/download?token=not-a-token").status_code == 401

    # missing token -> 422 (required query param)
    assert api_client.get("/v1/files/download").status_code == 422

    # well-formed token for an upload that does not exist -> 404
    ghost_token, _ = files_service.build_download_token(uuid4(), secret=DOWNLOAD_SECRET)
    assert api_client.get(f"/v1/files/download?token={ghost_token}").status_code == 404


def test_download_token_requires_secret(db, api_client, monkeypatch):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)

    from app.core.config import get_settings

    monkeypatch.delenv("DOWNLOAD_TOKEN_SECRET")
    get_settings.cache_clear()
    try:
        # fail closed: no secret -> 501, never a minted token
        response = api_client.post(
            f"/v1/files/{intent['upload_id']}/download-token", headers=auth(ATTORNEY_A)
        )
        assert response.status_code == 501
    finally:
        monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", DOWNLOAD_SECRET)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Cross-tenant isolation (existence oracle: 404, not 403)
# ---------------------------------------------------------------------------


def test_cross_tenant_access_404(db, api_client):
    _firm_a, _household_a, matter_a = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_a)
    upload_id = intent["upload_id"]

    # second firm with its own admin
    other_admin = str(uuid4())
    create_user(db, other_admin)
    other_firm = create_firm(db, "Other Synthetic Firm")
    add_firm_member(db, other_firm, other_admin, "firm_admin")
    other = {"Authorization": f"Bearer {token(other_admin)}"}

    # intent on another firm's matter -> 404
    response = api_client.post(
        f"/v1/matters/{matter_a}/files/intent",
        json={
            "filename": "x.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "purpose": "evidence",
        },
        headers=other,
    )
    assert response.status_code == 404

    # list another firm's matter files -> 404
    response = api_client.get(f"/v1/matters/{matter_a}/files", headers=other)
    assert response.status_code == 404

    # complete another firm's upload -> 404
    response = api_client.post(
        f"/v1/files/{upload_id}/complete",
        files={"file": ("x.pdf", PDF_BYTES, "application/pdf")},
        headers=other,
    )
    assert response.status_code == 404

    # download-token for another firm's upload -> 404
    response = api_client.post(f"/v1/files/{upload_id}/download-token", headers=other)
    assert response.status_code == 404


def test_list_matter_files_scoped(db, api_client):
    firm_id, household_id, matter_a = setup_firm_household_matter(db)
    matter_b = insert_matter(db, firm_id, household_id)

    _upload_clean_file(db, api_client, ATTORNEY_A, matter_a, filename="a.pdf")
    _upload_clean_file(
        db,
        api_client,
        ATTORNEY_A,
        matter_b,
        filename="b.pdf",
        content=JPEG_BYTES,
        mime_type="image/jpeg",
    )

    items_a = api_client.get(f"/v1/matters/{matter_a}/files", headers=auth(ATTORNEY_A)).json()
    items_b = api_client.get(f"/v1/matters/{matter_b}/files", headers=auth(ATTORNEY_A)).json()
    assert [item["original_filename"] for item in items_a] == ["a.pdf"]
    assert [item["original_filename"] for item in items_b] == ["b.pdf"]


# ---------------------------------------------------------------------------
# Worker scanner units
# ---------------------------------------------------------------------------


def test_dev_scanner_magic_bytes(tmp_path):
    scanning = _worker_scanning()
    scanner = scanning.DevScanner()

    cases = {
        "application/pdf": PDF_BYTES,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DOCX_BYTES,
        "image/png": PNG_BYTES,
        "image/jpeg": JPEG_BYTES,
    }
    for mime_type, content in cases.items():
        path = tmp_path / "sample.bin"
        path.write_bytes(content)
        result = scanner.scan(path, mime_type)
        assert result.clean is True, (mime_type, result.detail)

    # every cross-combination quarantines
    mimes = list(cases)
    for i, mime_type in enumerate(mimes):
        wrong = cases[mimes[(i + 1) % len(mimes)]]
        path = tmp_path / "wrong.bin"
        path.write_bytes(wrong)
        result = scanner.scan(path, mime_type)
        assert result.clean is False, (mime_type, result.detail)
        assert "magic bytes" in result.detail

    # empty file, missing file, and unknown MIME all fail closed
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert scanner.scan(empty, "application/pdf").clean is False

    missing = tmp_path / "missing.bin"
    assert scanner.scan(missing, "application/pdf").clean is False

    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(PDF_BYTES)
    assert scanner.scan(pdf_path, "application/x-msdownload").clean is False


def test_scan_is_idempotent(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)
    upload_id = intent["upload_id"]

    # a second scan run does not rescan or flip the verdict
    scanning = _worker_scanning()
    outcome = scanning.perform_file_scan(db, UUID(upload_id))
    db.commit()
    assert outcome == {"scanned": 0, "status": "clean"}

    # scanning an unknown id is a no-op ledger, not an error
    outcome = scanning.perform_file_scan(db, uuid4())
    db.commit()
    assert outcome == {"scanned": 0, "status": "missing"}
