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


def test_pack_bundle_member_write_failures_map_to_422(tmp_path) -> None:
    """m-2 (Batch C3 follow-up): a member chain through a non-directory
    (``evil_link`` written as a file, then ``evil_link/x`` beneath it)
    fails member writes with NotADirectoryError/FileExistsError — that
    must surface as the 422 validation shape, never a raw 500."""
    import stat

    from app.core.errors import ValidationAppError
    from app.domains.review import service as review_service

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        link = zipfile.ZipInfo("evil_link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/tmp")
        archive.writestr("evil_link/x.txt", "payload")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive_file:
        with pytest.raises(ValidationAppError) as excinfo:
            review_service._extract_pack_bundle(archive_file, tmp_path)
    assert excinfo.value.status == 422


def test_pack_bundle_encrypted_member_maps_to_422(tmp_path) -> None:
    """m-2: a member whose read raises RuntimeError (encrypted /
    unsupported compression) maps to the same 422 shape."""
    from app.core.errors import ValidationAppError
    from app.domains.review import service as review_service

    class _Info:
        filename = "secret.txt"
        file_size = 10

        def is_dir(self) -> bool:
            return False

    class _EncryptedArchive:
        def infolist(self):
            return [_Info()]

        def open(self, info):
            raise RuntimeError("File <ZipInfo 'secret.txt'> is encrypted")

    with pytest.raises(ValidationAppError) as excinfo:
        review_service._extract_pack_bundle(_EncryptedArchive(), tmp_path)  # type: ignore[arg-type]
    assert excinfo.value.status == 422


def test_pack_bundle_member_named_like_archive_cannot_corrupt_read(monkeypatch, tmp_path) -> None:
    """m-2: a member named ``bundle.zip`` (the uploaded archive's own
    temp name) must not overwrite the archive while it is being read:
    members extract into a subdirectory distinct from the archive."""
    from app.domains.review import service as review_service

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    captured: dict[str, object] = {}

    def stub_import(conn, actor_id, pack_dir, *, expected_bundle_hash=None):
        captured["pack_dir"] = pack_dir
        # The uploaded archive must still exist, intact, as a SIBLING of
        # the extraction directory (pre-fix the extraction dir WAS the
        # archive's directory and the member overwrote the archive).
        sibling_archive = pack_dir.parent / "bundle.zip"
        captured["archive_intact"] = (
            sibling_archive.is_file()
            and sibling_archive.read_bytes() == captured["bundle_bytes"]
        )
        captured["files"] = {
            p.relative_to(pack_dir).as_posix(): p.read_bytes()
            for p in sorted(pack_dir.rglob("*")) if p.is_file()
        }
        return "imported"

    monkeypatch.setattr(review_service, "_import_pack_dir", stub_import)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # Hostile member FIRST: overwriting the archive mid-read would
        # corrupt every member read after it (pre-fix behaviour).
        archive.writestr("bundle.zip", b"member-bytes-not-the-archive")
        archive.writestr("manifest.yaml", "pack: {}")
    captured["bundle_bytes"] = buffer.getvalue()
    bundle = StarletteUploadFile(filename="pack.zip", file=io.BytesIO(buffer.getvalue()))
    result = review_service.import_pack(
        object(), actor_sub=str(uuid4()), bundle=bundle  # type: ignore[arg-type]
    )
    assert result == "imported"
    assert captured["archive_intact"] is True
    # The importer saw the manifest intact AND the bundle.zip member as
    # its own file — the archive read was never corrupted.
    assert captured["files"]["manifest.yaml"] == b"pack: {}"  # type: ignore[index]
    assert captured["files"]["bundle.zip"] == b"member-bytes-not-the-archive"  # type: ignore[index]


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
