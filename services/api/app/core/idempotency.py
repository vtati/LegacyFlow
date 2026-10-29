"""``Idempotency-Key`` support for mutating requests (POST/PUT/PATCH/DELETE).

Protocol:

- Requests without an ``Idempotency-Key`` header pass through untouched.
- First sighting of ``(identity, key)``: the request is processed and the
  definitive response (2xx/4xx, never 429 or 5xx) is recorded for 24h
  (``IDEMPOTENCY_TTL_HOURS``), capped at 1 MiB of body.
- Repeat with an identical fingerprint (method + path + body hash): the
  recorded response is replayed byte-for-byte with
  ``Idempotent-Replayed: true``.
- Repeat with a *different* fingerprint: 422 RFC 7807
  (``idempotency_key_conflict``) — the key must be fresh per distinct
  operation.

Records are keyed by ``(identity, key)`` where identity is the verified JWT
``sub`` or the client IP (see :func:`app.core.rate_limit.client_identity`),
so a retry that fixes authentication is never served a stale replay from an
unauthenticated attempt.

Bodies are never logged (contract rule 9); only the key presence and the
replay/conflict outcome are observable.

Body-capture safety (SEC-014, Batch C3 follow-up M-1): this middleware is
outermost, so its capture must never become an unbounded-buffering
primitive. Idempotency is only meaningful for JSON mutations — the web
client sends no ``Idempotency-Key`` header at all today, and the known
header users are small JSON posts — so:

- upload-class requests (multipart content types, and the known
  upload/dictate route suffixes) SKIP idempotency handling entirely and
  stream through untouched; their endpoints enforce their own capped
  streaming reads (app.core.uploads);
- every other captured body is capped at ``_MAX_CAPTURED_BODY_BYTES``
  (1 MB): an oversize ``Content-Length`` is refused (413) from the
  header alone, and a chunked/lying body is refused (413) while
  streaming, having buffered at most the cap plus one chunk, and the
  downstream app is never invoked.

Single-instance limitation: the store lives in process memory. The scale
path is a shared store (Redis) keyed the same way; the middleware contract
is unchanged.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging import get_request_id
from app.core.rate_limit import client_identity

_IDEMPOTENT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_KEY_LENGTH = 256
_MAX_RECORDED_BODY_BYTES = 1_000_000
_MAX_RECORDS = 10_000
#: Hard cap on the request body this middleware will buffer for
#: fingerprinting (M-1). Sized at the recorded-response cap: legitimate
#: idempotent JSON mutations are far smaller, and anything larger is
#: not replayable through this middleware anyway.
_MAX_CAPTURED_BODY_BYTES = 1_000_000

#: Route suffixes whose requests carry upload bytes rather than JSON
#: mutations (files complete, interview dictate/extractions, funding
#: evidence, pack-bundle import). Multipart content types are also
#: upload-class regardless of path.
_UPLOAD_PATH_SUFFIXES = ("/dictate", "/extractions", "/complete", "/evidence", "/import")

_CONFLICT_TYPE = "urn:legacyflow:error:idempotency_key_conflict"
_INVALID_KEY_TYPE = "urn:legacyflow:error:invalid_idempotency_key"
_TOO_LARGE_TYPE = "urn:legacyflow:error:payload_too_large"


def _is_upload_class(request: Request) -> bool:
    """True for requests that stream upload bytes (never idempotency-captured)."""
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/"):
        return True
    return request.url.path.endswith(_UPLOAD_PATH_SUFFIXES)


def _declared_content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _read_body_capped(receive: Receive) -> bytes | None:
    """Read the ASGI request body, returning None once it exceeds the cap.

    Buffers at most the cap plus the chunk that crosses it; the caller
    refuses with 413 without invoking the downstream app.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"".join(chunks)
        if message["type"] != "http.request":
            continue
        chunk = bytes(message.get("body", b""))
        total += len(chunk)
        if total > _MAX_CAPTURED_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


@dataclass
class _Record:
    fingerprint: str
    status_code: int
    body: bytes
    media_type: str
    expires_at: float


_store: dict[tuple[str, str], _Record] = {}
_store_lock = threading.Lock()


