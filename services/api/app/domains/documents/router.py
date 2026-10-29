"""Documents endpoints (Slice 4).

Routers are thin: they resolve the caller and delegate to the domain
service. Mounted by the coordinator under ``/v1``::

    from app.domains.documents.router import router

Demo documents are ALWAYS watermarked "DEMO ONLY — NOT LEGALLY VALID";
production release is gated on an approved ``document_release`` gate
and an attorney / firm_admin actor, and fails closed (HTTP 409) without
them. Errors follow the RFC 7807 ``AppError`` convention; the handler
in ``app.main`` renders them.
"""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core import db
from app.core.deps import get_current_user
from app.core.security import Claims

from . import schemas as sch
from . import service

router = APIRouter(tags=["documents"])

User = Annotated[Claims, Depends(get_current_user)]


def _auto_run_attorney_review(
    matter_id: UUID, actor_sub: str, *, deterministic_only: bool = False
) -> None:
    """Best-effort advisory pre-review after a generate/submit commits.

    Runs on a daemon thread (review M2): the family's response must not
    wait on the review — a slow model call on the submit path could
    otherwise hold the request open until the client times out and
    retries an action that already committed. The core action is
    durable before this is called; the reviewer only advises on it.

    Generate passes deterministic_only=True (instant, no model spend);
    submit runs the full review, upgrading a deterministic-only run
    for the same fingerprint. The service entry never raises and
    manages its own transactions; the worker below only resolves the
    matter row and swallows everything else.
    """
    import threading

    thread = threading.Thread(
        target=_auto_run_attorney_review_worker,
        args=(matter_id, actor_sub),
        kwargs={"deterministic_only": deterministic_only},
        daemon=True,
        name=f"attorney-review-{matter_id}",
    )
    thread.start()


def _auto_run_attorney_review_worker(
    matter_id: UUID, actor_sub: str, *, deterministic_only: bool = False
) -> None:
    import logging
    from uuid import UUID as _UUID

    from app.domains.attorney_review import service as attorney_review_service
    from app.domains.matters import repository as matters_repo

    try:
        with db.transaction() as conn:
            matter = matters_repo.get_matter(conn, matter_id)
        if matter is None:
            return
        attorney_review_service.run_review_for_matter(
            matter=matter,
            actor_id=_UUID(actor_sub),
            deterministic_only=deterministic_only,
        )
    except Exception:  # advisory hook; see _auto_run_attorney_review
        logging.getLogger(__name__).exception(
            "attorney_review auto-run hook failed for matter %s", matter_id
        )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.post("/document-templates", response_model=sch.DocumentTemplateOut, status_code=201)
def create_document_template(
    user: User, payload: sch.DocumentTemplateCreate
) -> sch.DocumentTemplateOut:
    """Create a document template (firm_admin only)."""
    with db.transaction() as conn:
        return service.create_template(conn, actor_sub=user.sub, payload=payload)


@router.get("/document-templates", response_model=list[sch.DocumentTemplateOut])
def list_document_templates(user: User) -> list[sch.DocumentTemplateOut]:
    """List the caller's firm templates (firm staff)."""
    with db.transaction() as conn:
        return service.list_templates(conn, actor_sub=user.sub)


@router.get("/document-templates/{template_id}", response_model=sch.DocumentTemplateOut)
def get_document_template(user: User, template_id: UUID) -> sch.DocumentTemplateOut:
    """Fetch one template, firm-scoped."""
    with db.transaction() as conn:
        return service.get_template(conn, actor_sub=user.sub, template_id=template_id)


@router.delete("/document-templates/{template_id}", status_code=204)
def delete_document_template(user: User, template_id: UUID) -> None:
    """Delete a template (firm_admin only); 409 when packages reference it."""
    with db.transaction() as conn:
        service.delete_template(conn, actor_sub=user.sub, template_id=template_id)


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


@router.post(
    "/matters/{matter_id}/document-packages",
    response_model=sch.DocumentPackageOut,
    status_code=201,
)
def create_document_package(
    user: User, matter_id: UUID, payload: sch.DocumentPackageCreate
) -> sch.DocumentPackageOut:
    """Create a package binding a matter to a template (firm staff)."""
    with db.transaction() as conn:
        return service.create_package(
            conn, actor_sub=user.sub, matter_id=matter_id, payload=payload
        )


@router.get(
    "/matters/{matter_id}/document-packages",
    response_model=list[sch.DocumentPackageOut],
)
def list_document_packages(user: User, matter_id: UUID) -> list[sch.DocumentPackageOut]:
    """List a matter's packages (firm staff or household member)."""
    with db.transaction() as conn:
        return service.list_packages(conn, actor_sub=user.sub, matter_id=matter_id)


