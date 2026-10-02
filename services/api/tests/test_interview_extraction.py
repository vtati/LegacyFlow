"""Phase 2/3 tests: document extraction, consistency review, form leverage.

Runs against a local pgserver instance with all migrations and jurisdiction
seeds applied — NEVER against DATABASE_URL or live Supabase. All fixture
people, firms, households, and documents are synthetic.

Covers:
  * text extraction (pdf/docx/unsupported/scanned)
  * extraction value validation + prompt-injection hardening
  * extraction API: proposals with document provenance, confirm -> intake
  * consistency review: questions-only findings, idempotent escalations
  * forms: verified facts pre-fill templates; unmapped placeholders flagged
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.core import crypto
from app.domains.documents import service as documents_service
from app.domains.interview import consistency as consistency_module
from app.domains.interview import extraction as extraction_module
from app.domains.interview import text_extract
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_SECRET, mint_token

ADMIN_SUB = "aaaaaaaa-1111-1111-1111-111111111111"
CLIENT_SUB = "dddddddd-4444-4444-4444-444444444444"
OUTSIDER_SUB = "99999999-9999-9999-9999-999999999999"

USERS = {
    ADMIN_SUB: "admin@example.test",
    CLIENT_SUB: "client@example.test",
    OUTSIDER_SUB: "outsider@example.test",
}

US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

VA_SNAPSHOT = {
    "pack_id": "us-va",
    "jurisdiction_code": "US-VA",
    "semantic_version": "0.1.0",
}

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"

TRUNCATE_TABLES = (
    "interview_consistency_reviews, interview_extractions,"
    " interview_turn_bodies, interview_turns, field_proposals, ai_traces,"
    " interview_sessions, audit_events, escalations, intake_answers,"
    " document_versions, document_packages, document_templates,"
    " matter_status_history, matters, invitations, nominations,"
    " planning_decisions, relationships, persons, household_members,"
    " households, firm_memberships, firms, feature_flags, profiles, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text, raw_user_meta_data jsonb);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT current_setting('app.test_uid', true)::uuid $$;
"""


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-extract")))
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
def api_client(pg_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "<redacted>")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "gpt-test")
    monkeypatch.setenv("INTERVIEW_TURN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core.config import get_settings

    get_settings.cache_clear()
    crypto.reset_fernet_cache()

    from app.main import create_app

    app = create_app()
    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()
    crypto.reset_fernet_cache()


@pytest.fixture(autouse=True)
def _unit_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-synthetic")
    monkeypatch.setenv("INTERVIEW_TURN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core.config import get_settings

    get_settings.cache_clear()
    crypto.reset_fernet_cache()
    yield
    get_settings.cache_clear()
    crypto.reset_fernet_cache()


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def _fetchone(db, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def create_user(db, sub: str) -> None:
    email = USERS[sub]
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (sub, email))
    # The on_auth_user_created trigger provisions the profiles row; set the
    # synthetic display name via update instead of a second insert.
    _exec(
        db,
        "UPDATE profiles SET email = %s, display_name = %s WHERE id = %s",
        (email, f"Synthetic {email}", sub),
    )


def create_firm(db, name: str = "Synthetic Firm") -> str:
    firm_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO firms (id, legal_name, display_name, status) VALUES (%s, %s, %s, 'active')",
        (firm_id, name, name),
    )
    return firm_id


def _setup(db) -> dict[str, str]:
    """Firm + household + matter + interviewer flag; returns ids."""
    for sub in (ADMIN_SUB, CLIENT_SUB, OUTSIDER_SUB):
        create_user(db, sub)
    firm_id = create_firm(db)
    _exec(
        db,
        "INSERT INTO firm_memberships (firm_id, user_id, role, status)"
        " VALUES (%s, %s, 'firm_admin', 'active')",
        (firm_id, ADMIN_SUB),
    )
    household_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
        " VALUES (%s, %s, %s, %s)",
        (household_id, firm_id, "Synthetic Household", ADMIN_SUB),
    )
    _exec(
        db,
        "INSERT INTO household_members (household_id, user_id, relationship_role, access_status)"
        " VALUES (%s, %s, 'primary_client', 'active')",
        (household_id, CLIENT_SUB),
    )
    matter_id = str(uuid4())
    _exec(
        db,
        "INSERT INTO matters"
        " (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,"
        "  plan_type, status, jurisdiction_snapshot, completeness_pct)"
        " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s::jsonb, 0.0)",
        (
            matter_id,
            firm_id,
            household_id,
            US_VA_JURISDICTION_ID,
            US_VA_PACK_ID,
            json.dumps(VA_SNAPSHOT),
        ),
    )
    _exec(
        db,
        "INSERT INTO feature_flags (key, scope, scope_ref, is_enabled)"
        " VALUES ('ai_interviewer', 'firm', %s, true)",
        (firm_id,),
    )
    return {"firm_id": firm_id, "household_id": household_id, "matter_id": matter_id}


