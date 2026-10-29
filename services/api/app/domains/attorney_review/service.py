"""Attorney Reviewer — run service, snapshot recorder, acknowledgment.

The reviewer analyses ONLY the frozen artifact persisted at render time
(snapshots + the source payload for the packages' stamped fingerprint).
The one live read is the intake answers' draft-set resolution, used
solely to decide WHICH stamped fingerprint is the current one (an
orphaned package must not silence the reviewer); the analysis itself
never consumes live answers. It never touches a gate, and its findings
are advisory: nothing in this module can block generation, submission,
or release (plan 2026-10-27, design §9.7).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
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

# Per-matter run serialization (review N3): auto-runs now execute on
# background threads, so a generate run and a submit run (or a manual
# run) for the same matter can overlap. Idempotency is check-then-act
# in the prepare phase; without serialization two overlapping runs
# could both compute and both persist (duplicate model spend). A
# per-matter lock makes the second caller wait, after which its
# prepare phase sees the first run's completed row and returns it.
_run_locks: dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()


@contextmanager
def _matter_run_lock(matter_id: UUID) -> Iterator[None]:
    key = str(matter_id)
    with _run_locks_guard:
        lock = _run_locks.setdefault(key, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
    # Entries are never removed: one small Lock per matter that has
    # ever run a review is a bounded, acceptable footprint, and
    # removing one races with a waiter that already holds a reference
    # to it (which could then run concurrently with a fresh lock).


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
    # Identity slots the renderer fills from relationships/persons
    # rather than from _ANSWER_SLOT_TABLE (build_template_context):
    # their applicability evidence is the gated identity question, so
    # the placeholder check can still catch an answered-but-dropped
    # spouse/testator/children slot (review M3 — the defect class of
    # the 2026-10-27 spouse-capture fix).
    for slot, keys in {
        "spouse_name": ["spouse_full_name"],
        "testator_name": ["testator_full_name"],
        "grantor_name": ["testator_full_name"],
        "declarant_name": ["testator_full_name"],
        "principal_name": ["testator_full_name"],
        "parent_name": ["testator_full_name"],
        "children_list": ["minor_children_details", "has_minor_children"],
        # Composed prose slots: their blanks are the spouse/children
        # blanks (recital) and the gift-agent blank (appointment), so
        # the answered-but-dropped net follows them (review M-1).
        "family_identification": [
            "spouse_full_name", "minor_children_details", "has_minor_children",
        ],
        "anatomical_gift_appointment": [
            "healthcare_agent_person", "organ_donation_wishes",
        ],
    }.items():
        slot_answer_keys.setdefault(slot, []).extend(keys)
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


def _current_fingerprint(conn: Any, matter: dict[str, Any]) -> str | None:
    """The stamped fingerprint of the matter's CURRENT draft set.

    The current set is the document types the family's answers call
    for (documents' resolve_draft_set — the same definition the draft
    state uses), taking the latest package of each type. Packages
    outside the set — e.g. a trust the family removed from their plan
    and regenerated without — keep their old stamp and must not
    poison the reviewer into permanent silence (review H3). Mixed
    stamps WITHIN the set mean a regeneration is mid-flight: fail
    closed (None) until the set is uniform again.
    """
    from ...domains.documents.draft_set import resolve_draft_set
    from ...domains.intake import repository as intake_repo

    matter_id = UUID(str(matter["id"]))
    answers = {
        str(row["question_key"]): row["answer"]
        for row in intake_repo.list_answers(conn, matter_id)
    }
    expected = resolve_draft_set(answers)
    if not expected:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_fingerprint FROM ("
            " SELECT DISTINCT ON (t.template_type) p.source_fingerprint"
            " FROM document_packages p"
            " JOIN document_templates t ON t.id = p.template_id"
            " WHERE p.matter_id = %s AND p.source_fingerprint IS NOT NULL"
            " AND t.template_type = ANY(%s)"
            " ORDER BY t.template_type, p.created_at DESC, p.id"
            ") AS current_set",
            (str(matter_id), expected),
        )
        rows = [str(r["source_fingerprint"]) for r in cur.fetchall()]
    return rows[0] if rows and len(set(rows)) == 1 else None


def _execution_profiles(conn: Any, matter: dict[str, Any]) -> dict[str, Any]:
    """All execution profiles of the matter's pack, keyed by document_type.

    Pinned to the matter's snapshotted pack version: the reviewer
    judges a frozen draft, so the yardstick must be the pack version
    the matter was planned under, not whatever is newest on disk at
    run time (review L5).
    """
    from ..documents import service as documents_service

    pack_id, _jurisdiction_code, version = documents_service._matter_pack_ref(matter)
    loaded = documents_service._content_pack_files(pack_id, version)
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


def _ack_actor(conn: Any, matter: dict[str, Any], user_id: Any) -> str | None:
    """Who acknowledged a finding: "firm" or "family" (review L3).

    The packet shows the acknowledgment note; it must also show WHICH
    side recorded the override — the raw user id alone doesn't say.
    """
    if not user_id:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM firm_memberships WHERE user_id = %s"
            " AND firm_id = %s AND status = 'active'",
            (str(user_id), str(matter["firm_id"])),
        )
        if cur.fetchone():
            return "firm"
        cur.execute(
            "SELECT 1 FROM household_members WHERE user_id = %s"
            " AND household_id = %s",
            (str(user_id), str(matter["household_id"])),
        )
        if cur.fetchone():
            return "family"
    return None


def _finding_out(
    row: dict[str, Any], *, ack_actor: str | None = None
) -> sch.FindingOut:
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
        acknowledged_by_actor=ack_actor,
        acknowledged_at=row.get("acknowledged_at"),
    )


def _findings_out(
    conn: Any, matter: dict[str, Any], rows: list[dict[str, Any]]
) -> list[sch.FindingOut]:
    labels: dict[str, str | None] = {}
    out: list[sch.FindingOut] = []
    for row in rows:
        actor = row.get("acknowledged_by_user_id")
        key = str(actor) if actor else None
        if key and key not in labels:
            labels[key] = _ack_actor(conn, matter, actor)
        out.append(_finding_out(row, ack_actor=labels.get(key) if key else None))
    return out


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


def _is_full_run(run: dict[str, Any] | None) -> bool:
    """A completed run that included the model layer."""
    return bool(run) and str(run.get("model_id") or "") not in (
        "",
        "deterministic_only",
    )


class _PreparedReview:
    """Frozen inputs for one run, assembled in a short read transaction.

    The compute phase (deterministic checks + the model call) runs
    WITHOUT a database transaction — an LLM call must never hold a
    pooled connection or an open transaction (review M1). The write
    phase then persists everything in a second short transaction.
    """

    def __init__(
        self,
        *,
        matter: dict[str, Any],
        actor_id: UUID | None,
        fingerprint: str,
        snapshots: list[dict[str, Any]],
        source: dict[str, Any],
        profiles: dict[str, Any],
        review_date: date,
    ) -> None:
        self.matter = matter
        self.actor_id = actor_id
        self.fingerprint = fingerprint
        self.snapshots = snapshots
        self.source = source
        self.profiles = profiles
        # The frozen artifact's own date (when its source payload was
        # recorded at render): age arithmetic is judged as of the
        # packet, not as of whenever the run happens to execute
        # (review L5).
        self.review_date = review_date


def _prepare_review(
    conn: Any,
    *,
    matter: dict[str, Any],
    actor_id: UUID | None,
    force: bool,
    full: bool,
    quiet: bool,
) -> tuple[_PreparedReview | None, sch.RunResponseOut | None]:
    """Read phase: flag, frozen inputs, idempotency.

    Returns (prepared, None) when a run should execute, (None, response)
    for an idempotent hit, and (None, None) for a quiet skip.
    """
    from ...domains.feature_flags import service as feature_flags

    matter_id = UUID(str(matter["id"]))
    firm_id = UUID(str(matter["firm_id"]))
    pack_id, _jur, _ver = _pack_ref(matter)
    if not feature_flags.is_enabled(conn, FLAG_KEY, firm_id=firm_id, pack_id=pack_id):
        if quiet:
            return None, None
        raise AttorneyReviewDisabledError()
    fingerprint = _current_fingerprint(conn, matter)
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
            return None, None
        raise AttorneyReviewNotReadyError()
    if not force:
        existing = repo.latest_completed_run(
            conn, matter_id=matter_id, source_fingerprint=fingerprint
        )
        # A deterministic-only run satisfies a deterministic request,
        # and any completed run satisfies a repeat full request only if
        # it was itself full — a full request upgrades a det-only run.
        if existing is not None and (not full or _is_full_run(existing)):
            rows = repo.findings_for_run(conn, UUID(str(existing["id"])))
            return None, sch.RunResponseOut(
                run=_run_out(existing),
                findings=_findings_out(conn, matter, rows),
            )
    recorded = source_row.get("created_at")
    review_date = recorded.date() if hasattr(recorded, "date") else date.today()
    prepared = _PreparedReview(
        matter=matter,
        actor_id=actor_id,
        fingerprint=fingerprint,
        snapshots=snapshots,
        source=dict(source_row["payload"]),
        profiles=_execution_profiles(conn, matter),
        review_date=review_date,
    )
    return prepared, None


def _compute_findings(
    prepared: _PreparedReview, *, full: bool
) -> tuple[list[Finding], str, dict[str, int] | None]:
    """Compute phase: pure checks + (optionally) the model call.

    No database access. A provider failure degrades to the
    deterministic findings — the review itself must not fail.
    """
    findings = run_deterministic_checks(
        snapshots=prepared.snapshots,
        source=prepared.source,
        execution_profiles=prepared.profiles,
        today=prepared.review_date,
    )
    model_id = "deterministic_only"
    usage: dict[str, int] | None = None
    settings = get_settings()
    if (
        full
        and settings.AI_LLM_PROVIDER.strip().lower() == "openai"
        and settings.OPENAI_API_KEY
    ):
        candidate = settings.OPENAI_INTERVIEW_MODEL_V2
        try:
            model_findings, usage = model_review.review_via_model(
                snapshots=prepared.snapshots,
                source=prepared.source,
                model=candidate,
            )
            findings.extend(model_findings)
            model_id = candidate
        except Exception:  # provider failure must not fail the review
            logger.exception(
                "attorney_review model layer failed for matter %s; "
                "continuing with deterministic findings only",
                prepared.matter.get("id"),
            )
            usage = None
    # Idempotent by content hash across both layers.
    unique: dict[str, Finding] = {}
    for finding in findings:
        unique.setdefault(finding_hash(finding), finding)
    return list(unique.values()), model_id, usage


def _persist_review(
    conn: Any,
    *,
    prepared: _PreparedReview,
    findings: list[Finding],
    model_id: str,
    usage: dict[str, int] | None,
) -> sch.RunResponseOut:
    """Write phase: run row, disposition transitions, findings, audit."""
    matter = prepared.matter
    matter_id = UUID(str(matter["id"]))
    firm_id = UUID(str(matter["firm_id"]))
    run = repo.insert_run(
        conn,
        matter_id=matter_id,
        firm_id=firm_id,
        source_fingerprint=prepared.fingerprint,
        prompt_version=PROMPT_VERSION,
        created_by_user_id=prepared.actor_id,
    )
    run_id = UUID(str(run["id"]))
    new_hashes = {finding_hash(f) for f in findings}
    previous_run = repo.latest_completed_run(
        conn, matter_id=matter_id, exclude_run_id=run_id
    )
    previous_by_hash = _apply_disposition_transitions(
        conn,
        previous_run=previous_run,
        new_hashes=new_hashes,
    )
    # FUNC-024(b): acknowledgment CARRY-OVER is restricted to previous
    # runs with the SAME source_fingerprint as this run. Disposition
    # transitions above still use the latest run (regeneration changes
    # the fingerprint by definition, and fixed_by_regeneration must
    # keep working across it) — but an ack given against one frozen
    # source must never silently bless a finding produced from a
    # different source.
    if previous_run is not None and str(
        previous_run.get("source_fingerprint")
    ) == str(prepared.fingerprint):
        ack_by_hash = previous_by_hash
    elif prepared.fingerprint:
        same_fp_run = repo.latest_completed_run(
            conn,
            matter_id=matter_id,
            source_fingerprint=prepared.fingerprint,
            exclude_run_id=run_id,
        )
        ack_by_hash = (
            {
                str(row["finding_hash"]): row
                for row in repo.findings_for_run(conn, UUID(str(same_fp_run["id"])))
            }
            if same_fp_run is not None
            else {}
        )
    else:
        ack_by_hash = {}
    for finding in findings:
        digest = finding_hash(finding)
        carried = ack_by_hash.get(digest)
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
    if usage is not None:
        _record_trace(
            conn,
            matter=matter,
            firm_id=firm_id,
            model_id=model_id,
            usage=usage,
        )
    record_audit_event(
        conn,
        action="attorney_review.completed",
        resource_type="attorney_review_run",
        actor_user_id=prepared.actor_id,
        actor_type="user" if prepared.actor_id else "system",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=run_id,
        after_summary={
            "source_fingerprint": prepared.fingerprint,
            "findings_count": len(findings),
            "model_id": model_id,
        },
    )
    rows = repo.findings_for_run(conn, run_id)
    return sch.RunResponseOut(
        run=_run_out(completed),
        findings=_findings_out(conn, prepared.matter, rows),
    )


def _record_failed_run(prepared: _PreparedReview, error: BaseException) -> None:
    """Persist a 'failed' run row in its own transaction (review H4).

    The plan's lifecycle has a failed state; until now nothing could
    reach it — a compute/persist failure rolled back silently and the
    docstring's claim was fiction. Recording is itself best-effort:
    a failure here must never mask the original error.
    """
    from ...core import db

    try:
        with db.transaction() as conn:
            run = repo.insert_run(
                conn,
                matter_id=UUID(str(prepared.matter["id"])),
                firm_id=UUID(str(prepared.matter["firm_id"])),
                source_fingerprint=prepared.fingerprint,
                prompt_version=PROMPT_VERSION,
                created_by_user_id=prepared.actor_id,
            )
            repo.fail_run(conn, UUID(str(run["id"])), error=str(error)[:500])
    except Exception:
        logger.exception("attorney_review failed-run recording failed")


def run_review(
    *, actor_sub: str, matter_id: UUID, force: bool = False
) -> sch.RunResponseOut:
    """Run the pre-review over the matter's frozen package (endpoint).

    Idempotent per fingerprint: without ``force`` an existing completed
    run for the current fingerprint is returned as-is. ``force`` is
    honored only for firm-side actors — a family caller cannot burn
    repeated model calls (review L6); for them force degrades to the
    idempotent behavior. Manages its own transactions so the model
    call never runs inside one (review M1).
    """
    from ...core import db
    from ...domains.matters import service as matters_service

    actor_id = _actor_id(actor_sub)
    with _matter_run_lock(matter_id):
        with db.transaction() as conn:
            scope = matters_service.require_matter_write_scope(
                conn, matter_id, actor_id
            )
            prepared, early = _prepare_review(
                conn,
                matter=scope.matter,
                actor_id=actor_id,
                force=force and scope.via == "firm",
                full=True,
                quiet=False,
            )
        if early is not None:
            return early
        assert prepared is not None
        try:
            findings, model_id, usage = _compute_findings(prepared, full=True)
            with db.transaction() as conn:
                return _persist_review(
                    conn,
                    prepared=prepared,
                    findings=findings,
                    model_id=model_id,
                    usage=usage,
                )
        except Exception as exc:
            _record_failed_run(prepared, exc)
            raise


def run_review_for_matter(
    *,
    matter: dict[str, Any],
    actor_id: UUID | None,
    deterministic_only: bool = False,
) -> None:
    """Auto-run entry for the generate/submit hooks: never raises.

    Generate passes deterministic_only=True (instant, free — the model
    layer would otherwise bill a call on every regeneration, review
    M2); submit runs the full review, upgrading a deterministic-only
    run for the same fingerprint. Silent when the flag is off or no
    frozen package exists; failures are logged and swallowed — the
    reviewer advises on the core flow and must never break it.
    """
    try:
        with _matter_run_lock(UUID(str(matter["id"]))):
            _run_review_for_matter_locked(
                matter=matter,
                actor_id=actor_id,
                deterministic_only=deterministic_only,
            )
    except Exception:
        logger.exception(
            "attorney_review auto-run failed for matter %s", matter.get("id")
        )


def _run_review_for_matter_locked(
    *,
    matter: dict[str, Any],
    actor_id: UUID | None,
    deterministic_only: bool = False,
) -> None:
    """The auto-run body, executed under the per-matter run lock."""
    from ...core import db

    prepared: _PreparedReview | None = None
    try:
        with db.transaction() as conn:
            prepared, early = _prepare_review(
                conn,
                matter=matter,
                actor_id=actor_id,
                force=False,
                full=not deterministic_only,
                quiet=True,
            )
        if prepared is None:
            return
        findings, model_id, usage = _compute_findings(
            prepared, full=not deterministic_only
        )
        with db.transaction() as conn:
            _persist_review(
                conn,
                prepared=prepared,
                findings=findings,
                model_id=model_id,
                usage=usage,
            )
    except Exception as exc:
        if prepared is not None:
            _record_failed_run(prepared, exc)
        raise


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
    fingerprint = _current_fingerprint(conn, matter)
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
        findings=_findings_out(conn, matter, rows),
    )


def acknowledge_finding(
    conn: Any, *, actor_sub: str, matter_id: UUID, finding_id: UUID, note: str
) -> sch.FindingOut:
    """Acknowledge (override) a finding with a note — recorded, never a gate.

    Available to the family (write scope) and to firm staff; the
    acknowledgment is stored on the finding and travels with the packet.
    """
    from ...domains.feature_flags import service as feature_flags
    from ...domains.matters import service as matters_service

    actor_id = _actor_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    pack_id, _jur, _ver = _pack_ref(scope.matter)
    if not feature_flags.is_enabled(
        conn, FLAG_KEY,
        firm_id=UUID(str(scope.matter["firm_id"])), pack_id=pack_id,
    ):
        raise AttorneyReviewDisabledError()
    finding = repo.get_finding(conn, finding_id)
    if finding is None or UUID(str(finding["matter_id"])) != matter_id:
        raise NotFoundError("Finding not found.")
    fingerprint = _current_fingerprint(conn, scope.matter)
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
    if updated is None:
        # Lost a race with a re-run (or a duplicate ack): the finding
        # is no longer open, so there is nothing to override (L1).
        raise ConflictError(
            "This finding was already resolved or superseded by a newer review."
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
    return _finding_out(
        updated, ack_actor="firm" if scope.via == "firm" else "family"
    )


__all__ = [
    "AttorneyReviewDisabledError",
    "AttorneyReviewNotReadyError",
    "acknowledge_finding",
    "get_findings",
    "record_render_snapshot",
    "run_review",
    "run_review_for_matter",
]
