"""Magic-byte MIME sniffing for uploaded bytes.

Mirrors the worker scanner's table (``services/worker/worker/scanning.py``
``MIME_MAGIC`` — the worker is a separate deployable and cannot import
this module). Used at upload time to DERIVE a file's MIME type from its
bytes instead of trusting the client's claim (SEC-011): the scanner
later re-validates the same prefixes, so a type derived here is one
the scanner can positively identify.
"""

from __future__ import annotations

#: Magic-byte prefixes by MIME type. DOCX is an OOXML zip, so the
#: local-file-header / empty-archive / spanned-archive signatures all
#: count as a match.
MIME_MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    ),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}

#: File suffix per sniffed MIME type (storage keys).
SUFFIX_BY_MIME: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/png": "png",
    "image/jpeg": "jpg",
}


def sniff_mime_type(content: bytes) -> str | None:
    """Return the MIME type the bytes' magic prefix identifies, or None.

    Fail-closed: empty content and unrecognized prefixes return None —
    the same verdict the worker scanner records as quarantined.
    """
    if not content:
        return None
    for mime_type, prefixes in MIME_MAGIC.items():
        if any(content.startswith(prefix) for prefix in prefixes):
            return mime_type
    return None
