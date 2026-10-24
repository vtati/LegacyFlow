"""Applicability engine tests (Interview v2, Part 1).

``evaluate_applies_if`` is a three-valued (Kleene) evaluator over intake
answers: True / False / None (None = a referenced gate is unanswered, so
the question is PENDING — fail-closed, never pruned). ``partition_questions``
splits a pack's questions into applicable / pending / pruned, and
``compute_progress`` counts only applicable+pending required questions.
"""
from __future__ import annotations

import pytest

from app.domains.intake.question_sets import (
    compute_progress,
    evaluate_applies_if,
    partition_questions,
)

# ---------------------------------------------------------------------------
# Evaluator: comparisons
# ---------------------------------------------------------------------------


def test_bool_comparison_true_and_false():
    assert evaluate_applies_if("has_minor_children == true", {"has_minor_children": True}) is True
    assert evaluate_applies_if("has_minor_children == true", {"has_minor_children": False}) is False
    assert evaluate_applies_if("has_minor_children == false", {"has_minor_children": False}) is True
    assert evaluate_applies_if("has_minor_children != false", {"has_minor_children": True}) is True
    assert evaluate_applies_if("has_minor_children != false", {"has_minor_children": False}) is False


def test_string_and_number_comparisons():
    assert evaluate_applies_if("marital_status == 'married'", {"marital_status": "married"}) is True
    assert evaluate_applies_if("marital_status == 'married'", {"marital_status": "single"}) is False
    assert evaluate_applies_if("child_count == 2", {"child_count": 2}) is True
    assert evaluate_applies_if("child_count != 2", {"child_count": 3}) is True


def test_unanswered_key_is_pending_none():
    assert evaluate_applies_if("has_minor_children == true", {}) is None
    assert evaluate_applies_if("wants_poa_document != false", {}) is None
    # Answered-with-null also counts as unanswered.
    assert evaluate_applies_if("has_minor_children == true", {"has_minor_children": None}) is None
    assert evaluate_applies_if("has_minor_children == true", {"has_minor_children": ""}) is None


# ---------------------------------------------------------------------------
# Evaluator: boolean structure + three-valued logic
# ---------------------------------------------------------------------------


def test_and_or_not_precedence():
    answers = {"a": True, "b": False, "c": True}
    assert evaluate_applies_if("a == true and b == false", answers) is True
    assert evaluate_applies_if("a == true and b == true", answers) is False
    assert evaluate_applies_if("b == true or c == true", answers) is True
    assert evaluate_applies_if("not b == true", answers) is True
    # `and` binds tighter than `or`: (False and True) or True == True
    assert evaluate_applies_if("b == true and a == true or c == true", answers) is True
    # `not` binds tighter than `and`: (not False) and True == True
    assert evaluate_applies_if("not b == true and a == true", answers) is True


def test_parentheses():
    answers = {"a": False, "b": True, "c": False}
    assert evaluate_applies_if("(a == true or b == true) and c == false", answers) is True
    assert evaluate_applies_if("a == true or (b == true and c == true)", answers) is False
    assert evaluate_applies_if("not (a == true or b == false)", answers) is True


def test_three_valued_or_and():
    # None or True == True (decisive despite the unknown side)
    assert evaluate_applies_if("missing_key == true or a == true", {"a": True}) is True
    # None or False == None
    assert evaluate_applies_if("missing_key == true or a == true", {"a": False}) is None
    # None and False == False
    assert evaluate_applies_if("missing_key == true and a == true", {"a": False}) is False
    # None and True == None
    assert evaluate_applies_if("missing_key == true and a == true", {"a": True}) is None
    # not None == None
    assert evaluate_applies_if("not missing_key == true", {}) is None
    # None or None == None
    assert evaluate_applies_if("x == true or y == true", {}) is None


def test_bare_key_expression():
    assert evaluate_applies_if("has_minor_children", {"has_minor_children": True}) is True
    assert evaluate_applies_if("has_minor_children", {"has_minor_children": False}) is False
    assert evaluate_applies_if("has_minor_children", {}) is None


def test_malformed_expressions_raise():
    for bad in [
        "has_minor_children ==",
        "foo bar",
        "(has_minor_children == true",
        "has_minor_children == true)",
        "",
        "   ",
        "has_minor_children === true",
        "and has_minor_children == true",
        "has_minor_children == ",
        "== true",
    ]:
        with pytest.raises(ValueError):
            evaluate_applies_if(bad, {"has_minor_children": True})


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------


def _mini_pack():
    return {
        "household": {
            "sections": [
                {
                    "id": "basics",
                    "questions": [
                        {"key": "has_minor_children", "type": "boolean", "required": True},
                        {"key": "nickname", "type": "text", "required": False},
                    ],
                }
            ]
        },
        "guardians": {
            "sections": [
                {
                    "id": "primary",
                    "questions": [
                        {
                            "key": "primary_guardian_person",
                            "type": "person",
                            "required": True,
                            "applies_if": "has_minor_children == true",
                        },
                    ],
                }
            ]
        },
        "poa": {
            "sections": [
                {
                    "id": "agent",
                    "applies_if": "wants_poa_document != false",
                    "questions": [
                        {"key": "poa_agent_person", "type": "person", "required": True},
                    ],
                }
            ]
        },
    }


def _keys(rows):
    return [str(q["key"]) for _, _, q in rows]


def test_partition_gate_unanswered_is_pending():
    applicable, pending, pruned = partition_questions(_mini_pack(), {})
    assert "primary_guardian_person" in _keys(pending)
    assert "poa_agent_person" in _keys(pending)
    assert "has_minor_children" in _keys(applicable)
    assert pruned == []


