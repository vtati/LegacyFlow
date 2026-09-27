"""Intake submission (Slice 2, Track C).

``POST /v1/matters/{id}/submit`` moves a matter from INTAKE_IN_PROGRESS to
INTAKE_SUBMITTED once every precondition holds:

* the caller has intake write access (firm staff+ or household
  primary_client/collaborator),
* every required question in the matter's snapshotted pack is answered,
* no ``blocking`` escalation is still open.

Failures are HTTP 409 with a machine-readable ``code`` plus the offending
keys or rule ids. Nothing here invents legal behavior: submission only
records that the household finished the guided questionnaire.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.audit import record_audit_event
from app.core.errors import ConflictError
from app.domains.escalation import repository as escalation_repo
from app.domains.intake import question_sets as intake_questions
from app.domains.intake import repository as intake_repo
from app.domains.matters import repository as repo
from app.domains.matters import service as matters_service
from app.domains.matters.schemas import MatterOut
from app.domains.matters.state_machine import MatterStatus, validate_transition
from app.domains.review import repository as review_repo


class IntakeIncompleteError(ConflictError):
    """Required intake questions are still unanswered (HTTP 409)."""

    type = "urn:legacyflow:error:intake_incomplete"
    title = "Intake incomplete"
    code = "intake_incomplete"

    def __init__(self, missing: list[str]) -> None:
        super().__init__(detail=f"{len(missing)} required question(s) are still unanswered.")
        self.missing = list(missing)

    def to_response(self) -> dict[str, Any]:
        return {**super().to_response(), "code": self.code, "missing": self.missing}


class BlockingEscalationsOpenError(ConflictError):
    """Open blocking escalations must be dispositioned first (HTTP 409)."""

    type = "urn:legacyflow:error:blocking_escalations_open"
    title = "Blocking escalations open"
    code = "blocking_escalations_open"

    def __init__(self, rules: list[str]) -> None:
        super().__init__(detail=f"{len(rules)} blocking escalation(s) are still open.")
        self.rules = list(rules)

    def to_response(self) -> dict[str, Any]:
        return {**super().to_response(), "code": self.code, "rules": self.rules}


class InvalidMatterStatusError(ConflictError):
    """Submission was attempted from a non-submittable status (HTTP 409)."""

    type = "urn:legacyflow:error:invalid_matter_status"
    title = "Invalid matter status"
    code = "invalid_matter_status"

    def __init__(self, status: str) -> None:
        super().__init__(
            detail=(f"Submission requires status INTAKE_IN_PROGRESS; the matter is {status}.")
        )
        self.status_value = status

    def to_response(self) -> dict[str, Any]:
        return {**super().to_response(), "code": self.code}


def submit_matter(conn: Any, *, actor_sub: str, matter_id: UUID) -> MatterOut:
    """Submit a completed intake for attorney review.

    Recomputes answer-driven completeness from the matter's snapshotted
    pack (persisting it to ``matters.completeness_pct``), then enforces the
    submit preconditions before transitioning to INTAKE_SUBMITTED.
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    matter = scope.matter
    firm_id = UUID(str(matter["firm_id"]))
    from_status = str(matter["status"])
    if from_status != MatterStatus.INTAKE_IN_PROGRESS.value:
        raise InvalidMatterStatusError(from_status)

    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    answers = {
        row["question_key"]: row["answer"] for row in intake_repo.list_answers(conn, matter_id)
    }
    _per_set, overall, missing = intake_questions.compute_progress(pack.question_sets, answers)
    # ``matters.completeness_pct`` is an integer column (Track A Slice 2
    # migration): round once here so the persisted value, the audit trail,
    # and the response all agree.
    overall = float(int(round(overall)))
    repo.update_completeness(conn, matter_id, overall)
    if missing:
        raise IntakeIncompleteError(missing)

    blocking_rule_ids = escalation_repo.list_open_blocking_rule_ids(conn, matter_id)
    if blocking_rule_ids:
        raise BlockingEscalationsOpenError(blocking_rule_ids)

    # ``overall`` is 100.0 here: compute_progress derives it from the same
    # required-question set that ``missing`` covers.
    validate_transition(from_status, MatterStatus.INTAKE_SUBMITTED)
    submitted_at = datetime.now(UTC)
    updated = repo.update_matter_status(
        conn,
        matter_id,
        to_status=MatterStatus.INTAKE_SUBMITTED.value,
        completeness_pct=overall,
        submitted_at=submitted_at,
    )
    repo.insert_status_history(
        conn,
        matter_id=matter_id,
        from_status=from_status,
        to_status=MatterStatus.INTAKE_SUBMITTED.value,
        reason_code="intake_submitted",
        actor_user_id=actor_id,
    )
    record_audit_event(
        conn,
        action="matters.transitioned",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=matter_id,
        before_summary={"status": from_status},
        after_summary={"status": MatterStatus.INTAKE_SUBMITTED.value},
    )
    record_audit_event(
        conn,
        action="matters.submitted",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=firm_id,
        matter_id=matter_id,
        resource_id=matter_id,
        after_summary={"completeness_pct": overall},
    )
    # Slice 3: notify the firm team that a matter is ready for attorney review.
    recipients = review_repo.firm_staff_recipients(conn, firm_id)
    for recipient in recipients:
        review_repo.enqueue_notification(
            conn,
            firm_id=firm_id,
            matter_id=matter_id,
            template_key="matter.submitted",
            payload={
                "matter_id": str(matter_id),
                "status": MatterStatus.INTAKE_SUBMITTED.value,
                "title": str(matter.get("title") or ""),
            },
            recipient_user_id=UUID(str(recipient["user_id"])),
            recipient_email=str(recipient["email"]),
        )
    return matters_service.matter_out(updated)
