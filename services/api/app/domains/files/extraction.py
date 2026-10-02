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
    normalized = (mime_type or "").strip().lower()
    if normalized == PDF_MIME:
        text = _extract_pdf(content)
    elif normalized == DOCX_MIME:
        text = _extract_docx(content)
    elif normalized in _IMAGE_MIMES:
        return None
    else:
        return None
    if not text or not text.strip():
        return ""
    collapsed = " ".join(text.split())
    return collapsed[:MAX_EXTRACT_CHARS]


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