def _fingerprint(request: Request, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(request.method.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(request.url.path.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def _get_record(key: tuple[str, str], now: float) -> _Record | None:
    record = _store.get(key)
    if record is None:
        return None
    if record.expires_at <= now:
        del _store[key]
        return None
    return record


def _evict_if_needed(now: float) -> None:
    if len(_store) <= _MAX_RECORDS:
        return
    for key in [k for k, r in _store.items() if r.expires_at <= now]:
        del _store[key]
    if len(_store) > _MAX_RECORDS:
        # Drop the oldest-expiring records first.
        for key, _ in sorted(_store.items(), key=lambda item: item[1].expires_at)[
            : len(_store) - _MAX_RECORDS
        ]:
            del _store[key]


def _should_record(status_code: int, body: bytes) -> bool:
    """Decide whether a downstream outcome is replayable.

    Only definitive outcomes are replayed: 2xx/4xx except 429 (a stale
    ``Retry-After`` must never be replayed) and never 5xx (a transient
    failure must not become permanent). Bodies are capped at 1 MiB.
    """
    if not 200 <= status_code < 500 or status_code == 429:
        return False
    return len(body) <= _MAX_RECORDED_BODY_BYTES


def _media_type_from_headers(raw_headers: list[tuple[bytes, bytes]]) -> str:
    for name, value in raw_headers:
        if name.lower() == b"content-type":
            return value.decode("latin-1").split(";")[0].strip() or "application/json"
    return "application/json"


def _problem(status_code: int, title: str, detail: str, type_uri: str) -> JSONResponse:
    raw = get_request_id()
    return JSONResponse(
        status_code=status_code,
        content={
            "type": type_uri,
            "title": title,
            "status": status_code,
            "detail": detail,
            "request_id": raw if raw else uuid.uuid4().hex,
        },
    )


async def _send_replay(record: _Record, scope: Scope, receive: Receive, send: Send) -> None:
    """Replay a recorded response as raw ASGI messages."""
    body = record.body
    await send(
        {
            "type": "http.response.start",
            "status": record.status_code,
            "headers": [
                (b"content-type", record.media_type.encode("latin-1")),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"idempotent-replayed", b"true"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def clear_idempotency_store() -> None:
    """Drop all idempotency records (tests)."""
    with _store_lock:
        _store.clear()


class IdempotencyMiddleware:
    """Record and replay mutating requests carrying ``Idempotency-Key``.

    Pure-ASGI middleware: response bytes are captured through the ``send``
    channel, so recording works regardless of the downstream response type.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.method not in _IDEMPOTENT_METHODS:
            await self.app(scope, receive, send)
            return
        raw_key = request.headers.get("idempotency-key")
        if raw_key is None or not raw_key.strip():
            await self.app(scope, receive, send)
            return
        key = raw_key.strip()
        if len(key) > _MAX_KEY_LENGTH:
            await _problem(
                422,
                "Invalid idempotency key",
                "Idempotency-Key must be 256 characters or fewer.",
                _INVALID_KEY_TYPE,
            )(scope, receive, send)
            return

        if _is_upload_class(request):
            # Upload-class requests are not idempotent-replayable and
            # must stream through their endpoints' own capped reads —
            # never buffered here (M-1).
            await self.app(scope, receive, send)
            return

        declared = _declared_content_length(request)
        if declared is not None and declared > _MAX_CAPTURED_BODY_BYTES:
            await _problem(
                413,
                "Payload too large",
                "The request body exceeds the 1 MB limit for "
                "idempotency-tracked requests.",
                _TOO_LARGE_TYPE,
            )(scope, receive, send)
            return

        store_key = (client_identity(request), key)
        captured = await _read_body_capped(receive)
        if captured is None:
            await _problem(
                413,
                "Payload too large",
                "The request body exceeds the 1 MB limit for "
                "idempotency-tracked requests.",
                _TOO_LARGE_TYPE,
            )(scope, receive, send)
            return
        body = captured

        replayed = False

        async def receive_once() -> Message:
            # The body was already consumed above; replay it downstream
            # exactly once, then signal end-of-body.
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        fingerprint = _fingerprint(request, body)
        now = time.monotonic()
        ttl_seconds = float(get_settings().IDEMPOTENCY_TTL_HOURS) * 3600.0

        with _store_lock:
            record = _get_record(store_key, now)
            conflict = record is not None and record.fingerprint != fingerprint
        if conflict:
            await _problem(
                422,
                "Idempotency key conflict",
                "This Idempotency-Key was already used for a different request.",
                _CONFLICT_TYPE,
            )(scope, receive, send)
            return
        if record is not None:
            await _send_replay(record, scope, receive, send)
            return

        chunks: list[bytes] = []
        response_status = 500
        response_headers: list[tuple[bytes, bytes]] = []

        async def send_capture(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
                response_headers.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(bytes(message.get("body", b"")))
            await send(message)

        await self.app(scope, receive_once, send_capture)

        response_body = b"".join(chunks)
        with _store_lock:
            if _get_record(store_key, time.monotonic()) is not None:
                # Lost a race with a concurrent same-key request: the first
                # writer wins. This response was already delivered; just
                # don't record over it.
                return
            if _should_record(response_status, response_body):
                _store[store_key] = _Record(
                    fingerprint=fingerprint,
                    status_code=response_status,
                    body=response_body,
                    media_type=_media_type_from_headers(response_headers),
                    expires_at=time.monotonic() + ttl_seconds,
                )
                _evict_if_needed(time.monotonic())
