"""File upload endpoints (Slice 4, Track B4).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.files.router import router

``GET /files/download`` is public: it authenticates purely via the
short-lived HMAC download token (no JWT).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core import db
from app.core.audit import correlation_id_from_request_id
from app.core.deps import get_current_user
from app.core.errors import NotFoundError
from app.core.security import Claims
from app.domains.files import schemas as sch
from app.domains.files import service

router = APIRouter(tags=["files"])

User = Annotated[Claims, Depends(get_current_user)]

_CHUNK_SIZE = 65536


def _correlation_id(request: Request) -> UUID | None:
    return correlation_id_from_request_id(getattr(request.state, "request_id", None))


def _iter_file(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            yield chunk


def _download_filename(original: str) -> str:
    """Sanitize the original filename for Content-Disposition."""
    return original.replace('"', "").replace("\r", "").replace("\n", "")


@router.post(
    "/matters/{matter_id}/files/intent",
    response_model=sch.UploadIntentResponse,
    status_code=201,
)
def create_upload_intent(
    user: User,
    matter_id: UUID,
    payload: sch.UploadIntentCreate,
    request: Request,
) -> sch.UploadIntentResponse:
    """Reserve an upload slot: validates filename/MIME/size, returns the upload id."""
    with db.transaction() as conn:
        return service.create_upload_intent(
            conn,
            actor_sub=user.sub,
            matter_id=matter_id,
            payload=payload,
            correlation_id=_correlation_id(request),
        )


@router.post("/files/{upload_id}/complete", response_model=sch.FileUploadOut)
async def complete_upload(
    user: User,
    upload_id: UUID,
    file: Annotated[UploadFile, File(...)],
    request: Request,
) -> sch.FileUploadOut:
    """Upload the file bytes for a pending intent; marks it ready for scanning."""
    content = await file.read()
    with db.transaction() as conn:
        return service.complete_upload(
            conn,
            actor_sub=user.sub,
            upload_id=upload_id,
            content=content,
            correlation_id=_correlation_id(request),
        )


@router.get("/matters/{matter_id}/files", response_model=list[sch.FileUploadOut])
def list_matter_files(user: User, matter_id: UUID) -> list[sch.FileUploadOut]:
    """List a matter's uploads, newest first."""
    with db.transaction() as conn:
        return service.list_matter_files(conn, actor_sub=user.sub, matter_id=matter_id)


@router.post("/files/{upload_id}/analyze", response_model=sch.FileAnalysisOut)
def analyze_upload(
    user: User, upload_id: UUID, request: Request
) -> sch.FileAnalysisOut:
    """Run AI document analysis on an upload's extracted text.

    Returns the document type, summary, key facts, suggested tags, and
    confidence. Suggested tags are merged into the file's tag list.
    Requires extracted text — 422 when extraction is unavailable.
    """
    # Phased (PERF-016): the service manages short prepare/persist
    # transactions itself so the LLM call holds no transaction.
    return service.analyze_upload_phased(
        actor_sub=user.sub,
        upload_id=upload_id,
        correlation_id=_correlation_id(request),
    )


@router.post("/files/{upload_id}/tags", response_model=sch.FileUploadOut)
def add_upload_tags(
    user: User, upload_id: UUID, payload: sch.FileTagsUpdate, request: Request
) -> sch.FileUploadOut:
    """Add user tags to an upload (deduplicated, normalized)."""
    with db.transaction() as conn:
        return service.add_upload_tags(
            conn,
            actor_sub=user.sub,
            upload_id=upload_id,
            tags=payload.tags,
            correlation_id=_correlation_id(request),
        )


@router.post("/files/{upload_id}/download-token", response_model=sch.DownloadTokenResponse)
def create_download_token(
    user: User, upload_id: UUID, request: Request
) -> sch.DownloadTokenResponse:
    """Mint a 15-minute HMAC download token for an upload."""
    with db.transaction() as conn:
        return service.create_download_token(
            conn,
            actor_sub=user.sub,
            upload_id=upload_id,
            correlation_id=_correlation_id(request),
        )


@router.get("/files/download")
def download_file(token: Annotated[str, Query(...)]) -> StreamingResponse:
    """Serve a ``clean`` upload's bytes. Public: token-authenticated, no JWT.

    With the Supabase backend, redirects (302) to a 15-minute signed URL.
    With the local backend, streams the file directly.
    """
    with db.transaction() as conn:
        row = service.authorize_download(conn, token=token)
    stored_path = str(row["stored_path"])

    # Prefer a signed URL when the backend supports it (Supabase).
    signed = service.storage_signed_url(stored_path, expires_in=900)
    if signed:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=signed, status_code=302)

    path = service.resolve_stored_path(stored_path)
    if not path.is_file():
        # Row exists but bytes are missing: a server-side inconsistency.
        raise NotFoundError("File not found.")
    media_type = str(row["mime_type"]) if row.get("mime_type") else "application/octet-stream"
    filename = _download_filename(str(row["original_filename"]))
    return StreamingResponse(
        _iter_file(path),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
