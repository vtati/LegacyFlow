"""Unit tests for vault AI analysis (extraction + classification).

DB-free: exercises the extraction and analysis modules directly.
"""

import io

from app.domains.files import analysis, extraction


def _make_pdf(lines: list[str]) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for line in lines:
        c.drawString(100, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def _make_docx(paragraphs: list[str]) -> bytes:
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(buf)
    return buf.getvalue()


def test_pdf_extraction():
    pdf = _make_pdf(["GENERAL WARRANTY DEED", "Grantor: Robert Smith"])
    text = extraction.extract_text(pdf, "application/pdf")
    assert text is not None
    assert "GENERAL WARRANTY DEED" in text
    assert "Robert Smith" in text


def test_docx_extraction():
    docx = _make_docx(["LAST WILL AND TESTAMENT", "of Jane Doe"])
    text = extraction.extract_text(
        docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert text is not None
    assert "LAST WILL AND TESTAMENT" in text


def test_image_extraction_unavailable():
    assert extraction.extract_text(b"\x89PNG\r\n", "image/png") is None
    assert extraction.extract_text(b"\xff\xd8\xff", "image/jpeg") is None


def test_extraction_truncates():
    long_text = "word " * 10000
    pdf = _make_pdf([long_text])
    text = extraction.extract_text(pdf, "application/pdf")
    assert text is not None
    assert len(text) <= extraction.MAX_EXTRACT_CHARS


def test_classify_deed():
    result = analysis.analyze_with_llm(
        "GENERAL WARRANTY DEED Grantor: Robert Smith  Grantee: Mary Smith  "
        "Property address: 456 Elm Street, Fairfax, Virginia 22030",
        "deed.pdf",
    )
    assert result["document_type"] == "deed"
    labels = {f["label"] for f in result["key_facts"]}
    assert "Property address" in labels
    assert "Grantor" in labels
    assert "Grantee" in labels
    assert "deed" in result["suggested_tags"]
    assert 0.0 <= result["confidence"] <= 1.0


def test_classify_will():
    result = analysis.analyze_with_llm(
        "LAST WILL AND TESTAMENT of John Doe. I appoint Jane Doe as Executor.",
        "will.pdf",
    )
    assert result["document_type"] == "will"


def test_classify_trust():
    result = analysis.analyze_with_llm(
        "DECLARATION OF REVOCABLE LIVING TRUST known as the Smith Family Trust. "
        "I, John Smith, of Fairfax, Virginia.",
        "trust.pdf",
    )
    assert result["document_type"] == "trust"


def test_classify_insurance():
    result = analysis.analyze_with_llm(
        "Life insurance policy. Carrier: Acme Life. Policy number: AB-12345. "
        "Face amount: $500,000.",
        "policy.pdf",
    )
    assert result["document_type"] == "life_insurance"
    labels = {f["label"] for f in result["key_facts"]}
    assert "Carrier" in labels
    assert "Face amount" in labels


def test_classify_unknown_is_other():
    result = analysis.analyze_with_llm(
        "Meeting notes about the quarterly picnic planning committee.",
        "notes.pdf",
    )
    assert result["document_type"] == "other"


def test_normalize_rejects_bad_type():
    out = analysis._normalize(
        {"document_type": "not-a-real-type", "summary": "x", "key_facts": [], "suggested_tags": []},
        "f.pdf",
    )
    assert out["document_type"] == "other"


def test_trust_template_renders():
    """The VA trust template merges and renders through both pipelines."""
    import re
    from pathlib import Path

    from app.domains.documents.service import render_demo_docx, render_demo_pdf

    template_path = (
        Path(__file__).parent.parent
        / "content-packs"
        / "templates"
        / "us-va"
        / "v0"
        / "revocable-living-trust.txt"
    )
    assert template_path.exists(), "trust template file missing"
    body = template_path.read_text(encoding="utf-8")
    assert body.startswith("DRAFT — requires licensed attorney review")
    assert "ARTICLE VI" in body
    assert "64.2-700" in body  # Virginia Uniform Trust Code citation

    ctx = {
        "trust_name": "Test Family Trust",
        "grantor_name": "Test Grantor",
        "grantor_address": "1 Main St",
        "grantor_city": "Fairfax",
        "trustee_name": "Test Grantor",
        "successor_trustee_name": "Test Successor",
        "initial_property": "123 Main St",
        "marital_share": "One-half to spouse.",
        "children_list": "Kid One, Kid Two",
        "distribution_age": "25",
        "today": "October 2, 2026",
        # Batch C1 (FUNC-026): Article V is ONE composed slot now —
        # build_template_context always sets it, composing the marital
        # share, the children's shares, and the minors'/contingent
        # tail with sequential numbering. This value mirrors what the
        # builder emits for the facts above (married, two children,
        # distribution age 25); without the slot the merge leaves
        # {{trust_distribution_provisions}} behind.
        "trust_distribution_provisions": (
            "5.2 Marital share. One-half to spouse.\n\n"
            "5.3 Shares for children. The remaining trust property "
            "shall be divided into equal shares for my children:\n"
            "Kid One, Kid Two\n\n"
            "5.4 Minors' shares in continuing trust. The share of any "
            "child who has not attained the age of 25 shall be held in "
            "a separate continuing trust for that child. The Trustee "
            "shall apply so much of the income and principal as the "
            "Trustee deems advisable for the child's health, education, "
            "maintenance, and support until the child attains the age "
            "of 25, at which time the remaining balance shall be "
            "distributed outright to the child, free of trust.\n\n"
            "5.5 Contingent beneficiaries. If any child fails to "
            "survive me, that child's share shall be distributed to "
            "that child's then-living descendants, per stirpes, or if "
            "none, to my remaining children in equal shares."
        ),
    }
    merged = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx.get(m.group(1), m.group(0)), body)
    assert not re.search(r"\{\{\w+\}\}", merged), "unfilled placeholders remain"

    docx_bytes = render_demo_docx("Trust — Demo", merged, "DEMO ONLY")
    pdf_bytes = render_demo_pdf("Trust — Demo", merged, "DEMO ONLY")
    assert docx_bytes[:4] == b"PK\x03\x04"  # zip = docx
    assert pdf_bytes[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# F3 (2026-10-26): analysis provenance — model output is never again
# indistinguishable from the rule-based fallback, in the result dict,
# in storage, or in the API payloads.
# ---------------------------------------------------------------------------

import json as _json

import pytest as _pytest


@_pytest.fixture()
def _settings_env(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_rules_fallback_reports_rules_source(_settings_env):
    result = analysis.analyze_with_llm("GENERAL WARRANTY DEED Grantor: A B", "deed.pdf")
    assert result["source"] == "rules"


def test_model_path_reports_model_source(_settings_env, monkeypatch):
    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class _FakeJsonProvider:
        def complete_json(self, **kwargs):
            return (
                _json.dumps(
                    {
                        "document_type": "deed",
                        "summary": "A deed.",
                        "key_facts": [],
                        "suggested_tags": ["deed"],
                        "confidence": 0.9,
                    }
                ),
                {},
            )

    monkeypatch.setattr(analysis, "get_json_provider", lambda model=None: _FakeJsonProvider())
    result = analysis.analyze_with_llm("deed text", "deed.pdf")
    assert result["document_type"] == "deed"
    assert result["source"] == "model"


def test_model_failure_falls_back_to_rules_and_says_so(_settings_env, monkeypatch):
    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from app.core.config import get_settings

    get_settings.cache_clear()

    def _boom(model=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(analysis, "get_json_provider", _boom)
    result = analysis.analyze_with_llm("GENERAL WARRANTY DEED Grantor: A B", "deed.pdf")
    assert result["source"] == "rules"


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        self.conn.last_sql = sql

    def fetchone(self):
        if "SELECT tags" in (self.conn.last_sql or ""):
            return {"tags": ["user-tag"]}
        return {"tags": ["user-tag", "deed", "real-estate"]}


class _FakeConn:
    def __init__(self):
        self.statements = []
        self.last_sql = None

    def cursor(self):
        return _FakeCursor(self)


def test_update_analysis_persists_provenance():
    from app.domains.files import repository as files_repo

    conn = _FakeConn()
    files_repo.update_analysis(
        conn,
        "00000000-0000-0000-0000-000000000001",
        document_type="deed",
        ai_summary="A deed.",
        key_facts=[],
        suggested_tags=["deed", "real-estate"],
        confidence=0.9,
        analysis_source="model",
    )
    update_sql, params = conn.statements[-1]
    assert "analysis_source" in update_sql
    assert "ai_suggested_tags" in update_sql
    assert "model" in params
    # The analysis-time suggestions are recorded separately from the
    # merged tag list: that separation IS the tag provenance.
    assert _json.loads(params[3]) == ["user-tag", "deed", "real-estate"]  # tags merged
    assert params[5] == "model"  # analysis_source
    assert _json.loads(params[6]) == ["deed", "real-estate"]  # ai_suggested_tags


def test_analysis_payloads_expose_provenance():
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.domains.files.schemas import FileAnalysisOut, FileUploadOut

    analysis_out = FileAnalysisOut(
        upload_id=uuid4(),
        document_type="deed",
        summary="A deed.",
        key_facts=[],
        suggested_tags=["deed"],
        confidence=0.9,
        analyzed_at=datetime.now(UTC),
        analysis_source="model",
    )
    assert analysis_out.analysis_source == "model"

    upload_out = FileUploadOut(
        id=uuid4(),
        firm_id=uuid4(),
        household_id=uuid4(),
        matter_id=None,
        uploader_user_id=uuid4(),
        original_filename="deed.pdf",
        stored_path="client-uploads/x.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        scan_status="clean",
        scan_detail=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        analysis_source="rules",
        ai_suggested_tags=["deed"],
    )
    assert upload_out.analysis_source == "rules"
    assert upload_out.ai_suggested_tags == ["deed"]
