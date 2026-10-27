"""F7 (2026-10-26): OCR-needed-but-unreadable uploads say so explicitly.

Before: a scanned PDF with no text layer degraded silently — the
interview extraction reported only 'no_readable_text' with no record
that OCR was needed and unavailable, and a vault upload recorded the
generic 'failed' with no user-visible state at all. Now:

* interview text extraction reports ocr_attempted / ocr_unavailable;
* the extraction API result carries both flags;
* vault files get a distinct extraction_status 'unreadable_scan' and
  the file list shows a "couldn't read this scan" state.
"""

from __future__ import annotations

import io

import pytest

from app.domains.files import extraction as file_extraction
from app.domains.interview import text_extract


def _make_pdf(draw_text: bool) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    if draw_text:
        c.drawString(100, 750, "LAST WILL AND TESTAMENT of Jane Doe")
    # A real scanned PDF has a page with no text layer; showPage emits
    # that blank page (a bare save() would produce a zero-page file).
    c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Interview path: OCR flags on the extracted document
# ---------------------------------------------------------------------------


def test_text_pdf_never_attempts_ocr() -> None:
    doc = text_extract.extract_text(_make_pdf(True), "will.pdf")
    assert doc.has_readable_text is True
    assert doc.ocr_attempted is False
    assert doc.ocr_unavailable is False


def test_scan_without_tesseract_reports_ocr_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(text_extract.shutil, "which", lambda _name: None)
    doc = text_extract.extract_text(_make_pdf(False), "scan.pdf")
    assert doc.has_readable_text is False
    assert doc.ocr_attempted is True
    assert doc.ocr_unavailable is True
    assert doc.ocr_used is False


def test_scan_with_working_ocr_is_not_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ocr(data: bytes, page_count: int):
        return [text_extract.ExtractedPage(page_number=1, text="Scanned will text")]

    monkeypatch.setattr(text_extract, "_ocr_pdf_pages", fake_ocr)
    doc = text_extract.extract_text(_make_pdf(False), "scan.pdf")
    assert doc.has_readable_text is True
    assert doc.ocr_attempted is True
    assert doc.ocr_unavailable is False
    assert doc.ocr_used is True


def test_extraction_result_and_api_schema_carry_ocr_flags() -> None:
    import dataclasses

    from app.domains.interview.extraction import ExtractionResult
    from app.domains.interview.schemas import ExtractionOut

    result_fields = {f.name for f in dataclasses.fields(ExtractionResult)}
    assert {"ocr_attempted", "ocr_unavailable"} <= result_fields
    assert {"ocr_attempted", "ocr_unavailable"} <= set(ExtractionOut.model_fields)


# ---------------------------------------------------------------------------
# Vault path: a distinct unreadable-scan status
# ---------------------------------------------------------------------------


def test_files_text_pdf_is_done() -> None:
    text, status = file_extraction.extract_text_with_status(
        _make_pdf(True), "application/pdf"
    )
    assert status == "done"
    assert text and "Jane Doe" in text


def test_files_blank_pdf_is_unreadable_scan() -> None:
    text, status = file_extraction.extract_text_with_status(
        _make_pdf(False), "application/pdf"
    )
    assert status == "unreadable_scan"
    assert text == ""


def test_files_corrupt_pdf_is_failed_not_scan() -> None:
    text, status = file_extraction.extract_text_with_status(
        b"%PDF-1.4 not really a pdf at all", "application/pdf"
    )
    assert status == "failed"


def test_files_image_is_unavailable() -> None:
    text, status = file_extraction.extract_text_with_status(b"\x89PNG", "image/png")
    assert status == "unavailable"
    assert text is None


def test_files_service_records_the_scan_status() -> None:
    import inspect

    from app.domains.files import service

    assert "extract_text_with_status" in inspect.getsource(service.complete_upload)


def test_files_encrypted_pdf_is_failed_not_scan() -> None:
    """Review M-1: a password-protected PDF parses and has pages, but it
    is locked — not a scan. It must not be labeled 'unreadable_scan'
    (the UI would wrongly tell the user it looks like a scanned image)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(_make_pdf(True)))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("test-password")
    buf = io.BytesIO()
    writer.write(buf)
    text, status = file_extraction.extract_text_with_status(
        buf.getvalue(), "application/pdf"
    )
    assert status == "failed"
    assert text == ""
