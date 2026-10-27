"""Attorney Reviewer — run service, snapshot recorder, acknowledgment.

The reviewer analyses ONLY the frozen artifact persisted at render time
(snapshots + the source payload for the packages' stamped fingerprint).
It never reads live intake answers, never touches a gate, and its
findings are advisory: nothing in this module can block generation,
submission, or release (plan 2026-10-27, design §9.7).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from app.core.audit import record_audit_event
from app.core.config import get_settings
from app.core.errors import AuthError, ConflictError, NotFoundError

from . import model_review
from . import repository as repo
from . import schemas as sch
from .checks import Finding, finding_hash, run_deterministic_checks

logger = logging.getLogger(__name__)

FLAG_KEY = "attorney_reviewer"
PROMPT_VERSION = model_review.PROMPT_VERSION


class AttorneyReviewDisabledError(ConflictError):
    """The attorney_reviewer flag is off for the matter's firm (HTTP 409)."""

    type = "urn:legacyflow:error:attorney_review_disabled"
    title = "Automated pre-review disabled"
    code = "attorney_review_disabled"
    detail = (
        "The automated pre-review is not enabled for this firm. "
        "It requires the 'attorney_reviewer' feature flag."
    )


class AttorneyReviewNotReadyError(ConflictError):
    """No frozen draft package exists to review yet (HTTP 409)."""

    type = "urn:legacyflow:error:attorney_review_not_ready"
    title = "Nothing to review yet"
    code = "attorney_review_not_ready"
    detail = (
        "There is no generated draft package for the current answers yet. "
        "Generate the draft documents before running the pre-review."
    )


def _actor_id(actor_sub: str) -> UUID:
    try:
        return UUID(actor_sub)
    except ValueError as exc:
        raise AuthError("The token subject is invalid.") from exc


# ---------------------------------------------------------------------------
# Snapshot recorder (called from the documents render core)
# ---------------------------------------------------------------------------


def _effective_required_keys(pack: Any, answers: dict[str, Any]) -> list[str]:
    """Pack-required question keys that actually applied to this family.

    Questions flagged ``required`` but pruned by their ``applies_if``
    gate (guardians for a childless testator, specific bequests when
    there are none) never had to be answered — counting them as
    required makes the unfilled-placeholder check read every pruned
    packet as missing data. Mirrors the intake gate exactly by reusing
    its ``compute_progress`` partition (single source of truth).
    """
    from ...domains.intake import question_sets as intake_qs

    questions = [q for _, _, q in intake_qs.iter_questions(pack.question_sets)]
    flagged = [str(q["key"]) for q in questions if q.get("required")]
    try:
        _, _, _, pruned = intake_qs.compute_progress(pack.question_sets, answers)
    except Exception:  # a malformed pack must not break recording
        pruned = []
    pruned_set = {str(k) for k in pruned}
    return [k for k in flagged if k not in pruned_set]