@router.get(
    "/document-packages/{package_id}",
    response_model=sch.DocumentPackageOut,
)
def get_document_package(user: User, package_id: UUID) -> sch.DocumentPackageOut:
    """Fetch a package (firm staff or household member)."""
    with db.transaction() as conn:
        package, _versions = service.get_package_detail(
            conn, actor_sub=user.sub, package_id=package_id
        )
        return package


@router.get(
    "/matters/{matter_id}/document-versions",
    response_model=list[sch.DocumentVersionOut],
)
def list_matter_document_versions(user: User, matter_id: UUID) -> list[sch.DocumentVersionOut]:
    """List all document versions for a matter (firm staff or household member)."""
    with db.transaction() as conn:
        return service.list_matter_document_versions(conn, actor_sub=user.sub, matter_id=matter_id)


@router.post(
    "/packages/{package_id}/generate-demo",
    response_model=sch.DemoReleaseOut,
    status_code=201,
)
def generate_demo(user: User, package_id: UUID) -> sch.DemoReleaseOut:
    """Render DOCX+PDF demo versions (DEMO ONLY — NOT LEGALLY VALID)."""
    with db.transaction() as conn:
        package, versions = service.generate_demo(conn, actor_sub=user.sub, package_id=package_id)
        return sch.DemoReleaseOut(package=package, versions=versions)


@router.post(
    "/packages/{package_id}/release-production",
    response_model=sch.DemoReleaseOut,
    responses={
        409: {
            "description": (
                "Release is unavailable until the attorney approves the "
                "document release gate for the matter."
            )
        }
    },
)
def release_production(user: User, package_id: UUID) -> sch.DemoReleaseOut:
    """Release clean final documents for a package.

    Requires the attorney or firm_admin firm role and an approved
    ``document_release`` gate on the matter. Renders watermark-free
    DOCX+PDF versions and marks the package ``released``.
    """
    with db.transaction() as conn:
        package, versions = service.release_production(
            conn, actor_sub=user.sub, package_id=package_id
        )
        return sch.DemoReleaseOut(package=package, versions=versions)


# ---------------------------------------------------------------------------
# Family draft set (self-serve generation)
# ---------------------------------------------------------------------------


@router.get(
    "/matters/{matter_id}/draft-documents/state",
    response_model=sch.DraftSetStateOut,
)
def get_draft_documents_state(user: User, matter_id: UUID) -> sch.DraftSetStateOut:
    """Draft-set state for the family review page (never 409s on the flag)."""
    with db.transaction() as conn:
        return service.get_draft_set_state(conn, actor_sub=user.sub, matter_id=matter_id)


@router.post(
    "/matters/{matter_id}/draft-documents/generate",
    response_model=sch.DraftSetGenerateOut,
    status_code=201,
)
def generate_draft_documents(user: User, matter_id: UUID) -> sch.DraftSetGenerateOut:
    """Generate/regenerate the matter's watermarked draft set (family path).

    The service manages its own short transactions (PERF-020):
    rendering + storage uploads run between the prepare and persist
    transactions, never inside one.
    """
    result = service.generate_draft_set(actor_sub=user.sub, matter_id=matter_id)
    _auto_run_attorney_review(matter_id, user.sub, deterministic_only=True)
    return result


@router.post(
    "/matters/{matter_id}/draft-documents/submit",
    response_model=sch.DraftSubmitOut,
)
def submit_draft_documents(user: User, matter_id: UUID) -> sch.DraftSubmitOut:
    """Submit fresh drafts to the firm's routable attorney (frozen packet)."""
    with db.transaction() as conn:
        result = service.submit_for_attorney_review(
            conn, actor_sub=user.sub, matter_id=matter_id
        )
    _auto_run_attorney_review(matter_id, user.sub)
    return result


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.get("/document-versions/{version_id}/download")
def download_document_version(user: User, version_id: UUID) -> Response:
    """Download a rendered document version's file bytes."""
    with db.transaction() as conn:
        content, content_type, file_name = service.get_version_download(
            conn, actor_sub=user.sub, version_id=version_id
        )
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/matters/{matter_id}/notary-package")
def download_notary_package(user: User, matter_id: UUID) -> Response:
    """Download the notary execution package as a single PDF.

    Contains a cover page, the Virginia execution checklist, and the
    clean final PDF of every released package. All packages must be
    released (attorney-approved) before the package can be built.
    """
    with db.transaction() as conn:
        content = service.build_notary_package(
            conn, actor_sub=user.sub, matter_id=matter_id
        )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="notary-package-{matter_id}.pdf"'
        },
    )


# ---------------------------------------------------------------------------
# Signing checklists + attestations
# ---------------------------------------------------------------------------


