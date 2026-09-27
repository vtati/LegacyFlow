"""Pack question-set helpers (Slice 2, Track C).

Pure functions over parsed pack YAML — no I/O — so they are unit-testable
in isolation. They power key validation, progress math, and the facts dict
that escalation-rule evaluation runs against.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.domains.intake.schemas import QuestionSetProgress

#: Prefix marking a ``maps_to`` binding as an escalation fact.
FACTS_PREFIX = "facts."


def iter_questions(
    question_sets: dict[str, dict[str, Any]],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(set_id, section_id, question)`` for every question."""
    for set_id, question_set in question_sets.items():
        for section in question_set.get("sections", []):
            for question in section.get("questions", []):
                yield set_id, str(section.get("id", "")), dict(question)


def all_question_keys(question_sets: dict[str, dict[str, Any]]) -> set[str]:
    """Return every question key declared by the pack's question sets."""
    return {str(question["key"]) for _, _, question in iter_questions(question_sets)}


def is_answered(value: Any) -> bool:
    """A question counts as answered with a non-null, non-empty answer.

    ``False`` and ``0`` are legitimate answers (triage booleans and counts);
    only ``None``, ``""``, ``[]`` and ``{}`` count as unanswered.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def compute_progress(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
) -> tuple[dict[str, QuestionSetProgress], float, list[str]]:
    """Compute per-set progress and overall required-question completeness.

    Returns ``(per_set, completeness_pct, missing_required_keys)`` where
    ``completeness_pct`` is the required-answered share across all sets
    (100.0 when the pack declares no required questions) and
    ``missing_required_keys`` lists unanswered required keys in pack order.
    """
    totals: dict[str, dict[str, int]] = {}
    order: list[str] = []
    missing: list[str] = []
    for set_id, _, question in iter_questions(question_sets):
        key = str(question["key"])
        stats = totals.setdefault(set_id, {"total": 0, "answered": 0, "req": 0, "req_ok": 0})
        if set_id not in order:
            order.append(set_id)
        stats["total"] += 1
        answered = is_answered(answers.get(key))
        if answered:
            stats["answered"] += 1
        if question.get("required"):
            stats["req"] += 1
            if answered:
                stats["req_ok"] += 1
            else:
                missing.append(key)

    per_set: dict[str, QuestionSetProgress] = {}
    for set_id in order:
        stats = totals[set_id]
        total = stats["total"]
        per_set[set_id] = QuestionSetProgress(
            total_questions=total,
            answered=stats["answered"],
            required_total=stats["req"],
            required_answered=stats["req_ok"],
            percent_complete=round(100.0 * stats["answered"] / total, 2) if total else 0.0,
        )
    req_total = sum(stats["req"] for stats in totals.values())
    req_ok = sum(stats["req_ok"] for stats in totals.values())
    overall = round(100.0 * req_ok / req_total, 2) if req_total else 100.0
    return per_set, overall, missing


def build_facts(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
) -> dict[str, Any]:
    """Build the escalation facts dict from stored answers.

    Only questions whose ``maps_to`` starts with ``"facts."`` contribute;
    the prefix is stripped and any remaining dotted path nests the value.
    Unanswered questions contribute nothing (missing facts fail closed in
    rule evaluation).
    """
    facts: dict[str, Any] = {}
    for _, _, question in iter_questions(question_sets):
        maps_to = question.get("maps_to")
        if not isinstance(maps_to, str) or not maps_to.startswith(FACTS_PREFIX):
            continue
        key = str(question["key"])
        if key not in answers or answers[key] is None:
            continue
        node = facts
        path = maps_to[len(FACTS_PREFIX) :].split(".")
        for part in path[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[path[-1]] = answers[key]
    return facts


def sanitize_facts_snapshot(facts: dict[str, Any]) -> dict[str, Any]:
    """Reduce a facts dict to snapshot-safe triage values.

    Escalation facts are triage booleans/numbers; free-text answers
    (strings), selections (lists) and nested objects are dropped so they
    are never persisted in ``escalations.facts_snapshot``.
    """
    clean: dict[str, Any] = {}
    for key, value in facts.items():
        if isinstance(value, dict):
            nested = sanitize_facts_snapshot(value)
            if nested:
                clean[key] = nested
        elif isinstance(value, (bool, int, float)):
            clean[key] = value
    return clean