def _build_source_payload(conn: Any, matter: dict[str, Any]) -> dict[str, Any]:
    """Assemble the frozen source payload from CURRENT source data.

    Called at render time only — the payload is persisted with the
    snapshot, so a later review run sees exactly what the render saw.
    """
    from ...domains.documents.draft_set import resolve_draft_set
    from ...domains.household import repository as household_repo
    from ...domains.intake import question_sets as intake_qs
    from ...domains.intake import repository as intake_repo
    from ...domains.legal_content import pack_loader
    from ...domains.nominations import repository as nominations_repo
    from ..documents.service import _matter_pack_ref

    matter_id = UUID(str(matter["id"]))
    household_id = UUID(str(matter["household_id"]))
    answers = {
        str(row["question_key"]): row["answer"]
        for row in intake_repo.list_answers(conn, matter_id)
    }
    persons = [
        {
            "id": str(row["id"]),
            "full_name": str(row.get("full_name") or ""),
            "kind": row.get("kind"),
            "date_of_birth": (
                str(row["date_of_birth"]) if row.get("date_of_birth") else None
            ),
        }
        for row in household_repo.list_persons_for_household(conn, household_id)
    ]
    nominations = [
        {
            "nomination_type": str(row["nomination_type"]),
            "person_id": str(row["person_id"]) if row.get("person_id") else None,
            "priority": row.get("priority"),
            "status": str(row["status"]),
        }
        for row in nominations_repo.list_nominations(conn, matter_id)
    ]
    question_keys: list[str] = []
    required_question_keys: list[str] = []
    _pack_id, jurisdiction_code, semantic_version = _matter_pack_ref(matter)
    try:
        pack = pack_loader.get_pack(jurisdiction_code, semantic_version)
        questions = [q for _, _, q in intake_qs.iter_questions(pack.question_sets)]
        question_keys = [str(q["key"]) for q in questions]
        required_question_keys = _effective_required_keys(pack, answers)
    except FileNotFoundError:
        question_keys = []
        required_question_keys = []
    # Slot -> the answer keys that feed it, inverted from the documents
    # slot table: the unfilled-placeholder check uses it to tell an
    # answered-but-dropped slot (defect) from an optional question the
    # family skipped (blank by design).
    from ..documents.service import _ANSWER_SLOT_TABLE

    slot_answer_keys: dict[str, list[str]] = {}
    for answer_key, (slots, _is_person) in _ANSWER_SLOT_TABLE.items():
        for slot in slots:
            slot_answer_keys.setdefault(slot, []).append(answer_key)
    return {
        "persons": persons,
        "nominations": nominations,
        "answers": answers,
        "expected_set": resolve_draft_set(answers),
        "question_keys": question_keys,
        "required_question_keys": required_question_keys,
        "slot_answer_keys": slot_answer_keys,
    }


def record_render_snapshot(
    conn: Any,
    *,
    matter: dict[str, Any],
    package_id: UUID,
    package_version: int,
    source_fingerprint: str,
    template_type: str,
    template_body: str,
    body_text: str,
    context: dict[str, str],
) -> None:
    """Persist the frozen artifact for one rendered package version.

    The matter-level source payload is recorded once per fingerprint —
    at that fingerprint's first render — so every package in the set
    shares the exact source view the render consumed.
    """
    matter_id = UUID(str(matter["id"]))
    firm_id = UUID(str(matter["firm_id"]))
    repo.insert_snapshot(
        conn,
        matter_id=matter_id,
        firm_id=firm_id,
        package_id=package_id,
        package_version=package_version,
        source_fingerprint=source_fingerprint,
        template_type=template_type,
        template_body=template_body,
        body_text=body_text,
        context=context,
    )
    if repo.get_source(conn, matter_id=matter_id, source_fingerprint=source_fingerprint) is None:
        payload = _build_source_payload(conn, matter)
        repo.insert_source(
            conn,
            matter_id=matter_id,
            firm_id=firm_id,
            source_fingerprint=source_fingerprint,
            payload=payload,
        )


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


def _current_fingerprint(conn: Any, matter_id: UUID) -> str | None:
    """The uniform stamped fingerprint of the matter's packages, if any."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT source_fingerprint FROM document_packages"
            " WHERE matter_id = %s AND source_fingerprint IS NOT NULL",
            (str(matter_id),),
        )
        rows = [str(r["source_fingerprint"]) for r in cur.fetchall()]
    return rows[0] if len(rows) == 1 else None


def _execution_profiles(conn: Any, matter: dict[str, Any]) -> dict[str, Any]:
    """All execution profiles of the matter's pack, keyed by document_type."""
    from ..documents import service as documents_service

    pack_id, _jurisdiction_code, _version = documents_service._matter_pack_ref(matter)
    loaded = documents_service._content_pack_files(pack_id)
    profiles: dict[str, Any] = {}
    if loaded is None:
        fallback = documents_service._FALLBACK_EXECUTION_PROFILE
        profiles[str(fallback["document_type"])] = fallback
        return profiles
    profiles_doc, _playbooks = loaded
    for profile in profiles_doc.get("execution_profiles", []) or []:
        doc_type = profile.get("document_type")
        if doc_type:
            profiles[str(doc_type)] = profile
    return profiles


