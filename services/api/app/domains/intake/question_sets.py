"""Pack question-set helpers (Slice 2, Track C).

Pure functions over parsed pack YAML — no I/O — so they are unit-testable
in isolation. They power key validation, progress math, and the facts dict
that escalation-rule evaluation runs against.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from app.domains.intake.schemas import QuestionSetProgress

#: Prefix marking a ``maps_to`` binding as an escalation fact.
FACTS_PREFIX = "facts."


# ---------------------------------------------------------------------------
# ``applies_if`` evaluator (Interview v2 applicability engine)
#
# A small recursive-descent evaluator over intake ANSWERS (flat mapping of
# question key -> JSON value) with Kleene three-valued logic:
#
#   True  — the rule is decisively satisfied
#   False — the rule is decisively violated (the question is pruned)
#   None  — a referenced gate key is unanswered (the question is PENDING:
#           fail-closed, still counted and still askable, never pruned)
#
# Grammar (no eval):
#   expr       := or_expr
#   or_expr    := and_expr ("or" and_expr)*
#   and_expr   := not_expr ("and" not_expr)*
#   not_expr   := "not" not_expr | comparison
#   comparison := operand (("==" | "!=") operand)?
#   operand    := identifier | "true" | "false" | number | 'string' | "(" expr ")"
# ---------------------------------------------------------------------------

_APPLIES_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<op>==|!=|\(|\))
    | (?P<bool>true|false)\b
    | (?P<number>-?\d+(?:\.\d+)?)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)
_APPLIES_KEYWORDS = {"and", "or", "not"}

#: Sentinel for "referenced key has no usable answer yet".
_UNKNOWN = object()


def _tokenize_applies(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expression):
        match = _APPLIES_TOKEN_RE.match(expression, pos)
        if not match or match.end() == pos:
            raise ValueError(
                f"Cannot tokenize applies_if expression near: {expression[pos : pos + 20]!r}"
            )
        kind = match.lastgroup or "ws"
        pos = match.end()
        if kind == "ws":
            continue
        tokens.append((kind, match.group()))
    return tokens


class _AppliesParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> Any:
        node = self._parse_or()
        if self._pos != len(self._tokens):
            raise ValueError("Unexpected trailing tokens in applies_if expression.")
        return node

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ValueError("Unexpected end of applies_if expression.")
        self._pos += 1
        return token

    def _parse_or(self) -> Any:
        node = self._parse_and()
        while self._peek() == ("word", "or"):
            self._next()
            node = ("or", node, self._parse_and())
        return node

    def _parse_and(self) -> Any:
        node = self._parse_not()
        while self._peek() == ("word", "and"):
            self._next()
            node = ("and", node, self._parse_not())
        return node

    def _parse_not(self) -> Any:
        if self._peek() == ("word", "not"):
            self._next()
            return ("not", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_operand()
        peek = self._peek()
        if peek is not None and peek[0] == "op" and peek[1] in ("==", "!="):
            self._next()
            right = self._parse_operand()
            return ("cmp", peek[1], left, right)
        return left

    def _parse_operand(self) -> Any:
        kind, text = self._next()
        if kind == "op" and text == "(":
            node = self._parse_or()
            if self._next() != ("op", ")"):
                raise ValueError("Missing closing parenthesis in applies_if expression.")
            return node
        if kind == "bool":
            return ("lit", text == "true")
        if kind == "number":
            return ("lit", float(text) if "." in text else int(text))
        if kind == "string":
            return ("lit", re.sub(r"\\(.)", r"\1", text[1:-1]))
        if kind == "word":
            if text in _APPLIES_KEYWORDS:
                raise ValueError(f"Unexpected keyword {text!r} in applies_if expression.")
            if text in ("True", "False", "TRUE", "FALSE"):
                raise ValueError(
                    f"Boolean literals in applies_if are lowercase: {text!r}."
                )
            return ("key", text)
        raise ValueError(f"Unexpected token {text!r} in applies_if expression.")


def _eval_raw(node: Any, answers: Mapping[str, Any]) -> Any:
    """Evaluate an AST node to a RAW value (literal / answer value / _UNKNOWN
    for an unanswered key) — used at comparison leaves."""
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "key":
        value = answers.get(node[1])
        return value if is_answered(value) else _UNKNOWN
    # A parenthesised boolean sub-expression used as an operand: its
    # three-valued truth result is the value (None maps back to _UNKNOWN;
    # the grammar has no null literal, so None only ever means "pending").
    result = _eval_bool(node, answers)
    return _UNKNOWN if result is None else result


def _truth_of(value: Any) -> bool | None:
    if value is _UNKNOWN:
        return None
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return bool(value)


def _eval_bool(node: Any, answers: Mapping[str, Any]) -> bool | None:
    kind = node[0]
    if kind in ("lit", "key"):
        return _truth_of(_eval_raw(node, answers))
    if kind == "cmp":
        left = _eval_raw(node[2], answers)
        right = _eval_raw(node[3], answers)
        if left is _UNKNOWN or right is _UNKNOWN:
            return None
        # Type-strict across the bool/number family: Python's True == 1
        # conflation must not make `count == true` decisive.
        if isinstance(left, bool) != isinstance(right, bool) and isinstance(
            left, (bool, int, float)
        ) and isinstance(right, (bool, int, float)):
            return node[1] == "!="
        return bool(left == right) if node[1] == "==" else bool(left != right)
    if kind == "not":
        inner = _eval_bool(node[1], answers)
        return None if inner is None else not inner
    if kind == "and":
        left = _eval_bool(node[1], answers)
        right = _eval_bool(node[2], answers)
        if left is False or right is False:
            return False
        if left is None or right is None:
            return None
        return True
    if kind == "or":
        left = _eval_bool(node[1], answers)
        right = _eval_bool(node[2], answers)
        if left is True or right is True:
            return True
        if left is None or right is None:
            return None
        return False
    raise ValueError(f"Unknown applies_if AST node {kind!r}.")


def evaluate_applies_if(expression: str, answers: Mapping[str, Any]) -> bool | None:
    """Evaluate an ``applies_if`` rule against intake answers.

    Returns True/False when the rule is decisive, None when any referenced
    key is unanswered and the outcome is not already decided by the other
    side of an ``and``/``or`` (Kleene logic). Raises ValueError on a
    malformed expression.
    """
    tokens = _tokenize_applies(expression)
    if not tokens:
        raise ValueError("Empty applies_if expression.")
    ast = _AppliesParser(tokens).parse()
    return _eval_bool(ast, answers)


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


def _applies_state(expr: Any, answers: Mapping[str, Any], *, where: str) -> bool | None:
    """Evaluate one ``applies_if`` slot: absent -> True; a present but
    non-string value is a pack authoring error and fails loud."""
    if expr is None:
        return True
    if not isinstance(expr, str):
        raise ValueError(f"applies_if at {where} must be a string expression.")
    if not expr.strip():
        return True
    return evaluate_applies_if(expr, answers)


def _partition_rows(
    question_sets: dict[str, dict[str, Any]],
    answers: Mapping[str, Any],
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Every question with its applicability state, in pack order.

    State combines the section's and the question's ``applies_if``: any
    False -> "pruned"; else any None -> "pending"; else "applicable".
    """
    rows: list[tuple[str, str, str, dict[str, Any]]] = []
    for set_id, question_set in question_sets.items():
        for section in question_set.get("sections", []):
            section_id = str(section.get("id", ""))
            section_state = _applies_state(
                section.get("applies_if"), answers, where=f"{set_id}.{section_id}"
            )
            for question in section.get("questions", []):
                question = dict(question)
                question_state = _applies_state(
                    question.get("applies_if"),
                    answers,
                    where=f"{set_id}.{question.get('key')}",
                )
                states = (section_state, question_state)
                if False in states:
                    state = "pruned"
                elif None in states:
                    state = "pending"
                else:
                    state = "applicable"
                rows.append((state, set_id, section_id, question))
    return rows


