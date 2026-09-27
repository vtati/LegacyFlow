"""Matter lifecycle state machine (FR-MAT-004).

Pure domain logic — no I/O, no DB — so it is unit-testable in isolation.
Services call :func:`validate_transition` before persisting any status
change and record the change in ``matter_status_history`` plus an audit
event. Invalid transitions are rejected server-side and audited (§12.2.4).

Approval gates (FR-REV-004/005): some transitions additionally require a
recorded attorney approval gate. :data:`GATE_REQUIRED` names the gate type;
services enforce it — the machine only declares it.
"""

from __future__ import annotations

from enum import Enum

from app.core.errors import ValidationAppError


class MatterStatus(str, Enum):  # noqa: UP042 -- str-mixin keeps .value semantics stable
    """Controlled matter lifecycle states."""

    DRAFT = "DRAFT"
    INTAKE_IN_PROGRESS = "INTAKE_IN_PROGRESS"
    INTAKE_SUBMITTED = "INTAKE_SUBMITTED"
    ATTORNEY_REVIEW = "ATTORNEY_REVIEW"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    ESCALATED = "ESCALATED"
    APPROVED_FOR_ASSEMBLY = "APPROVED_FOR_ASSEMBLY"
    DOCUMENTS_RELEASED = "DOCUMENTS_RELEASED"
    SIGNING_IN_PROGRESS = "SIGNING_IN_PROGRESS"
    FUNDING_IN_PROGRESS = "FUNDING_IN_PROGRESS"
    PLAN_IMPLEMENTED = "PLAN_IMPLEMENTED"
    ARCHIVED = "ARCHIVED"


_TRANSITIONS: dict[MatterStatus, frozenset[MatterStatus]] = {
    MatterStatus.DRAFT: frozenset({MatterStatus.INTAKE_IN_PROGRESS, MatterStatus.ARCHIVED}),
    # A blocking escalation discovered during intake routes the matter to
    # ESCALATED for attorney disposition (Slice 2 guided intake).
    MatterStatus.INTAKE_IN_PROGRESS: frozenset(
        {MatterStatus.INTAKE_SUBMITTED, MatterStatus.ESCALATED, MatterStatus.ARCHIVED}
    ),
    MatterStatus.INTAKE_SUBMITTED: frozenset(
        {MatterStatus.ATTORNEY_REVIEW, MatterStatus.ESCALATED, MatterStatus.ARCHIVED}
    ),
    MatterStatus.ATTORNEY_REVIEW: frozenset(
        {
            MatterStatus.CLARIFICATION_REQUESTED,
            MatterStatus.APPROVED_FOR_ASSEMBLY,
            MatterStatus.ESCALATED,
            MatterStatus.ARCHIVED,
        }
    ),
    MatterStatus.CLARIFICATION_REQUESTED: frozenset(
        {MatterStatus.ATTORNEY_REVIEW, MatterStatus.ESCALATED, MatterStatus.ARCHIVED}
    ),
    MatterStatus.ESCALATED: frozenset({MatterStatus.ATTORNEY_REVIEW, MatterStatus.ARCHIVED}),
    MatterStatus.APPROVED_FOR_ASSEMBLY: frozenset(
        {MatterStatus.DOCUMENTS_RELEASED, MatterStatus.ATTORNEY_REVIEW, MatterStatus.ARCHIVED}
    ),
    MatterStatus.DOCUMENTS_RELEASED: frozenset(
        {MatterStatus.SIGNING_IN_PROGRESS, MatterStatus.ARCHIVED}
    ),
    MatterStatus.SIGNING_IN_PROGRESS: frozenset(
        {MatterStatus.FUNDING_IN_PROGRESS, MatterStatus.ARCHIVED}
    ),
    MatterStatus.FUNDING_IN_PROGRESS: frozenset(
        {MatterStatus.PLAN_IMPLEMENTED, MatterStatus.ARCHIVED}
    ),
    # Annual checkup / life-event updates can route an implemented plan back
    # through attorney review (FR-FUND-005).
    MatterStatus.PLAN_IMPLEMENTED: frozenset({MatterStatus.ATTORNEY_REVIEW, MatterStatus.ARCHIVED}),
    MatterStatus.ARCHIVED: frozenset(),
}

# (from, to) -> gate type that must exist before the transition is allowed.
# Services verify the gate record; the machine only declares the requirement.
GATE_REQUIRED: dict[tuple[MatterStatus, MatterStatus], str] = {
    (MatterStatus.ATTORNEY_REVIEW, MatterStatus.APPROVED_FOR_ASSEMBLY): "intake_approval",
    (MatterStatus.APPROVED_FOR_ASSEMBLY, MatterStatus.DOCUMENTS_RELEASED): "document_release",
}


def _coerce_status(value: MatterStatus | str) -> MatterStatus:
    """Return the enum member or raise a 422-style error for unknown states."""
    try:
        return MatterStatus(value)
    except ValueError as exc:
        raise ValidationAppError(f"Unknown matter status: {value!r}.") from exc


def allowed_transitions(from_status: MatterStatus | str) -> list[str]:
    """Return the valid target states for ``from_status``."""
    current = _coerce_status(from_status)
    return sorted(t.value for t in _TRANSITIONS[current])


def validate_transition(from_status: MatterStatus | str, to_status: MatterStatus | str) -> None:
    """Raise :class:`ValidationAppError` when the transition is not allowed."""
    current = _coerce_status(from_status)
    target = _coerce_status(to_status)
    if target not in _TRANSITIONS[current]:
        raise ValidationAppError(
            f"Transition from {current.value} to {target.value} is not allowed."
        )


def gate_required_for(from_status: MatterStatus | str, to_status: MatterStatus | str) -> str | None:
    """Return the approval-gate type required for a transition, if any."""
    return GATE_REQUIRED.get((_coerce_status(from_status), _coerce_status(to_status)))
