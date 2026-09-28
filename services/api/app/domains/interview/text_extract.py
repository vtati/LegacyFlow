"""Text extraction for interview document ingestion (Phase 2).

Converts uploaded source documents into plain text the extraction model can
read. Supported: PDF (per-page via pypdf) and DOCX (via python-docx).

PDFs with no text layer (scanned / image-only) fall back to OCR via
tesseract when the binary is available; when it is not, the result reports
``has_readable_text=False`` so the caller can tell the user to type the
details instead. Anything else unsupported also returns
``has_readable_text=False`` so the caller can report gracefully instead of
failing.

Limits keep provider payloads bounded: per-page caps and a total cap, with
truncation noted on the result. OCR is additionally capped to a small number
of pages at modest resolution so a hostile upload cannot burn unbounded CPU.
"""

from __future__ import annotations

import io
import logging
import shutil
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Hard stop on upload size handled at the router; this is defense in depth.
MAX_BYTES = 10 * 1024 * 1024
#: Max characters kept per page and in total (provider payload bound).
MAX_CHARS_PER_PAGE = 20_000
MAX_CHARS_TOTAL = 120_000
#: OCR fallback bounds: only image-only PDFs reach OCR, and only the first
#: few pages are OCR'd at 200 DPI.
OCR_MAX_PAGES = 5
OCR_DPI = 200


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    truncated: bool = False


@dataclass
class ExtractedDocument:
    filename: str
    file_kind: str  # "pdf" | "docx"
    pages: list[ExtractedPage] = field(default_factory=list)
    has_readable_text: bool = False
    total_chars: int = 0
    truncated: bool = False
    ocr_used: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(f"[page {p.page_number}]\n{p.text}" for p in self.pages)


class UnsupportedDocumentError(ValueError):
    """Raised when the file type has no text extractor."""


def _detect_kind(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith(".docx"):
        return "docx"
    raise UnsupportedDocumentError(
        f"Unsupported document type for {filename!r}: only .pdf and .docx are supported."
    )


def _cap_pages(pages: list[ExtractedPage]) -> tuple[list[ExtractedPage], bool]:
    """Apply per-page and total character caps; return (pages, truncated)."""
    capped: list[ExtractedPage] = []
    total = 0
    truncated = False
    for page in pages:
        text = page.text
        page_truncated = False
        if len(text) > MAX_CHARS_PER_PAGE:
            text = text[:MAX_CHARS_PER_PAGE]
            page_truncated = True
            truncated = True
        if total + len(text) > MAX_CHARS_TOTAL:
            remaining = MAX_CHARS_TOTAL - total
            if remaining > 0:
                text = text[:remaining]
                page_truncated = True
            else:
                truncated = True
                break
            truncated = True
        total += len(text)
        capped.append(
            ExtractedPage(page_number=page.page_number, text=text, truncated=page_truncated)
        )
    return capped, truncated


def _extract_pdf(data: bytes) -> list[ExtractedPage]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[ExtractedPage] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            logger.warning("pypdf failed on page %d; treating as empty", i)
            text = ""
        pages.append(ExtractedPage(page_number=i, text=text.strip()))
    return pages


def _extract_docx(data: bytes) -> list[ExtractedPage]:
    import docx

    document = docx.Document(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
    tables_text: list[str] = []
    for table in document.tables:
        for row in table.rows:
            tables_text.append(
                " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            )
    combined = text
    if tables_text:
        combined += "\n" + "\n".join(tables_text)
    return [ExtractedPage(page_number=1, text=combined.strip())]


def _tesseract_available() -> bool:
    """True when the tesseract binary is on PATH (lazy: no import cost)."""
    return shutil.which("tesseract") is not None


def _ocr_pdf_pages(data: bytes, page_count: int) -> list[ExtractedPage]:
    """OCR the first ``OCR_MAX_PAGES`` pages of an image-only PDF.

    Requires ``pymupdf`` (page rendering) and the ``tesseract`` binary.
    Raises RuntimeError when either is missing so the caller can degrade
    gracefully to ``has_readable_text=False``.
    """
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("pymupdf not installed; cannot OCR") from exc
    if not _tesseract_available():
        raise RuntimeError("tesseract binary not found; cannot OCR")
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("pytesseract/Pillow not installed; cannot OCR") from exc

    pages: list[ExtractedPage] = []
    doc = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    try:
        for i in range(min(page_count, OCR_MAX_PAGES)):
            try:
                page = doc[i]
                pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                text = pytesseract.image_to_string(img) or ""
            except Exception:
                logger.warning("OCR failed on page %d; treating as empty", i + 1)
                text = ""
            pages.append(ExtractedPage(page_number=i + 1, text=text.strip()))
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return pages


def extract_text(file_bytes: bytes, filename: str) -> ExtractedDocument:
    """Extract readable text from an uploaded document.

    Image-only PDFs fall back to OCR when tesseract is available; otherwise
    they report ``has_readable_text=False``.

    Raises:
        UnsupportedDocumentError: for file types with no extractor.
        ValueError: when the payload exceeds ``MAX_BYTES``.
    """
    if len(file_bytes) > MAX_BYTES:
        raise ValueError(f"Document exceeds the {MAX_BYTES // (1024 * 1024)} MB limit.")
    kind = _detect_kind(filename)
    ocr_used = False
    if kind == "pdf":
        raw_pages = _extract_pdf(file_bytes)
        if not any(p.text for p in raw_pages):
            # Image-only / scanned PDF: try OCR before giving up.
            try:
                raw_pages = _ocr_pdf_pages(file_bytes, len(raw_pages))
                ocr_used = any(p.text for p in raw_pages)
            except RuntimeError as exc:
                logger.info("OCR unavailable for %s: %s", filename, exc)
            except Exception:
                logger.warning("OCR crashed for %s; treating as unreadable", filename)
    else:
        raw_pages = _extract_docx(file_bytes)
    pages, truncated = _cap_pages(raw_pages)
    total_chars = sum(len(p.text) for p in pages)
    return ExtractedDocument(
        filename=filename,
        file_kind=kind,
        pages=pages,
        has_readable_text=any(p.text for p in pages),
        total_chars=total_chars,
        truncated=truncated,
        ocr_used=ocr_used,
    )
