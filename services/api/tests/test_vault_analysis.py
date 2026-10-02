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
    }
    merged = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx.get(m.group(1), m.group(0)), body)
    assert not re.search(r"\{\{\w+\}\}", merged), "unfilled placeholders remain"

    docx_bytes = render_demo_docx("Trust — Demo", merged, "DEMO ONLY")
    pdf_bytes = render_demo_pdf("Trust — Demo", merged, "DEMO ONLY")
    assert docx_bytes[:4] == b"PK\x03\x04"  # zip = docx
    assert pdf_bytes[:5] == b"%PDF-"