@router.post(
    "/matters/{matter_id}/signing-checklists",
    response_model=sch.SigningChecklistOut,
    status_code=201,
)
def generate_signing_checklist(
    user: User, matter_id: UUID, payload: sch.SigningChecklistGenerate | None = None
) -> sch.SigningChecklistOut:
    """Generate a signing checklist from the matter's execution profile."""
    with db.transaction() as conn:
        return service.generate_signing_checklist(
            conn,
            actor_sub=user.sub,
            matter_id=matter_id,
            document_type=payload.document_type if payload else None,
        )


@router.get(
    "/matters/{matter_id}/signing-checklist",
    response_model=sch.SigningChecklistOut,
)
def get_matter_signing_checklist(user: User, matter_id: UUID) -> sch.SigningChecklistOut:
    """Return the newest signing checklist for a matter (firm staff or household)."""
    with db.transaction() as conn:
        return service.get_matter_signing_checklist(conn, actor_sub=user.sub, matter_id=matter_id)


@router.get(
    "/signing-checklists/{checklist_id}",
    response_model=sch.SigningChecklistOut,
)
def get_signing_checklist(user: User, checklist_id: UUID) -> sch.SigningChecklistOut:
    """Fetch a signing checklist (firm staff or household member)."""
    with db.transaction() as conn:
        return service.get_signing_checklist(conn, actor_sub=user.sub, checklist_id=checklist_id)


@router.post(
    "/signing-checklists/{checklist_id}/attest",
    response_model=sch.AttestationOut,
    status_code=201,
)
def record_attestation(
    user: User, checklist_id: UUID, payload: sch.AttestationIn
) -> sch.AttestationOut:
    """Record a household member's non-certifying attestation."""
    with db.transaction() as conn:
        return service.record_attestation(
            conn, actor_sub=user.sub, checklist_id=checklist_id, payload=payload
        )


# ---------------------------------------------------------------------------
# Funding tasks
# ---------------------------------------------------------------------------


@router.post(
    "/matters/{matter_id}/funding-tasks/generate",
    response_model=list[sch.FundingTaskOut],
    status_code=201,
)
def generate_funding_tasks(user: User, matter_id: UUID) -> list[sch.FundingTaskOut]:
    """Generate funding tasks from playbooks × household assets (idempotent)."""
    with db.transaction() as conn:
        return service.generate_funding_tasks(conn, actor_sub=user.sub, matter_id=matter_id)


@router.get(
    "/matters/{matter_id}/funding-tasks",
    response_model=list[sch.FundingTaskOut],
)
def list_funding_tasks(user: User, matter_id: UUID) -> list[sch.FundingTaskOut]:
    """List a matter's funding tasks (firm staff or household member)."""
    with db.transaction() as conn:
        return service.list_funding_tasks(conn, actor_sub=user.sub, matter_id=matter_id)


@router.patch(
    "/funding-tasks/{task_id}",
    response_model=sch.FundingTaskOut,
)
async def update_funding_task(user: User, task_id: UUID, request: Request) -> sch.FundingTaskOut:
    """Update a funding task's status and/or attach an evidence file.

    Accepts JSON ``{status, evidence_file_id}`` or a multipart form with
    a ``status`` field and/or an ``evidence_file`` upload (firm staff or
    household member).
    """
    content_type = request.headers.get("content-type", "")
    status: str | None = None
    evidence_file_id: UUID | None = None
    evidence_upload: tuple[str, bytes, str] | None = None
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_status = form.get("status")
        status = str(raw_status) if raw_status not in (None, "") else None
        maybe_file = form.get("evidence_file")
        # NB: starlette's form parser instantiates starlette.datastructures.UploadFile
        # (the parent class), so check against it rather than fastapi.UploadFile.
        if isinstance(maybe_file, StarletteUploadFile):
            upload_bytes = await maybe_file.read()
            evidence_upload = (
                maybe_file.filename or "evidence",
                upload_bytes,
                maybe_file.content_type or "application/octet-stream",
            )
    else:
        try:
            raw = await request.json()
        except json.JSONDecodeError:
            raw = None
        payload = sch.FundingTaskUpdate.model_validate(raw or {})
        status = payload.status
        evidence_file_id = payload.evidence_file_id
    with db.transaction() as conn:
        return service.update_funding_task(
            conn,
            actor_sub=user.sub,
            task_id=task_id,
            status=status,
            evidence_file_id=evidence_file_id,
            evidence_upload=evidence_upload,
        )