def partition_questions(
    question_sets: dict[str, dict[str, Any]],
    answers: Mapping[str, Any],
) -> tuple[
    list[tuple[str, str, dict[str, Any]]],
    list[tuple[str, str, dict[str, Any]]],
    list[tuple[str, str, dict[str, Any]]],
]:
    """Partition a pack's questions into (applicable, pending, pruned).

    Lists preserve pack order. This is the single source of truth for
    progress, the interview's next-gap selection, and the documents
    completeness gate.
    """
    applicable: list[tuple[str, str, dict[str, Any]]] = []
    pending: list[tuple[str, str, dict[str, Any]]] = []
    pruned: list[tuple[str, str, dict[str, Any]]] = []
    buckets = {"applicable": applicable, "pending": pending, "pruned": pruned}
    for state, set_id, section_id, question in _partition_rows(question_sets, answers):
        buckets[state].append((set_id, section_id, question))
    return applicable, pending, pruned


def compute_progress(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
) -> tuple[dict[str, QuestionSetProgress], float, list[str], list[str]]:
    """Compute per-set progress and overall required-question completeness.

    Returns ``(per_set, completeness_pct, missing_required_keys,
    pruned_required_keys)``. Denominators count APPLICABLE + PENDING
    required questions only — pruned questions (their ``applies_if`` gate
    answered decisively) leave the denominator entirely, while pending
    questions still count (fail-closed). ``missing_required_keys`` lists
    unanswered applicable/pending required keys in pack order;
    ``completeness_pct`` is the required-answered share (100.0 when no
    applicable required questions remain).
    """
    rows = _partition_rows(question_sets, answers)
    # Pre-register every set in pack order (zeroed) so per-set payloads
    # stay shape-stable — and in position — even when a whole set prunes.
    totals: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for set_id in question_sets:
        totals[set_id] = {"total": 0, "answered": 0, "req": 0, "req_ok": 0}
        order.append(set_id)
    missing: list[str] = []
    pruned_required: list[str] = []
    for state, set_id, _section_id, question in rows:
        key = str(question["key"])
        if state == "pruned":
            if question.get("required"):
                pruned_required.append(key)
            continue
        stats = totals[set_id]
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
    return per_set, overall, missing, pruned_required