def test_partition_gate_false_prunes():
    applicable, pending, pruned = partition_questions(
        _mini_pack(), {"has_minor_children": False, "wants_poa_document": False}
    )
    assert "primary_guardian_person" in _keys(pruned)
    assert "poa_agent_person" in _keys(pruned)
    assert pending == []


def test_partition_gate_true_applies():
    applicable, pending, pruned = partition_questions(
        _mini_pack(), {"has_minor_children": True}
    )
    assert "primary_guardian_person" in _keys(applicable)
    # Section gate still unanswered -> pending, not pruned.
    assert "poa_agent_person" in _keys(pending)


def test_partition_mixed_section_pending_question_pruned():
    # Section pending (gate unanswered) + the question itself decisively
    # pruned by its own rule -> the question is pruned (a False anywhere
    # prunes; pending only when nothing is decisively False).
    pack = _mini_pack()
    pack["poa"]["sections"][0]["questions"][0]["applies_if"] = "has_minor_children == true"
    _app, pending, pruned = partition_questions(pack, {"has_minor_children": False})
    assert "poa_agent_person" in _keys(pruned)
    assert "poa_agent_person" not in _keys(pending)


# ---------------------------------------------------------------------------
# Progress contract
# ---------------------------------------------------------------------------


def test_progress_pending_counts_pruned_excluded():
    pack = _mini_pack()
    # Gate unanswered: guardian + poa count in the denominator (pending).
    per_set, pct, missing, pruned = compute_progress(pack, {})
    assert per_set["guardians"].required_total == 1
    assert sorted(missing) == ["has_minor_children", "poa_agent_person", "primary_guardian_person"]
    assert pruned == []
    assert pct == 0.0
    # Gate answered False: pruned questions leave the denominator entirely.
    per_set, pct, missing, pruned = compute_progress(
        pack, {"has_minor_children": False, "wants_poa_document": False}
    )
    assert per_set["guardians"].required_total == 0
    assert per_set["poa"].required_total == 0
    assert sorted(pruned) == ["poa_agent_person", "primary_guardian_person"]
    assert missing == []
    assert pct == 100.0


def test_progress_answered_then_pruned_is_excluded_not_error():
    pack = _mini_pack()
    answers = {
        "has_minor_children": True,
        "primary_guardian_person": "Amit Sharma",
        "wants_poa_document": True,
        "poa_agent_person": "Priya Sharma",
    }
    per_set, pct, missing, pruned = compute_progress(pack, answers)
    assert pct == 100.0 and missing == [] and pruned == []
    # Flip the gate: the answered guardian answer is now pruned — progress
    # must stay computable and exclude it from missing AND the denominator.
    answers["has_minor_children"] = False
    per_set, pct, missing, pruned = compute_progress(pack, answers)
    assert "primary_guardian_person" in pruned
    assert "primary_guardian_person" not in missing
    assert per_set["guardians"].required_total == 0
    assert pct == 100.0


def test_progress_no_rules_pack_behaves_like_before():
    pack = {
        "s": {
            "sections": [
                {
                    "id": "x",
                    "questions": [
                        {"key": "a", "required": True},
                        {"key": "b", "required": True},
                        {"key": "c", "required": False},
                    ],
                }
            ]
        }
    }
    per_set, pct, missing, pruned = compute_progress(pack, {"a": "yes"})
    assert pct == 50.0
    assert missing == ["b"]
    assert pruned == []
    assert per_set["s"].total_questions == 3


# ---------------------------------------------------------------------------
# Review-round fixes (Part 1 diff review)
# ---------------------------------------------------------------------------


def test_identifier_starting_with_true_or_false():
    assert evaluate_applies_if("true_value == true", {"true_value": True}) is True
    assert evaluate_applies_if("false_positive == false", {"false_positive": False}) is True
    assert evaluate_applies_if("truex == true", {"truex": True}) is True


def test_bool_number_comparisons_are_type_strict():
    assert evaluate_applies_if("count == true", {"count": 1}) is False
    assert evaluate_applies_if("count != true", {"count": 1}) is True
    assert evaluate_applies_if("flag == 1", {"flag": True}) is False
    assert evaluate_applies_if("count == 1", {"count": 1}) is True


def test_string_escapes_are_decoded():
    assert evaluate_applies_if(r"name == 'it\'s'", {"name": "it's"}) is True
    assert evaluate_applies_if(r"path == 'a\\b'", {"path": "a\\b"}) is True


def test_capitalized_boolean_literal_is_an_error():
    with pytest.raises(ValueError):
        evaluate_applies_if("flag == True", {"flag": True})


def test_non_string_applies_if_fails_loud():
    pack = {
        "s": {
            "sections": [
                {
                    "id": "x",
                    "questions": [
                        {"key": "a", "required": True, "applies_if": True},
                    ],
                }
            ]
        }
    }
    with pytest.raises(ValueError):
        partition_questions(pack, {})


def test_duplicate_keys_across_sets_count_independently():
    pack = {
        "s1": {
            "sections": [
                {"id": "x", "questions": [{"key": "dup", "required": True}]}
            ]
        },
        "s2": {
            "sections": [
                {
                    "id": "y",
                    "questions": [
                        {"key": "dup", "required": True},
                        {"key": "uniq", "required": True},
                    ],
                }
            ]
        },
    }
    per_set, pct, missing, pruned = compute_progress(pack, {})
    assert per_set["s1"].total_questions == 1
    assert per_set["s2"].total_questions == 2
    assert per_set["s1"].required_total == 1
    assert per_set["s2"].required_total == 2
    assert missing == ["dup", "dup", "uniq"]
