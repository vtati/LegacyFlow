"""Streaming size caps for upload endpoints (SEC-014).

Upload handlers must never buffer an entire request body before a
size check: a multi-GB POST from any authenticated account would OOM
the API for every tenant. These helpers enforce a hard cap WHILE
reading — rejecting early from Content-Length when it is present and
trustworthy, and counting bytes as they stream so a chunked (or
lying) body is cut off at the cap, having never been fully buffered
or handed to a service layer.
"""

from __future__ import annotations

from typing import Any, BinaryIO

from app.core.errors import PayloadTooLargeError

_CHUNK_SIZE = 65536

def _content_length_allowance(max_bytes: int) -> int:
    """Framing allowance for the Content-Length early reject.

    Multipart framing adds overhead beyond the file bytes themselves,
    so the early reject only fires when the declared total exceeds the
    cap by more than this allowance; the streaming count (which
    measures the file/body bytes exactly) is the real gate.
    """
    return min(64 * 1024, max(1024, max_bytes // 20))


def _too_large(max_bytes: int) -> PayloadTooLargeError:
    return PayloadTooLargeError(
        f"The upload exceeds the {max_bytes // (1024 * 1024)} MB limit."
    )


def ensure_content_length_within(headers: Any, max_bytes: int) -> None:
    """Reject early when a present Content-Length already proves excess."""
    raw = None
    try:
        raw = headers.get("content-length")
    except AttributeError:
        return
    if raw is None:
        return
    try:
        declared = int(raw)
    except (TypeError, ValueError):
        return
    if declared > max_bytes + _content_length_allowance(max_bytes):
        raise _too_large(max_bytes)


async def read_upload_capped(file: Any, max_bytes: int) -> bytes:
    """Read an async UploadFile in chunks, aborting past ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def read_sync_stream_capped(stream: BinaryIO, max_bytes: int) -> bytes:
    """Read a sync binary stream in chunks, aborting past ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


async def read_request_body_capped(request: Any, max_bytes: int) -> bytes:
    """Read a raw request body with a hard cap (Content-Length + stream)."""
    ensure_content_length_within(request.headers, max_bytes)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)
