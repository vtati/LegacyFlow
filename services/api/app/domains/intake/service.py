"""Guided-intake application services (Slice 2, Track C).

Question sets are served from the jurisdiction pack snapshotted on the
matter (never the live pack); answers autosave with pack key validation;
progress persists the answer-driven completeness percentage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, ValidationAppError
from app.domains.intake import question_sets as qs
from app.domains.intake import repository as repo
from app.domains.intake.schemas import (
    AnswersOut,
    IntakeProgressOut,
    QuestionSetsOut,
)
from app.domains.matters import repository as matters_repo
from app.domains.matters import service as matters_service
from app.domains.matters.state_machine import MatterStatus

# Intake writes are only meaningful while the household is actively
# answering (or responding to attorney clarification).
WRITABLE_STATUSES = frozenset(
    {MatterStatus.INTAKE_IN_PROGRESS.value, MatterStatus.CLARIFICATION_REQUESTED.value}
)


def _require_writable_status(matter: dict[str, Any]) -> None:
    if str(matter["status"]) not in WRITABLE_STATUSES:
        raise ConflictError(
            "Intake answers can only be saved while intake is in progress "
            "or clarification has been requested."
        )


def _answers_out(conn: Any, matter_id: UUID) -> AnswersOut:
    rows = repo.list_answers(conn, matter_id)
    answers = {str(row["question_key"]): row["answer"] for row in rows}
    updated = [row["updated_at"] for row in rows if row.get("updated_at")]
    latest: datetime | None = max(updated) if updated else None
    return AnswersOut(answers=answers, updated_at=latest)


def get_question_sets(conn: Any, *, actor_sub: str, matter_id: UUID) -> QuestionSetsOut:
    """Serve the matter's snapshotted pack question sets (read access)."""
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    pack, code, version = matters_service.load_matter_pack(conn, matter_id)
    return QuestionSetsOut(
        jurisdiction_code=code,
        pack_version=version,
        question_sets=pack.question_sets,
    )


def save_answers(
    conn: Any, *, actor_sub: str, matter_id: UUID, answers: dict[str, Any]
) -> AnswersOut:
    """Autosave answers: validate keys against the pack, then upsert.

    Audit records summary counts only — never answer values (contract
    rule 9).
    """
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    _require_writable_status(scope.matter)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    known_keys = qs.all_question_keys(pack.question_sets)
    unknown = sorted(key for key in answers if key not in known_keys)
    if unknown:
        raise ValidationAppError(f"Unknown question key(s): {', '.join(unknown)}.")
    repo.upsert_answers(conn, matter_id, answers)
    record_audit_event(
        conn,
        action="intake.answers_saved",
        resource_type="matter",
        actor_user_id=actor_id,
        actor_type="user",
        firm_id=UUID(str(scope.matter["firm_id"])),
        matter_id=matter_id,
        resource_id=matter_id,
        after_summary={"saved_count": len(answers)},
    )
    return _answers_out(conn, matter_id)


def get_answers(conn: Any, *, actor_sub: str, matter_id: UUID) -> AnswersOut:
    """Return the matter's stored answers (read access)."""
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    return _answers_out(conn, matter_id)


def get_progress(conn: Any, *, actor_sub: str, matter_id: UUID) -> IntakeProgressOut:
    """Return per-set progress and persist the overall completeness.

    The overall percentage is the required-answered share across all
    question sets; it is written back to ``matters.completeness_pct`` so
    submission and review see the answer-driven value.
    """
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    answers = {
        str(row["question_key"]): row["answer"] for row in repo.list_answers(conn, matter_id)
    }
    per_set, overall, _missing = qs.compute_progress(pack.question_sets, answers)
    # ``matters.completeness_pct`` is an integer column (Track A Slice 2
    # migration): round once here so the persisted value and the response agree.
    overall = float(int(round(overall)))
    matters_repo.update_completeness(conn, matter_id, overall)
    return IntakeProgressOut(sets=per_set, completeness_pct=overall)
