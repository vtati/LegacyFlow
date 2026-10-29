"""FUNC-015 (jurisdiction-local ``today``) + FUNC-018 (no trailing
blank PDF page)."""
from __future__ import annotations

import io
from datetime import UTC, datetime

from pypdf import PdfReader

from app.domains.documents import service as documents_service


def _page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


# ---------------------------------------------------------------------------
# FUNC-015
# ---------------------------------------------------------------------------


def test_today_uses_jurisdiction_timezone_at_boundary():
    # 2027-01-01 04:30 UTC is still 2026-12-31 23:30 in America/New_York.
    instant = datetime(2027, 1, 1, 4, 30, tzinfo=UTC)
    assert documents_service._today_for_jurisdiction("US-VA", now=instant) == (
        "December 31, 2026"
    )
    # And just after ET midnight it rolls to Jan 1.
    instant = datetime(2027, 1, 1, 5, 30, tzinfo=UTC)
    assert documents_service._today_for_jurisdiction("US-VA", now=instant) == (
        "January 01, 2027"
    )


def test_today_defaults_to_eastern_for_unknown_jurisdiction():
    instant = datetime(2027, 1, 1, 4, 30, tzinfo=UTC)
    assert documents_service._today_for_jurisdiction("US-XX", now=instant) == (
        "December 31, 2026"
    )


# ---------------------------------------------------------------------------
# FUNC-018
# ---------------------------------------------------------------------------


def test_render_demo_pdf_short_body_is_one_page():
    pdf_bytes = documents_service.render_demo_pdf(
        "Synthetic Title", "A short body paragraph.", "DEMO ONLY"
    )
    assert _page_count(pdf_bytes) == 1


def test_render_demo_pdf_no_trailing_blank_page():
    # A body sized to spill onto a second page must produce exactly two
    # pages — the page-break logic must not append a third, blank page.
    paragraph = " ".join(["word"] * 60)
    body = "\n\n".join([paragraph] * 40)
    pdf_bytes = documents_service.render_demo_pdf("Synthetic Title", body, None)
    count = _page_count(pdf_bytes)
    assert count >= 2
    # The final page must carry text (not be a blank chrome-only page).
    reader = PdfReader(io.BytesIO(pdf_bytes))
    last_text = reader.pages[-1].extract_text() or ""
    assert "word" in last_text
