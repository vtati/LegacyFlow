"""Escalation application services (Slice 2, Track C).

``evaluate_intake`` builds the facts dict from stored intake answers and
evaluates the matter's snapshotted pack escalation rules in pack order:

* every triggered rule is recorded as an ``open`` escalation (idempotent
  per rule while it stays open),
* any ``blocking`` severity transitions the matter to ESCALATED with a
  status-history row and audit events,
* ``review``/``info`` severities are recorded only — the attorney sees
  them at review.

Facts snapshots keep triage booleans/numbers only; free-text answers are
never persisted (contract rule 9).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core import authz
from app.core.audit import record_audit_event
from app.core.errors import ConfigurationError, ConflictError, ValidationAppError
from app.domains.documents.draft_set import resolve_draft_documents
from app.domains.escalation import repository as repo
from app.domains.escalation.schemas import (
    CompletenessOut,
    EscalationOut,
    EvaluateOut,
    MissingAnswerOut,
    TriggeredRuleOut,
)
from app.domains.intake import question_sets as intake_questions
from app.domains.intake import repository as intake_repo
from app.domains.legal_content.pack_loader import evaluate_rule_expression
from app.domains.matters import repository as matters_repo
from app.domains.matters import service as matters_service
from app.domains.matters.state_machine import MatterStatus, validate_transition

logger = logging.getLogger(__name__)

EVALUATABLE_STATUSES = frozenset(
    {MatterStatus.INTAKE_IN_PROGRESS.value, MatterStatus.CLARIFICATION_REQUESTED.value}
)

ESCALATION_STATUSES = frozenset({"open", "resolved", "dismissed"})


def _escalation_out(row: dict[str, Any]) -> EscalationOut:
    return EscalationOut(
        id=UUID(str(row["id"])),
        matter_id=UUID(str(row["matter_id"])),
        rule_id=str(row["rule_id"]),
        severity=str(row["severity"]),
        outcome=str(row["outcome"]),
        facts_snapshot=dict(row.get("facts_snapshot") or {}),
        status=str(row["status"]),
        created_at=row["created_at"],
    )


def _apply_blocking_transition(conn: Any, *, scope: authz.MatterScope, actor_id: UUID) -> None:
    """Transition the matter to ESCALATED with history + audit."""
    from_status = str(scope.matter["status"])
    matter_id = UUID(str(scope.matter["id"]))
    firm_id = UUID(str(scope.matter["firm_id"]))
    validate_transition(from_status, MatterStatus.ESCALATED)
    current_pct = scope.matter.get("completeness_pct")
    matters_repo.update_matter_status(
        conn,
        matter_id,
        to_status=MatterStatus.ESCALATED.value,
        completeness_pct=float(current_pct) if current_pct is not None else 0.0,
        submitted_at=None,
    )
    matters_repo.insert_status_history(
        conn,
        matter_id=matter_id,
        from_status=from_status,
        to_status=MatterStatus.ESCALATED.value,
        reason_code="blocking_escalation",
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
        after_summary={"status": MatterStatus.ESCALATED.value},
    )


def evaluate_intake(conn: Any, *, actor_sub: str, matter_id: UUID) -> EvaluateOut:
    """Evaluate the matter's intake facts against its pack escalation rules."""
    actor_id = matters_service.caller_id(actor_sub)
    scope = matters_service.require_matter_write_scope(conn, matter_id, actor_id)
    matter = scope.matter
    if str(matter["status"]) not in EVALUATABLE_STATUSES:
        raise ConflictError(
            "Escalation evaluation is only available while intake is in "
            "progress or clarification has been requested."
        )
    firm_id = UUID(str(matter["firm_id"]))

    pack, _code, _version = matters_service.load_matter_pack(conn, matter_id)
    answers = {
        str(row["question_key"]): row["answer"] for row in intake_repo.list_answers(conn, matter_id)
    }
    facts = intake_questions.build_facts(pack.question_sets, answers)
    snapshot = intake_questions.sanitize_facts_snapshot(facts)

    triggered: list[TriggeredRuleOut] = []
    for rule in pack.escalation_rules:
        try:
            fires = evaluate_rule_expression(rule.when, facts)
        except ValueError as exc:
            # Fail closed: a pack rule that cannot be evaluated must not be
            # silently skipped. Rule ids are content metadata, safe to log.
            logger.error("Invalid escalation rule expression", extra={"rule_id": rule.id})
            raise ConfigurationError(
                f"Escalation rule {rule.id} has an invalid expression."
            ) from exc
        if not fires:
            continue
        if not repo.open_escalation_exists(conn, matter_id, rule.id):
            # ERR-016: savepoint-isolated — one bad row degrades to a
            # skipped escalation + log; siblings (and the response,
            # which reports the rule as triggered) still persist.
            repo.insert_escalation_isolated(
                conn,
                matter_id=matter_id,
                rule_id=rule.id,
                severity=rule.severity,
                outcome=rule.outcome,
                facts_snapshot=snapshot,
            )
        triggered.append(
            TriggeredRuleOut(
                rule_id=rule.id,
                severity=rule.severity,
                outcome=rule.outcome,
                user_message=rule.user_message,
            )
        )

    has_blocking = any(rule.severity == "blocking" for rule in triggered)
    if triggered:
        record_audit_event(
            conn,
            action="escalations.evaluated",
            resource_type="matter",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=firm_id,
            matter_id=matter_id,
            resource_id=matter_id,
            after_summary={
                "triggered_rule_ids": [rule.rule_id for rule in triggered],
                "has_blocking": has_blocking,
            },
        )
    if has_blocking:
        record_audit_event(
            conn,
            action="escalations.triggered",
            resource_type="matter",
            actor_user_id=actor_id,
            actor_type="user",
            firm_id=firm_id,
            matter_id=matter_id,
            resource_id=matter_id,
            after_summary={
                "triggered_rule_ids": [
                    rule.rule_id for rule in triggered if rule.severity == "blocking"
                ],
            },
        )
        _apply_blocking_transition(conn, scope=scope, actor_id=actor_id)
    # The family-facing half of the check (2026-10-31): completeness of
    # applicable required answers and the projected document set, both
    # computed from the same stored answers + pack partition the
    # progress endpoint and the submit gate use — never a second,
    # divergent notion of "complete".
    completeness = intake_questions.build_completeness(pack.question_sets, answers)
    return EvaluateOut(
        triggered=triggered,
        has_blocking=has_blocking,
        checked_at=datetime.now(UTC),
        completeness=CompletenessOut(
            required_total=completeness["required_total"],
            required_answered=completeness["required_answered"],
            missing=[MissingAnswerOut(**entry) for entry in completeness["missing"]],
        ),
        documents=resolve_draft_documents(answers),
    )


def list_escalations(
    conn: Any, *, actor_sub: str, matter_id: UUID, status: str | None = None
) -> list[EscalationOut]:
    """List a matter's escalations, optionally filtered by status."""
    actor_id = matters_service.caller_id(actor_sub)
    matters_service.require_matter_read_scope(conn, matter_id, actor_id)
    if status is not None and status not in ESCALATION_STATUSES:
        raise ValidationAppError(f"Unknown escalation status filter: {status}.")
    return [_escalation_out(row) for row in repo.list_escalations(conn, matter_id, status)]