def _finding_out(row: dict[str, Any]) -> sch.FindingOut:
    location = row.get("location") or {}
    return sch.FindingOut(
        id=UUID(str(row["id"])),
        layer=str(row["layer"]),
        check=str(row["check_name"]),
        severity=str(row["severity"]),
        location=sch.FindingLocationOut(
            document=location.get("document"),
            field=location.get("field"),
            excerpt=location.get("excerpt"),
        ),
        explanation=str(row["explanation"]),
        suggested_fix=row.get("suggested_fix"),
        disposition=str(row["disposition"]),
        acknowledgment_note=row.get("acknowledgment_note"),
        acknowledged_by_user_id=(
            UUID(str(row["acknowledged_by_user_id"]))
            if row.get("acknowledged_by_user_id") else None
        ),
        acknowledged_at=row.get("acknowledged_at"),
    )


def _run_out(row: dict[str, Any]) -> sch.RunOut:
    return sch.RunOut(
        id=UUID(str(row["id"])),
        status=str(row["status"]),
        model_id=row.get("model_id"),
        prompt_version=row.get("prompt_version"),
        source_fingerprint=str(row["source_fingerprint"]),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
    )


def _apply_disposition_transitions(
    conn: Any,
    *,
    previous_run: dict[str, Any] | None,
    new_hashes: set[str],
) -> dict[str, dict[str, Any]]:
    """Move the previous run's findings to their §9.5 dispositions.

    Returns the previous findings by hash, so acknowledgments can carry
    over to the identical finding in the new run.
    """
    if previous_run is None:
        return {}
    previous = repo.findings_for_run(conn, UUID(str(previous_run["id"])))
    by_hash: dict[str, dict[str, Any]] = {}
    for row in previous:
        by_hash[str(row["finding_hash"])] = row
        if str(row["finding_hash"]) in new_hashes:
            repo.set_disposition(conn, UUID(str(row["id"])), "stale")
        elif str(row["disposition"]) in ("open", "acknowledged"):
            repo.set_disposition(
                conn, UUID(str(row["id"])), "fixed_by_regeneration"
            )
    return by_hash


def run_review(
    conn: Any, *, actor_sub: str, matter_id: UUID
) -> sch.RunResponseOut:
    """Run the pre-review over the matter's frozen package (write scope)."""
    from ...domains.matters import service as matters_service

    actor_id = _actor_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    return _run_review_scoped(conn, matter=scope.matter, actor_id=actor_id)


def run_review_for_matter(
    conn: Any, *, matter: dict[str, Any], actor_id: UUID | None
) -> None:
    """Auto-run entry for the generate/submit hooks: never raises.

    Silent when the flag is off or no frozen package exists, and any
    unexpected failure is logged and swallowed — the reviewer advises
    on the core flow and must never break it. The hook runs in its own
    transaction, so a swallowed failure here rolls back only the run.
    """
    try:
        _run_review_scoped(conn, matter=matter, actor_id=actor_id, quiet=True)
    except Exception:
        logger.exception(
            "attorney_review auto-run failed for matter %s", matter.get("id")
        )


