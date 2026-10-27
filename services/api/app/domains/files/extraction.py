"""Text extraction for uploaded files (vault AI analysis).

Supported:
* ``application/pdf`` — pypdf page text
* ``application/vnd.openxmlformats-officedocument.wordprocessingml.document``
  — python-docx paragraph text
* images (png/jpeg) — no OCR; returns ``None`` so the caller records
  ``extraction_status='unavailable'`` instead of fabricating text.

Extracted text is truncated to :data:`MAX_EXTRACT_CHARS` characters.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

MAX_EXTRACT_CHARS = 20_000

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg"})


def extract_text(content: bytes, mime_type: str) -> str | None:
    """Return extracted text, or ``None`` when extraction is unavailable.

    Raises no exceptions for corrupt files — callers treat an empty/failed
    extraction as ``extraction_status='failed'``.
    """
    text, _status = extract_text_with_status(content, mime_type)
    return text


def extract_text_with_status(content: bytes, mime_type: str) -> tuple[str | None, str]:
    """Return (extracted text, extraction_status) — F7.

    Statuses: ``done`` (text extracted), ``unavailable`` (type has no
    extractor — images), ``unreadable_scan`` (the file parsed but holds
    no readable text: a scanned/image-only document; the vault does not
    OCR, so the user must hear "couldn't read this scan" rather than
    silence), ``failed`` (corrupt/unparseable file, or an empty DOCX).
    """
    normalized = (mime_type or "").strip().lower()
    if normalized == PDF_MIME:
        text = _extract_pdf(content)
        if text and text.strip():
            collapsed = " ".join(text.split())
            return collapsed[:MAX_EXTRACT_CHARS], "done"
        # No text: scanned/image-only (pages parse, nothing to read) vs
        # corrupt (the reader cannot parse the file at all).
        return "", "unreadable_scan" if _pdf_parses(content) else "failed"
    if normalized == DOCX_MIME:
        text = _extract_docx(content)
        if text and text.strip():
            collapsed = " ".join(text.split())
            return collapsed[:MAX_EXTRACT_CHARS], "done"
        return "", "failed"
    # Images (and unknown types): no OCR in the vault — 'unavailable'.
    return None, "unavailable"


def _pdf_parses(content: bytes) -> bool:
    """True when pypdf can open the file and count its pages."""
    from pypdf import PdfReader

    try:
        return len(PdfReader(io.BytesIO(content)).pages) > 0
    except Exception:
        return False


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # corrupt/encrypted PDFs must not break upload
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


def _extract_docx(content: bytes) -> str:
    from docx import Document

    try:
        doc = Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("DOCX text extraction failed: %s", exc)
        return ""
