"""Cluster selector + prompt v6 tests (Interview v2, Part 2).

The next-gap selector is deterministic: applicable-missing REQUIRED
questions (plus unanswered gate questions that pending content waits on),
grouped by the pack's ``cluster:`` field, in fixed cluster order. Prompt
v6 scopes the state section to that cluster; without a gap the prompt is
the v5 text unchanged.
"""
from __future__ import annotations

from app.domains.interview import prompts
from app.domains.interview.service import next_gap_cluster
from app.domains.legal_content.pack_loader import get_pack


def _v2_sets():
    return get_pack("US-VA", "2.0.0").question_sets


def _keys(gap):
    return [q["key"] for q in gap["questions"]]


IDENTITY_ANSWERS = {
    "marital_status": "married",
    "primary_residence_state": "Virginia",
    "testator_street_address": "123 Main Street",
    "testator_city": "Richmond",
    "has_child_with_special_needs": False,
    "has_prior_marriage_or_children_outside_current_relationship": False,
    "owns_real_estate_outside_primary_jurisdiction": False,
    "owns_or_controls_business_interest": False,
    "expects_family_conflict": False,
    "wants_poa_document": True,
    "wants_healthcare_directive": True,
}


def test_empty_profile_starts_at_identity_with_gates():
    gap = next_gap_cluster(_v2_sets(), {})
    assert gap is not None and gap["cluster"] == "identity"
    keys = _keys(gap)
    assert "marital_status" in keys
    assert "testator_street_address" in keys
    # The document-choice gates ride along: pending POA/healthcare content
    # cannot become applicable until they are answered.
    assert "wants_poa_document" in keys
    assert "wants_healthcare_directive" in keys
    # No other cluster's questions leak in.
    assert "has_minor_children" not in keys
    assert "primary_guardian_person" not in keys


def test_identity_done_moves_to_children():
    gap = next_gap_cluster(_v2_sets(), dict(IDENTITY_ANSWERS))
    assert gap is not None and gap["cluster"] == "children"
    assert _keys(gap) == ["has_minor_children"]


def test_pruned_profile_never_surfaces_guardian_keys():
    answers = dict(IDENTITY_ANSWERS)
    answers["has_minor_children"] = False
    seen: list[str] = []
    for _ in range(12):
        gap = next_gap_cluster(_v2_sets(), answers)
        if gap is None:
            break
        for q in gap["questions"]:
            seen.append(q["key"])
            answers[q["key"]] = True if q["type"] == "boolean" else "x"
    assert "primary_guardian_person" not in seen
    assert "backup_guardian_person" not in seen
    assert "primary_guardian_address" not in seen
    assert next_gap_cluster(_v2_sets(), answers) is None


def test_all_applicable_answered_returns_none():
    answers = dict(IDENTITY_ANSWERS)
    for _ in range(20):
        gap = next_gap_cluster(_v2_sets(), answers)
        if gap is None:
            return
        for q in gap["questions"]:
            answers[q["key"]] = True if q["type"] == "boolean" else "x"
    raise AssertionError("selector never exhausted the pack")


def test_gap_questions_carry_labels_and_types():
    gap = next_gap_cluster(_v2_sets(), {})
    for q in gap["questions"]:
        assert q["label"] and q["type"] and q["key"]


# ---------------------------------------------------------------------------
# Prompt v6
# ---------------------------------------------------------------------------


def test_prompt_with_gap_scopes_still_needed_to_cluster():
    gap = {
        "cluster": "identity",
        "questions": [
            {"key": "marital_status", "label": "What best describes your household?", "type": "single_select"},
        ],
    }
    text = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["marital_status", "healthcare_agent_person"],
        answered_required=3,
        total_required=41,
        pending_proposal_keys=[],
        answered_keys=["asset_categories_present"],
        cluster_gap=gap,
    )
    assert "CLUSTER FOCUS: identity" in text
    assert "What best describes your household?" in text
    # The other cluster's missing key is not presented as still needed.
    assert "healthcare_agent_person" not in text
    # v5 guardrails survive.
    assert "NEVER REPEAT QUESTIONS" in text
    assert "never state a count of recorded values" in text


def test_prompt_without_gap_is_the_v5_text():
    text = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["marital_status", "healthcare_agent_person"],
        answered_required=3,
        total_required=41,
        pending_proposal_keys=[],
        answered_keys=[],
    )
    assert "CLUSTER FOCUS" not in text
    assert "marital_status, healthcare_agent_person" in text
    assert prompts.PROMPT_TEMPLATE_VERSION == "v6"
    assert prompts.PROMPT_TEMPLATE_VERSION_V5 == "v5"


def test_prompt_with_gap_replaces_section_order_instruction():
    # Review fix: the v5 "complete one section" bullet contradicts cluster
    # focus; in a gap prompt it is replaced by cluster-order language.
    gap = {
        "cluster": "money",
        "questions": [
            {"key": "has_debts", "label": "Do you have significant debts?", "type": "boolean"},
        ],
    }
    text = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["has_debts"],
        answered_required=9,
        total_required=41,
        pending_proposal_keys=[],
        answered_keys=[],
        cluster_gap=gap,
    )
    assert "Complete one section before moving to" not in text
    assert "cluster" in text.lower()
    # And the no-gap prompt keeps the original sentence (v5 byte-for-byte).
    plain = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["has_debts"],
        answered_required=9,
        total_required=41,
        pending_proposal_keys=[],
        answered_keys=[],
    )
    assert "Complete one section before moving to" in plain


# ---------------------------------------------------------------------------
# Flag wiring: fail-safe + pack-shape tolerance
# ---------------------------------------------------------------------------

from uuid import uuid4  # noqa: E402

from app.domains.interview import service as interview_service  # noqa: E402


def test_cluster_gap_for_turn_fails_safe_on_flag_error(monkeypatch):
    def _boom(conn, key, *, firm_id=None, pack_id=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(interview_service.feature_flags, "is_enabled", _boom)
    pack = get_pack("US-VA", "2.0.0")
    assert (
        interview_service._cluster_gap_for_turn(
            object(), firm_id=uuid4(), pack=pack, answers={}
        )
        is None
    )


def test_cluster_gap_for_turn_flag_paths(monkeypatch):
    pack_v2 = get_pack("US-VA", "2.0.0")
    pack_v1 = get_pack("US-VA", "0.2.0")
    monkeypatch.setattr(
        interview_service.feature_flags,
        "is_enabled",
        lambda conn, key, *, firm_id=None, pack_id=None: False,
    )
    assert (
        interview_service._cluster_gap_for_turn(
            object(), firm_id=uuid4(), pack=pack_v2, answers={}
        )
        is None
    )
    monkeypatch.setattr(
        interview_service.feature_flags,
        "is_enabled",
        lambda conn, key, *, firm_id=None, pack_id=None: True,
    )
    gap = interview_service._cluster_gap_for_turn(
        object(), firm_id=uuid4(), pack=pack_v2, answers={}
    )
    assert gap is not None and gap["cluster"] == "identity"
    # v1 pack has no cluster fields: graceful None, never a crash.
    assert (
        interview_service._cluster_gap_for_turn(
            object(), firm_id=uuid4(), pack=pack_v1, answers={}
        )
        is None
    )
