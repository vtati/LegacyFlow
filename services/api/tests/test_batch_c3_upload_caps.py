"""Batch C3 — SEC-014: upload endpoints must not buffer entire bodies
before size checks.

Unit-level (no DB): the capped-read helper stops at the cap, and the
router handlers reject oversize bodies with 413 BEFORE the service
layer is invoked.
"""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.errors import PayloadTooLargeError
from app.core.uploads import (
    read_request_body_capped,
    read_sync_stream_capped,
    read_upload_capped,
)


class _FakeAsyncUpload:
    """Minimal async UploadFile stand-in yielding fixed-size chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.consumed = 0

    async def read(self, size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        self.consumed += len(chunk)
        return chunk


async def test_read_upload_capped_stops_at_cap() -> None:
    upload = _FakeAsyncUpload([b"x" * 100] * 10)
    with pytest.raises(PayloadTooLargeError) as excinfo:
        await read_upload_capped(upload, 250)  # type: ignore[arg-type]
    assert excinfo.value.status == 413
    # stopped early — the full 1000 bytes were never pulled through
    assert upload.consumed <= 300


async def test_read_upload_capped_returns_small_body() -> None:
    upload = _FakeAsyncUpload([b"hello ", b"world"])
    assert await read_upload_capped(upload, 100) == b"hello world"  # type: ignore[arg-type]


def test_read_sync_stream_capped() -> None:
    stream = io.BytesIO(b"y" * 1000)
    with pytest.raises(PayloadTooLargeError):
        read_sync_stream_capped(stream, 100)
    assert read_sync_stream_capped(io.BytesIO(b"ok"), 100) == b"ok"


class _FakeRequest:
    def __init__(self, chunks: list[bytes], content_length: int | None = None) -> None:
        self._chunks = list(chunks)
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.consumed = 0

    async def stream(self):
        for chunk in self._chunks:
            self.consumed += len(chunk)
            yield chunk


async def test_read_request_body_capped_content_length_early_reject() -> None:
    request = _FakeRequest([b"z" * 10], content_length=10_000)
    with pytest.raises(PayloadTooLargeError):
        await read_request_body_capped(request, 100)  # type: ignore[arg-type]
    assert request.consumed == 0  # rejected from the header alone


async def test_read_request_body_capped_streaming() -> None:
    request = _FakeRequest([b"z" * 100] * 5)
    with pytest.raises(PayloadTooLargeError):
        await read_request_body_capped(request, 250)  # type: ignore[arg-type]
    assert request.consumed <= 300


async def test_files_complete_upload_rejects_oversize_before_service(monkeypatch) -> None:
    from app.domains.files import router as files_router
    from app.domains.files import service as files_service

    called: dict[str, bytes] = {}

    def stub_complete(conn, **kwargs):  # pragma: no cover - must not run
        called["content"] = kwargs["content"]
        raise AssertionError("service must not be invoked for oversize bodies")

    monkeypatch.setattr(files_service, "complete_upload", stub_complete)

    oversize = b"x" * (25 * 1024 * 1024 + 1)
    upload = StarletteUploadFile(filename="big.pdf", file=io.BytesIO(oversize))
    with pytest.raises(PayloadTooLargeError):
        await files_router.complete_upload(
            user=None, upload_id=uuid4(), file=upload, request=_FakeRequest([])  # type: ignore[arg-type]
        )
    assert called == {}


async def test_documents_attach_evidence_rejects_oversize_before_service(monkeypatch) -> None:
    from app.domains.documents import router as documents_router
    from app.domains.documents import service as documents_service

    def stub_attach(conn, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("service must not be invoked for oversize bodies")

    monkeypatch.setattr(documents_service, "attach_funding_evidence", stub_attach)

    oversize = b"%PDF" + b"x" * (25 * 1024 * 1024)
    upload = StarletteUploadFile(filename="big.pdf", file=io.BytesIO(oversize))
    with pytest.raises(PayloadTooLargeError):
        await documents_router.attach_funding_evidence(
            user=None, task_id=uuid4(), file=upload  # type: ignore[arg-type]
        )


def test_pack_bundle_extracted_size_cap(monkeypatch) -> None:
    """A zip whose EXTRACTED size exceeds the cap is rejected before
    import, with no partial extraction reaching the importer."""
    from app.domains.review import service as review_service

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    imported: list[str] = []
    monkeypatch.setattr(
        review_service, "_import_pack_dir", lambda *a, **k: imported.append("called")
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.yaml", "pack: {}")
        archive.writestr("big.txt", b"0" * (101 * 1024 * 1024))
    bundle_bytes = buffer.getvalue()
    assert len(bundle_bytes) < 25 * 1024 * 1024  # compressed is small — a zip bomb shape

    bundle = StarletteUploadFile(filename="pack.zip", file=io.BytesIO(bundle_bytes))
    from app.core.errors import ValidationAppError

    with pytest.raises((ValidationAppError, PayloadTooLargeError)):
        review_service.import_pack(
            object(), actor_sub=str(uuid4()), bundle=bundle  # type: ignore[arg-type]
        )
    assert imported == []


def test_interview_extraction_rejects_oversize_before_service(monkeypatch) -> None:
    from app.domains.interview import router as interview_router

    def stub_run(conn, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("extraction must not run for oversize bodies")

    monkeypatch.setattr(interview_router.extraction_service, "run_extraction", stub_run)

    oversize = b"%PDF" + b"x" * (10 * 1024 * 1024)
    upload = StarletteUploadFile(filename="facts.pdf", file=io.BytesIO(oversize))
    with pytest.raises(PayloadTooLargeError):
        interview_router.extract_document(
            matter_id=uuid4(),
            session_id=uuid4(),
            claims=None,  # type: ignore[arg-type]
            file=upload,  # type: ignore[arg-type]
        )
