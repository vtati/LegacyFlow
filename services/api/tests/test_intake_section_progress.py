"""Per-section intake progress (Phase 3 QA defect 3).

The progress payload used to carry per-SET progress only, and the intake
wizard rendered that set percentage under every section of the set — one
answered question in "About your household" made "Quick check" show the
same 8.33%. ``compute_section_progress`` breaks the same partition down
per section so each section reports its own answered share, with the
exact semantics of ``compute_progress`` (pruned questions leave the
denominator, pending questions still count, percent is answered/total).
"""
from __future__ import annotations

from app.domains.intake.question_sets import (
    compute_progress,
    compute_section_progress,
)
from app.domains.intake.schemas import IntakeProgressOut


def _q(key: str, *, required: bool = True, applies_if: str | None = None) -> dict:
    question = {"key": key, "required": required}
    if applies_if is not None:
        question["applies_if"] = applies_if
    return question


def household_like_pack() -> dict:
    """One set, two sections — the us-va household shape (7 + 5 questions)."""
    return {
        "household": {
            "sections": [
                {
                    "id": "basics",
                    "questions": [
                        _q("testator_full_name"),
                        _q("marital_status"),
                        _q("primary_residence_state"),
                        _q("has_minor_children"),
                        _q("minor_children_details", required=False),
                        _q("wants_poa_document", required=False),
                        _q("wants_healthcare_directive", required=False),
                    ],
                },
                {
                    "id": "triage",
                    "questions": [
                        _q("has_child_with_special_needs"),
                        _q("has_prior_marriage"),
                        _q("owns_outside_real_estate"),
                        _q("has_business_interests"),
                        _q("expects_conflict"),
                    ],
                },
            ]
        }
    }


def test_one_answer_counts_only_in_its_own_section():
    sections = compute_section_progress(
        household_like_pack(), {"testator_full_name": "Raj Sharma"}
    )
    basics = sections["household"]["basics"]
    triage = sections["household"]["triage"]
    assert basics.total_questions == 7
    assert basics.answered == 1
    assert basics.percent_complete == 14.29
    assert triage.total_questions == 5
    assert triage.answered == 0
    assert triage.percent_complete == 0.0


def test_section_counts_sum_to_the_set_counts():
    answers = {
        "testator_full_name": "Raj Sharma",
        "marital_status": "married",
        "has_child_with_special_needs": False,
    }
    pack = household_like_pack()
    sections = compute_section_progress(pack, answers)
    per_set, _overall, _missing, _pruned = compute_progress(pack, answers)
    household = per_set["household"]
    assert sum(s.total_questions for s in sections["household"].values()) == (
        household.total_questions
    )
    assert sum(s.answered for s in sections["household"].values()) == household.answered
    assert sum(s.required_total for s in sections["household"].values()) == (
        household.required_total
    )
    assert sum(s.required_answered for s in sections["household"].values()) == (
        household.required_answered
    )


def test_pruned_questions_leave_the_section_denominator():
    pack = {
        "household": {
            "sections": [
                {
                    "id": "children",
                    "questions": [
                        _q("has_minor_children"),
                        _q(
                            "minor_children_details",
                            applies_if="has_minor_children == true",
                        ),
                    ],
                },
                {
                    "id": "gated",
                    "questions": [
                        _q("wants_poa_document"),
                        _q("poa_agent_name", applies_if="wants_poa_document == true"),
                    ],
                },
            ]
        }
    }
    answers = {"has_minor_children": False, "wants_poa_document": False}
    sections = compute_section_progress(pack, answers)
    children = sections["household"]["children"]
    assert children.total_questions == 1
    assert children.answered == 1
    assert children.percent_complete == 100.0
    # A section whose gated questions all pruned keeps only its gate.
    gated = sections["household"]["gated"]
    assert gated.total_questions == 1
    assert gated.answered == 1


def test_fully_pruned_section_is_present_and_zeroed():
    pack = {
        "household": {
            "sections": [
                {
                    "id": "basics",
                    "questions": [_q("has_minor_children")],
                },
                {
                    "id": "trust",
                    "applies_if": "wants_trust == true",
                    "questions": [_q("trustee_name"), _q("successor_trustee_name")],
                },
            ]
        }
    }
    sections = compute_section_progress(pack, {"wants_trust": False})
    trust = sections["household"]["trust"]
    assert trust.total_questions == 0
    assert trust.answered == 0
    assert trust.percent_complete == 0.0


def test_pending_gate_questions_still_count():
    pack = household_like_pack()
    pack["household"]["sections"][0]["questions"].append(
        _q("guardian_name", applies_if="has_minor_children == true")
    )
    sections = compute_section_progress(pack, {})
    basics = sections["household"]["basics"]
    # Gate unanswered -> pending -> fail-closed: still in the denominator.
    assert basics.total_questions == 8
    assert basics.answered == 0


def test_sections_present_before_any_answers():
    sections = compute_section_progress(household_like_pack(), {})
    assert set(sections["household"]) == {"basics", "triage"}
    assert sections["household"]["basics"].percent_complete == 0.0
    assert sections["household"]["triage"].percent_complete == 0.0


def test_progress_schema_carries_sections_with_empty_default():
    out = IntakeProgressOut(sets={}, completeness_pct=0.0, overall=_zero_overall())
    assert out.sections == {}
    sections = compute_section_progress(
        household_like_pack(), {"testator_full_name": "Raj Sharma"}
    )
    out = IntakeProgressOut(
        sets={}, completeness_pct=8.33, overall=_zero_overall(), sections=sections
    )
    assert out.sections["household"]["basics"].percent_complete == 14.29


def _zero_overall():
    from app.domains.intake.schemas import QuestionSetProgress

    return QuestionSetProgress(
        total_questions=0,
        answered=0,
        required_total=0,
        required_answered=0,
        percent_complete=0.0,
    )