@router.post(
    "/funding-tasks/{task_id}/evidence",
    response_model=sch.FundingTaskOut,
    status_code=201,
)
async def attach_funding_evidence(
    user: User,
    task_id: UUID,
    file: Annotated[UploadFile, File(...)],
) -> sch.FundingTaskOut:
    """Upload an evidence file and attach it to the funding task."""
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    with db.transaction() as conn:
        return service.attach_funding_evidence(
            conn,
            actor_sub=user.sub,
            task_id=task_id,
            filename=file.filename or "evidence",
            content=content,
            content_type=content_type,
        )


# ---------------------------------------------------------------------------
# Vault shares
# ---------------------------------------------------------------------------


@router.post(
    "/matters/{matter_id}/vault-shares",
    response_model=sch.VaultShareCreateOut,
    status_code=201,
)
def create_vault_share(
    user: User, matter_id: UUID, payload: sch.VaultShareCreate
) -> sch.VaultShareCreateOut:
    """Create a vault share; the raw token is returned once (firm staff)."""
    with db.transaction() as conn:
        out, _token = service.create_vault_share(
            conn, actor_sub=user.sub, matter_id=matter_id, payload=payload
        )
        return out


@router.get(
    "/matters/{matter_id}/vault-shares",
    response_model=list[sch.VaultShareOut],
)
def list_vault_shares(user: User, matter_id: UUID) -> list[sch.VaultShareOut]:
    """List a matter's vault shares (firm staff or household member)."""
    with db.transaction() as conn:
        return service.list_vault_shares(conn, actor_sub=user.sub, matter_id=matter_id)


@router.post("/vault/access", response_model=sch.VaultAccessOut)
def access_vault_share(payload: sch.VaultAccessIn) -> sch.VaultAccessOut:
    """Resolve a vault share by bearer token (no JWT required)."""
    with db.transaction() as conn:
        return service.access_vault_share(conn, payload)


@router.get("/vault-shares/{share_id}", response_model=sch.VaultShareOut)
def get_vault_share(user: User, share_id: UUID) -> sch.VaultShareOut:
    """Fetch a vault share summary (firm staff; no token material)."""
    with db.transaction() as conn:
        return service.get_vault_share_detail(conn, actor_sub=user.sub, share_id=share_id)


@router.post("/vault-shares/{share_id}/revoke", response_model=sch.VaultShareOut)
def revoke_vault_share(user: User, share_id: UUID) -> sch.VaultShareOut:
    """Revoke a vault share (firm staff)."""
    with db.transaction() as conn:
        return service.revoke_vault_share(conn, actor_sub=user.sub, share_id=share_id)


# ---------------------------------------------------------------------------
# Family instruction letter (nonbinding demo)
# ---------------------------------------------------------------------------


@router.post(
    "/matters/{matter_id}/instruction-letter",
    response_model=sch.InstructionLetterOut,
    status_code=201,
)
def generate_instruction_letter(
    user: User, matter_id: UUID, payload: sch.InstructionLetterIn
) -> sch.InstructionLetterOut:
    """Generate a nonbinding demo instruction letter (DEMO ONLY — NOT LEGALLY VALID)."""
    with db.transaction() as conn:
        package, versions = service.generate_instruction_letter(
            conn, actor_sub=user.sub, matter_id=matter_id, payload=payload
        )
        return sch.InstructionLetterOut(package=package, versions=versions)


# ---------------------------------------------------------------------------
# Amendments
# ---------------------------------------------------------------------------


@router.post(
    "/matters/{matter_id}/amendments",
    response_model=sch.AmendmentRequestOut,
    status_code=201,
)
def create_amendment_request(
    user: User, matter_id: UUID, payload: sch.AmendmentRequestIn
) -> sch.AmendmentRequestOut:
    """Request changes to an implemented plan."""
    with db.transaction() as conn:
        return service.create_amendment_request(
            conn, actor_sub=user.sub, matter_id=matter_id, payload=payload
        )


@router.get(
    "/matters/{matter_id}/amendments",
    response_model=list[sch.AmendmentRequestOut],
)
def list_amendment_requests(
    user: User, matter_id: UUID
) -> list[sch.AmendmentRequestOut]:
    """List amendment requests for a matter (version timeline)."""
    with db.transaction() as conn:
        return service.list_amendment_requests(
            conn, actor_sub=user.sub, matter_id=matter_id
        )


@router.patch(
    "/matters/{matter_id}/amendments/{amendment_id}",
    response_model=sch.AmendmentRequestOut,
)
def decide_amendment_request(
    user: User, matter_id: UUID, amendment_id: UUID, payload: sch.AmendmentDecideIn
) -> sch.AmendmentRequestOut:
    """Attorney decision on an amendment request (attorney/firm_admin only)."""
    with db.transaction() as conn:
        return service.decide_amendment_request(
            conn, actor_sub=user.sub, matter_id=matter_id,
            amendment_id=amendment_id, payload=payload,
        )