def _headers(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub)}"}


def _start_session(api_client: TestClient, matter_id: str) -> str:
    resp = api_client.post(
        f"/v1/matters/{matter_id}/interview/sessions",
        json={"question_set_id": "household"},
        headers=_headers(CLIENT_SUB),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _make_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for line in text.split("\n"):
        c.drawString(72, y, line)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_docx(paragraphs: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# text_extract unit tests
# ---------------------------------------------------------------------------


class TestTextExtract:
    def test_pdf_per_page_text(self) -> None:
        doc = text_extract.extract_text(
            _make_pdf("Marital status: married\nState: Virginia"), "will.pdf"
        )
        assert doc.file_kind == "pdf"
        assert doc.has_readable_text
        assert "married" in doc.full_text
        assert "[page 1]" in doc.full_text

    def test_docx_text(self) -> None:
        doc = text_extract.extract_text(
            _make_docx(["Marital status: single", "State: Virginia"]), "notes.docx"
        )
        assert doc.file_kind == "docx"
        assert doc.has_readable_text
        assert "single" in doc.full_text

    def test_unsupported_type(self) -> None:
        with pytest.raises(text_extract.UnsupportedDocumentError):
            text_extract.extract_text(b"hello", "notes.txt")

    def test_scanned_pdf_no_readable_text(self) -> None:
        doc = text_extract.extract_text(_make_blank_pdf(), "scan.pdf")
        assert not doc.has_readable_text

    def test_oversize_rejected(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            text_extract.extract_text(b"x" * (text_extract.MAX_BYTES + 1), "big.pdf")


def _make_scanned_pdf(text: str) -> bytes:
    """Image-only PDF with no text layer (simulates a scan)."""
    import pymupdf
    from PIL import Image, ImageDraw

    img = Image.new("L", (1200, 400), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((40, 40), text, fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=200)
    page.insert_image(page.rect, stream=buf.getvalue())
    out = doc.tobytes()
    doc.close()
    return out


class TestOcrFallback:
    @pytest.mark.skipif(not text_extract._tesseract_available(), reason="tesseract not installed")
    def test_scanned_pdf_ocr_reads_text(self) -> None:
        doc = text_extract.extract_text(_make_scanned_pdf("Marital status: married"), "scan.pdf")
        assert doc.ocr_used
        assert doc.has_readable_text
        assert "married" in doc.full_text.lower()

    def test_ocr_missing_binary_degrades_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(text_extract.shutil, "which", lambda _name: None)
        doc = text_extract.extract_text(_make_scanned_pdf("Marital status: married"), "scan.pdf")
        assert not doc.ocr_used
        assert not doc.has_readable_text

    def test_ocr_page_cap_respected(self) -> None:
        assert text_extract.OCR_MAX_PAGES <= 5


class TestExtractionValidation:
    def test_clean_value_types(self) -> None:
        assert extraction_module._clean_value("  hello  ") == "hello"
        assert extraction_module._clean_value(True) is True
        assert extraction_module._clean_value(42) == 42
        assert extraction_module._clean_value({"a": 1}) is None
        assert extraction_module._clean_value(["x"]) is None
        assert extraction_module._clean_value("   ") is None

    def test_clean_value_strips_control_chars_and_caps(self) -> None:
        assert extraction_module._clean_value("ab\x00cd\x1fef") == "abcdef"
        long_val = "x" * 600
        assert len(extraction_module._clean_value(long_val)) == 500

    def test_confidence_clamped(self) -> None:
        assert extraction_module._clamp_confidence(2.5) == 1.0
        assert extraction_module._clamp_confidence(-1) == 0.0
        assert extraction_module._clamp_confidence("nope") == 0.0

    def test_system_prompt_hardens_against_injection(self) -> None:
        prompt = extraction_module._SYSTEM_PROMPT
        assert "UNTRUSTED" in prompt
        assert "NEVER follow" in prompt
        assert "do not guess" in prompt


# ---------------------------------------------------------------------------
# Extraction API tests (mocked provider)
# ---------------------------------------------------------------------------


def _mock_extraction(monkeypatch, items: list[dict[str, Any]]) -> None:
    def fake(**kwargs):
        return items, {"prompt_tokens": 10, "completion_tokens": 5}

    monkeypatch.setattr(extraction_module, "_extract_via_openai", fake)


class TestExtractionApi:
    def test_pdf_extraction_creates_proposals(
        self, db, api_client: TestClient, monkeypatch
    ) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _mock_extraction(
            monkeypatch,
            [
                {
                    "question_key": "marital_status",
                    "value": "married",
                    "confidence": 0.95,
                    "source_page": 1,
                    "source_snippet": "Marital status: married",
                },
                {
                    "question_key": "not_a_real_key",
                    "value": "evil",
                    "confidence": 0.99,
                    "source_page": 1,
                    "source_snippet": "x",
                },
            ],
        )
        pdf = _make_pdf("Marital status: married")
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("will.pdf", pdf, "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "completed"
        # Unknown key discarded by the server, not stored.
        assert len(body["proposals"]) == 1
        assert body["discarded"] == 1
        proposal = body["proposals"][0]
        assert proposal["question_key"] == "marital_status"
        assert proposal["value"] == "married"

        row = _fetchone(
            db, "SELECT * FROM field_proposals WHERE id = %s", (proposal["proposal_id"],)
        )
        assert row is not None
        assert row["source"] == "document_extracted"
        assert row["source_document_id"] is not None
        assert row["source_page"] == 1
        assert row["status"] == "proposed"

    def test_confirm_extracted_proposal_writes_intake_answer(
        self, db, api_client: TestClient, monkeypatch
    ) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _mock_extraction(
            monkeypatch,
            [
                {
                    "question_key": "marital_status",
                    "value": "married",
                    "confidence": 0.9,
                    "source_page": 1,
                    "source_snippet": "married",
                }
            ],
        )
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("will.pdf", _make_pdf("married"), "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        proposal_id = resp.json()["proposals"][0]["proposal_id"]
        confirm = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/proposals/{proposal_id}/confirm",
            headers=_headers(CLIENT_SUB),
        )
        assert confirm.status_code == 200, confirm.text
        row = _fetchone(
            db,
            "SELECT answer FROM intake_answers WHERE matter_id = %s AND question_key = %s",
            (ids["matter_id"], "marital_status"),
        )
        assert row is not None
        assert json.loads(json.dumps(row["answer"])) == "married"

    def test_docx_extraction(self, db, api_client: TestClient, monkeypatch) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _mock_extraction(
            monkeypatch,
            [
                {
                    "question_key": "primary_residence_state",
                    "value": "Virginia",
                    "confidence": 0.88,
                    "source_page": 1,
                    "source_snippet": "State: Virginia",
                }
            ],
        )
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={
                "file": (
                    "notes.docx",
                    _make_docx(["State: Virginia"]),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["proposals"][0]["question_key"] == "primary_residence_state"

    def test_unsupported_file_type_rejected(self, db, api_client: TestClient, monkeypatch) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code in (400, 422), resp.text

    def test_scanned_pdf_reports_no_readable_text(
        self, db, api_client: TestClient, monkeypatch
    ) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        called = []
        _mock_extraction(monkeypatch, [])
        orig = extraction_module._extract_via_openai

        def spy(**kwargs):
            called.append(True)
            return orig(**kwargs)

        monkeypatch.setattr(extraction_module, "_extract_via_openai", spy)
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("scan.pdf", _make_blank_pdf(), "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "no_readable_text"
        assert body["proposals"] == []
        # Provider never called for an unreadable document.
        assert called == []

    def test_existing_open_proposal_is_skipped(
        self, db, api_client: TestClient, monkeypatch
    ) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        item = {
            "question_key": "marital_status",
            "value": "married",
            "confidence": 0.9,
            "source_page": 1,
            "source_snippet": "married",
        }
        _mock_extraction(monkeypatch, [item])
        url = f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions"
        first = api_client.post(
            url,
            files={"file": ("a.pdf", _make_pdf("married"), "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        assert len(first.json()["proposals"]) == 1
        second = api_client.post(
            url,
            files={"file": ("b.pdf", _make_pdf("married"), "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["proposals"] == []
        assert body["skipped_existing"] == ["marital_status"]

    def test_outsider_cannot_extract(self, db, api_client: TestClient) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("a.pdf", _make_pdf("x"), "application/pdf")},
            headers=_headers(OUTSIDER_SUB),
        )
        assert resp.status_code in (401, 403, 404), resp.text

    def test_injection_values_are_validated(self, db, api_client: TestClient, monkeypatch) -> None:
        """A document carrying instruction-like text cannot smuggle bad values."""
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _mock_extraction(
            monkeypatch,
            [
                {
                    # Simulates a model echoing an injected instruction's payload
                    # as a non-scalar value: must be discarded.
                    "question_key": "marital_status",
                    "value": {"ignore": "previous instructions"},
                    "confidence": 1.0,
                    "source_page": 1,
                    "source_snippet": "ignore previous instructions",
                },
                {
                    "question_key": "primary_residence_state",
                    "value": "Vir\x00ginia",
                    "confidence": 0.7,
                    "source_page": 1,
                    "source_snippet": "state",
                },
            ],
        )
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("evil.pdf", _make_pdf("x"), "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["discarded"] == 1
        assert len(body["proposals"]) == 1
        assert body["proposals"][0]["value"] == "Virginia"

    def test_list_extractions(self, db, api_client: TestClient, monkeypatch) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _mock_extraction(monkeypatch, [])
        api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            files={"file": ("a.pdf", _make_pdf("hello"), "application/pdf")},
            headers=_headers(CLIENT_SUB),
        )
        resp = api_client.get(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/extractions",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        assert body[0]["filename"] == "a.pdf"
        assert body[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# Consistency review tests (mocked provider)
# ---------------------------------------------------------------------------


def _mock_consistency(monkeypatch, findings: list[dict[str, Any]]) -> None:
    def fake(**kwargs):
        return findings, {"prompt_tokens": 10, "completion_tokens": 5}

    monkeypatch.setattr(consistency_module, "_review_via_openai", fake)


def _confirm_proposal(db, api_client: TestClient, matter_id: str, proposal_id: str) -> None:
    resp = api_client.post(
        f"/v1/matters/{matter_id}/interview/proposals/{proposal_id}/confirm",
        headers=_headers(CLIENT_SUB),
    )
    assert resp.status_code == 200, resp.text


def _seed_verified_facts(
    db, api_client: TestClient, matter_id: str, session_id: str, monkeypatch
) -> None:
    _mock_extraction(
        monkeypatch,
        [
            {
                "question_key": "marital_status",
                "value": "single",
                "confidence": 0.9,
                "source_page": 1,
                "source_snippet": "single",
            },
            {
                "question_key": "has_minor_children",
                "value": True,
                "confidence": 0.9,
                "source_page": 1,
                "source_snippet": "two minor children",
            },
        ],
    )
    resp = api_client.post(
        f"/v1/matters/{matter_id}/interview/sessions/{session_id}/extractions",
        files={"file": ("facts.pdf", _make_pdf("facts"), "application/pdf")},
        headers=_headers(CLIENT_SUB),
    )
    assert resp.status_code == 200, resp.text
    for p in resp.json()["proposals"]:
        _confirm_proposal(db, api_client, matter_id, p["proposal_id"])


class TestConsistencyReview:
    def test_finding_becomes_attorney_question(
        self, db, api_client: TestClient, monkeypatch
    ) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _seed_verified_facts(db, api_client, ids["matter_id"], session_id, monkeypatch)
        _mock_consistency(
            monkeypatch,
            [
                {
                    "question": (
                        "Marital status is single but minor children were listed — "
                        "could you clarify guardianship plans?"
                    ),
                    "fields": ["marital_status", "has_minor_children"],
                    "severity": "review",
                }
            ],
        )
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/consistency-review",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["facts_considered"] == 2
        assert len(body["findings"]) == 1
        assert body["escalations_created"] == 1
        row = _fetchone(
            db,
            "SELECT * FROM escalations WHERE matter_id = %s"
            " AND rule_id LIKE 'interview_consistency:%%'",
            (ids["matter_id"],),
        )
        assert row is not None
        assert row["outcome"] == "attorney_review"
        snapshot = row["facts_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        assert "clarify" in snapshot["question"].lower()

    def test_non_question_findings_discarded(self, db, api_client: TestClient, monkeypatch) -> None:
        """Findings not phrased as questions (verdicts) are dropped server-side."""
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _seed_verified_facts(db, api_client, ids["matter_id"], session_id, monkeypatch)
        _mock_consistency(
            monkeypatch,
            [
                {
                    "question": "The marital status is inconsistent with the facts.",
                    "fields": ["marital_status"],
                    "severity": "review",
                }
            ],
        )
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/consistency-review",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["findings"] == []
        assert body["escalations_created"] == 0

    def test_unknown_fields_dropped(self, db, api_client: TestClient, monkeypatch) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _seed_verified_facts(db, api_client, ids["matter_id"], session_id, monkeypatch)
        _mock_consistency(
            monkeypatch,
            [
                {
                    "question": "Is this a real tension?",
                    "fields": ["made_up_key"],
                    "severity": "review",
                }
            ],
        )
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/consistency-review",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.json()["findings"] == []

    def test_rerun_is_idempotent(self, db, api_client: TestClient, monkeypatch) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        _seed_verified_facts(db, api_client, ids["matter_id"], session_id, monkeypatch)
        finding = {
            "question": "Could you clarify the guardianship plans?",
            "fields": ["has_minor_children"],
            "severity": "review",
        }
        _mock_consistency(monkeypatch, [finding])
        url = f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/consistency-review"
        first = api_client.post(url, headers=_headers(CLIENT_SUB))
        second = api_client.post(url, headers=_headers(CLIENT_SUB))
        assert first.json()["escalations_created"] == 1
        assert second.json()["escalations_created"] == 0
        rows = _fetchone(
            db,
            "SELECT count(*) AS n FROM escalations WHERE matter_id = %s"
            " AND rule_id LIKE 'interview_consistency:%%'",
            (ids["matter_id"],),
        )
        assert rows is not None and int(rows["n"]) == 1

    def test_no_facts_no_provider_call(self, db, api_client: TestClient, monkeypatch) -> None:
        ids = _setup(db)
        session_id = _start_session(api_client, ids["matter_id"])
        called = []

        def spy(**kwargs):
            called.append(True)
            return [], {}

        monkeypatch.setattr(consistency_module, "_review_via_openai", spy)
        resp = api_client.post(
            f"/v1/matters/{ids['matter_id']}/interview/sessions/{session_id}/consistency-review",
            headers=_headers(CLIENT_SUB),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["findings"] == []
        assert body["facts_considered"] == 0
        assert called == []


# ---------------------------------------------------------------------------
# Form leverage tests
# ---------------------------------------------------------------------------


class TestFormLeverage:
    def test_verified_facts_prefill_render_context(self, db) -> None:
        ids = _setup(db)
        matter = _fetchone(db, "SELECT * FROM matters WHERE id = %s", (ids["matter_id"],))
        assert matter is not None
        _exec(
            db,
            "INSERT INTO intake_answers (matter_id, question_key, answer)"
            " VALUES (%s, %s, %s::jsonb)",
            (ids["matter_id"], "marital_status", json.dumps("married")),
        )
        context = documents_service._render_context(db, matter)
        assert context["marital_status"] == "married"
        # Curated keys still win.
        assert context["household_name"] == "Synthetic Household"

    def test_unfilled_placeholders_detected(self) -> None:
        body = documents_service.render_template_content(
            "Hello {{household_name}}, status {{marital_status}}, {{unknown_field}}.",
            {"household_name": "Smiths", "marital_status": "married"},
        )
        assert documents_service.unfilled_placeholders(body) == ["unknown_field"]

    def test_unmapped_placeholder_becomes_attorney_question(self, db) -> None:
        ids = _setup(db)
        matter = _fetchone(db, "SELECT * FROM matters WHERE id = %s", (ids["matter_id"],))
        assert matter is not None
        # generate_demo flags placeholders on the merged body, not the raw template.
        context = documents_service._render_context(db, matter)
        body = documents_service.render_template_content(
            "Hello {{household_name}}. Unmapped: {{mystery_field}}.", context
        )
        assert documents_service.unfilled_placeholders(body) == ["mystery_field"]
        documents_service._flag_unfilled_placeholders(
            db, matter=matter, body=body, template_name="test-template"
        )
        row = _fetchone(
            db,
            "SELECT * FROM escalations WHERE matter_id = %s AND rule_id = %s",
            (ids["matter_id"], "document_unmapped_placeholder:test-template:mystery_field"),
        )
        assert row is not None
        assert row["outcome"] == "attorney_review"
        # Idempotent on re-run.
        documents_service._flag_unfilled_placeholders(
            db, matter=matter, body=body, template_name="test-template"
        )
        count = _fetchone(
            db,
            "SELECT count(*) AS n FROM escalations WHERE matter_id = %s"
            " AND rule_id LIKE 'document_unmapped_placeholder:%%'",
            (ids["matter_id"],),
        )
        assert count is not None and int(count["n"]) == 1