def _run_review_scoped(
    conn: Any,
    *,
    matter: dict[str, Any],
    actor_id: UUID | None,
    quiet: bool = False,
) -> sch.RunResponseOut | None:
    from ...domains.feature_flags import service as feature_flags

    matter_id = UUID(str(matter["id"]))
    firm_id = UUID(str(matter["firm_id"]))
    pack_id, _jur, _ver = _pack_ref(matter)
    if not feature_flags.is_enabled(conn, FLAG_KEY, firm_id=firm_id, pack_id=pack_id):
        if quiet:
            return None
        raise AttorneyReviewDisabledError()
    fingerprint = _current_fingerprint(conn, matter_id)
    snapshots = (
        repo.snapshots_for(conn, matter_id=matter_id, source_fingerprint=fingerprint)
        if fingerprint else []
    )
    source_row = (
        repo.get_source(conn, matter_id=matter_id, source_fingerprint=fingerprint)
        if fingerprint else None
    )
    if not fingerprint or not snapshots or source_row is None:
        if quiet:
            return None
        raise AttorneyReviewNotReadyError()

    run = repo.insert_run(
        conn,
        matter_id=matter_id,
        firm_id=firm_id,
        source_fingerprint=fingerprint,
        prompt_version=PROMPT_VERSION,
        created_by_user_id=actor_id,
    )
    run_id = UUID(str(run["id"]))
    findings = run_deterministic_checks(
        snapshots=snapshots,
        source=dict(source_row["payload"]),
        execution_profiles=_execution_profiles(conn, matter),
        today=date.today(),
    )
    model_id = "deterministic_only"
    settings = get_settings()
    if (
        settings.AI_LLM_PROVIDER.strip().lower() == "openai"
        and settings.OPENAI_API_KEY
    ):
        model_id = settings.OPENAI_INTERVIEW_MODEL_V2
        try:
            model_findings, usage = model_review.review_via_model(
                snapshots=snapshots,
                source=dict(source_row["payload"]),
                model=model_id,
            )
            findings.extend(model_findings)
            _record_trace(
                conn,
                matter=matter,
                firm_id=firm_id,
                model_id=model_id,
                usage=usage,
            )
        except Exception:  # provider failure must not fail the review
            logger.exception(
                "attorney_review model layer failed for matter %s; "
                "continuing with deterministic findings only",
                matter_id,
            )
            model_id = "deterministic_only"
    # Idempotent by content hash across both layers.
    unique: dict[str, Finding] = {}
    for finding in findings:
        unique.setdefault(finding_hash(finding), finding)
    findings = list(unique.values())
    new_hashes = set(unique.keys())

    previous_run = repo.latest_completed_run(
        conn, matter_id=matter_id, exclude_run_id=run_id
    )
    previous_by_hash = _apply_disposition_transitions(
        conn,
        previous_run=previous_run,
        new_hashes=new_hashes,
    )
    for finding in findings:
        digest = finding_hash(finding)
        carried = previous_by_hash.get(digest)
        disposition = "open"
        ack_fields: dict[str, Any] = {}
        if carried is not None and carried.get("disposition") == "acknowledged":
            disposition = "acknowledged"
            ack_fields = {
                "acknowledged_by_user_id": carried.get("acknowledged_by_user_id"),
                "acknowledgment_note": carried.get("acknowledgment_note"),
                "acknowledged_at": carried.get("acknowledged_at"),
            }
        repo.insert_finding(
            conn,
            run_id=run_id,
            matter_id=matter_id,
            package_id=finding.package_id,
            finding_hash=digest,
            layer=finding.layer,
            check_name=finding.check,
            severity=finding.severity,
            location={
                "document": finding.document,
                "field": finding.field,
                "excerpt": finding.excerpt,
            },
            explanation=finding.explanation,
            suggested_fix=finding.suggested_fix,
            disposition=disposition,
            **ack_fields,
        )
    completed = repo.complete_run(conn, run_id, model_id=model_id)
    record_audit_event(
        conn,
        action="attorney_review.completed",
        resource_type="attorney_review_run",
        actor_user_id=actor_id,
        actor_type="user" if actor_id else "system",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=run_id,
        after_summary={
            "source_fingerprint": fingerprint,
            "findings_count": len(findings),
            "model_id": model_id,
        },
    )
    rows = repo.findings_for_run(conn, run_id)
    return sch.RunResponseOut(
        run=_run_out(completed),
        findings=[_finding_out(row) for row in rows],
    )


