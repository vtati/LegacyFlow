"""Attorney Reviewer — source payload derivation (plan 2026-10-27).

The unfilled-placeholder check trusts required_question_keys to mean
"the family actually had to answer this". Pack questions flagged
required but gated off by applies_if (guardians for a childless
testator, specific bequests when there are none) are pruned by the
intake gate — they must leave the required set too, or every pruned
packet reads as missing data (production gate, Lee packet, 2026-10-27).
"""

from __future__ import annotations

from app.domains.attorney_review.service import _effective_required_keys


class _Pack:
    question_sets = {
        "household": {
            "sections": [{
                "id": "basics",
                "questions": [
                    {"key": "testator_full_name", "required": True},
                    {"key": "has_minor_children", "required": True},
                ],
            }],
        },
        "guardians": {
            "sections": [{
                "id": "guardians",
                "questions": [
                    {"key": "primary_guardian_person", "required": True,
                     "applies_if": "has_minor_children == true"},
                    {"key": "primary_guardian_address", "required": True,
                     "applies_if": "has_minor_children == true"},
                ],
            }],
        },
        "bequests": {
            "sections": [{
                "id": "bequests",
                "questions": [
                    {"key": "specific_bequests", "required": True,
                     "applies_if": "has_specific_bequests == true"},
                ],
            }],
        },
    }


def test_pruned_required_questions_leave_the_required_set() -> None:
    keys = _effective_required_keys(
        _Pack(), {"has_minor_children": False, "has_specific_bequests": False}
    )
    assert "primary_guardian_person" not in keys
    assert "primary_guardian_address" not in keys
    assert "specific_bequests" not in keys
    assert "testator_full_name" in keys


def test_applicable_required_questions_stay() -> None:
    keys = _effective_required_keys(
        _Pack(), {"has_minor_children": True, "has_specific_bequests": True}
    )
    assert "primary_guardian_person" in keys
    assert "specific_bequests" in keys