def compute_section_progress(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
) -> dict[str, dict[str, QuestionSetProgress]]:
    """Per-section progress, from the same partition as ``compute_progress``.

    Returns ``{set_id: {section_id: QuestionSetProgress}}`` in pack order.
    Semantics match the per-set figures exactly — pruned questions leave
    the denominator, pending questions still count, and
    ``percent_complete`` is the answered share of the section's
    applicable+pending questions — so a section's numbers always sum to
    its set's. Every section is pre-registered (zeroed) so the payload
    stays shape-stable even when a whole section prunes. The wizard's
    section nav reads this: a set-level percentage repeated under each
    section misleads (one answer in section 1 made section 2 show the
    same %).
    """
    totals: dict[str, dict[str, dict[str, int]]] = {}
    order: list[tuple[str, str]] = []
    for set_id, question_set in question_sets.items():
        totals[set_id] = {}
        for section in question_set.get("sections", []):
            section_id = str(section.get("id", ""))
            totals[set_id][section_id] = {"total": 0, "answered": 0, "req": 0, "req_ok": 0}
            order.append((set_id, section_id))
    for state, set_id, section_id, question in _partition_rows(question_sets, answers):
        if state == "pruned":
            continue
        stats = totals[set_id][section_id]
        stats["total"] += 1
        answered = is_answered(answers.get(str(question["key"])))
        if answered:
            stats["answered"] += 1
        if question.get("required"):
            stats["req"] += 1
            if answered:
                stats["req_ok"] += 1

    per_section: dict[str, dict[str, QuestionSetProgress]] = {}
    for set_id, section_id in order:
        stats = totals[set_id][section_id]
        total = stats["total"]
        per_section.setdefault(set_id, {})[section_id] = QuestionSetProgress(
            total_questions=total,
            answered=stats["answered"],
            required_total=stats["req"],
            required_answered=stats["req_ok"],
            percent_complete=round(100.0 * stats["answered"] / total, 2) if total else 0.0,
        )
    return per_section


def build_overall_progress(
    per_set: dict[str, QuestionSetProgress], completeness_pct: float
) -> QuestionSetProgress:
    """Aggregate per-set progress into the overall row.

    ``percent_complete`` is the REQUIRED-answered share (the same number
    as ``completeness_pct`` and the interview's coverage), so every
    surface — My Plan, checkup, the wizard — shows one consistent
    completeness figure instead of mixing definitions.
    """
    return QuestionSetProgress(
        total_questions=sum(s.total_questions for s in per_set.values()),
        answered=sum(s.answered for s in per_set.values()),
        required_total=sum(s.required_total for s in per_set.values()),
        required_answered=sum(s.required_answered for s in per_set.values()),
        percent_complete=completeness_pct,
    )


def build_completeness(
    question_sets: dict[str, dict[str, Any]],
    answers: dict[str, Any],
) -> dict[str, Any]:
    """Family-facing completeness detail, from the SAME partition as
    :func:`compute_progress` (2026-10-31).

    Returns ``{"required_total", "required_answered", "missing"}`` where
    the denominator counts applicable + pending required questions
    (pruned questions — their gate answered decisively — are excluded,
    exactly as progress and the submit gate count them) and ``missing``
    lists each unanswered one in pack order with everything a UI needs
    to send the family back to it: key, label, section title and ids.
    """
    section_titles: dict[tuple[str, str], str] = {}
    for set_id, question_set in question_sets.items():
        for section in question_set.get("sections", []):
            section_titles[(set_id, str(section.get("id", "")))] = str(
                section.get("title") or ""
            )
    required_total = 0
    required_answered = 0
    missing: list[dict[str, Any]] = []
    for state, set_id, section_id, question in _partition_rows(question_sets, answers):
        if state == "pruned" or not question.get("required"):
            continue
        required_total += 1
        key = str(question["key"])
        if is_answered(answers.get(key)):
            required_answered += 1
            continue
        missing.append(
            {
                "key": key,
                "label": str(question.get("label") or key),
                "section_title": section_titles.get((set_id, section_id), ""),
                "set_id": set_id,
                "section_id": section_id,
            }
        )
    return {
        "required_total": required_total,
        "required_answered": required_answered,
        "missing": missing,
    }


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