def _pack_ref(matter: dict[str, Any]) -> tuple[str, str, str]:
    from ..documents.service import _matter_pack_ref

    return _matter_pack_ref(matter)


def _record_trace(
    conn: Any,
    *,
    matter: dict[str, Any],
    firm_id: UUID,
    model_id: str,
    usage: dict[str, int],
) -> None:
    from ...domains.interview import repository as interview_repo

    _pack_id, _jur, semantic_version = _pack_ref(matter)
    interview_repo.insert_ai_trace(
        conn,
        firm_id=firm_id,
        matter_id=UUID(str(matter["id"])),
        turn_id=None,
        provider="openai",
        model_id=model_id,
        model_version=None,
        prompt_template=model_review.TRACE_PROMPT_TEMPLATE,
        prompt_version=PROMPT_VERSION,
        pack_version=semantic_version,
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
        latency_ms=None,
        retrieved_doc_ids=[],
    )


# ---------------------------------------------------------------------------
# Reads + acknowledgment
# ---------------------------------------------------------------------------


def get_findings(
    conn: Any, *, actor_sub: str, matter_id: UUID
) -> sch.FindingsResponseOut:
    """Latest run's findings for the current fingerprint (read scope)."""
    from ...domains.feature_flags import service as feature_flags
    from ...domains.matters import service as matters_service

    actor_id = _actor_id(actor_sub)
    scope = matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    pack_id, _jur, _ver = _pack_ref(matter)
    enabled = feature_flags.is_enabled(
        conn, FLAG_KEY, firm_id=firm_id, pack_id=pack_id
    )
    if not enabled:
        return sch.FindingsResponseOut(enabled=False)
    fingerprint = _current_fingerprint(conn, matter_id)
    if not fingerprint:
        return sch.FindingsResponseOut(enabled=True)
    run = repo.latest_completed_run(
        conn, matter_id=matter_id, source_fingerprint=fingerprint
    )
    if run is None:
        return sch.FindingsResponseOut(enabled=True)
    rows = repo.findings_for_run(conn, UUID(str(run["id"])))
    return sch.FindingsResponseOut(
        enabled=True,
        run=_run_out(run),
        findings=[_finding_out(row) for row in rows],
    )


def acknowledge_finding(
    conn: Any, *, actor_sub: str, matter_id: UUID, finding_id: UUID, note: str
) -> sch.FindingOut:
    """Acknowledge (override) a finding with a note — recorded, never a gate.

    Available to the family (write scope) and to firm staff; the
    acknowledgment is stored on the finding and travels with the packet.
    """
    from ...domains.matters import service as matters_service

    actor_id = _actor_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    finding = repo.get_finding(conn, finding_id)
    if finding is None or UUID(str(finding["matter_id"])) != matter_id:
        raise NotFoundError("Finding not found.")
    fingerprint = _current_fingerprint(conn, matter_id)
    latest = (
        repo.latest_completed_run(
            conn, matter_id=matter_id, source_fingerprint=fingerprint
        )
        if fingerprint else None
    )
    if latest is None or UUID(str(finding["run_id"])) != UUID(str(latest["id"])):
        raise ConflictError("Only findings from the current review can be acknowledged.")
    updated = repo.acknowledge_finding(
        conn, finding_id, actor_id=actor_id, note=note.strip()
    )
    record_audit_event(
        conn,
        action="attorney_review.acknowledged",
        resource_type="attorney_review_finding",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=finding_id,
        after_summary={"check": str(finding["check_name"]), "note": note.strip()[:200]},
    )
    return _finding_out(updated)


__all__ = [
    "AttorneyReviewDisabledError",
    "AttorneyReviewNotReadyError",
    "acknowledge_finding",
    "get_findings",
    "record_render_snapshot",
    "run_review",
    "run_review_for_matter",
]
