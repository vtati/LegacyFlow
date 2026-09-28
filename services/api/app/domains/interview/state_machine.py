"""Interview session state machine (Phase 1).

Mirrors the matters state-machine pattern: explicit enum, validated
transitions, no arbitrary strings. Pause/resume is free and unlimited —
a session only ends via completion, abandonment, or explicit user action.
"""

from __future__ import annotations

from enum import Enum

from app.core.errors import ConflictError


class SessionStatus(str, Enum):  # noqa: UP042 -- str-mixin keeps .value semantics stable
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.DRAFT: frozenset({SessionStatus.IN_PROGRESS, SessionStatus.ABANDONED}),
    SessionStatus.IN_PROGRESS: frozenset(
        {SessionStatus.PAUSED, SessionStatus.COMPLETED, SessionStatus.ABANDONED}
    ),
    SessionStatus.PAUSED: frozenset({SessionStatus.IN_PROGRESS, SessionStatus.ABANDONED}),
    # Terminal states: no outgoing transitions.
    SessionStatus.COMPLETED: frozenset(),
    SessionStatus.ABANDONED: frozenset(),
}


def validate_transition(current: SessionStatus, target: SessionStatus) -> None:
    """Raise ConflictError when ``current -> target`` is not allowed."""
    if target not in _TRANSITIONS[current]:
        raise ConflictError(
            f"Interview session cannot move from {current.value} to {target.value}."
        )


#: Statuses that count as "active" for the one-active-session uniqueness rule.
ACTIVE_STATUSES = frozenset({SessionStatus.DRAFT, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED})
