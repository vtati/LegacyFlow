"""Foundation tests: state machine, rule expressions, pack loading.

These guard the two most critical shared contracts: matter lifecycle
behavior and jurisdiction-pack-driven behavior (no code branching on
jurisdiction).
"""

import pytest

from app.domains.legal_content.pack_loader import (
    evaluate_rule_expression,
    get_pack,
    list_packs,
)
from app.domains.matters.state_machine import (
    MatterStatus,
    allowed_transitions,
    gate_required_for,
    validate_transition,
)
from app.core.errors import ValidationAppError


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_happy_path_intake_to_review():
    validate_transition("INTAKE_IN_PROGRESS", "INTAKE_SUBMITTED")
    validate_transition("INTAKE_SUBMITTED", "ATTORNEY_REVIEW")
    validate_transition("ATTORNEY_REVIEW", "APPROVED_FOR_ASSEMBLY")
    validate_transition("APPROVED_FOR_ASSEMBLY", "DOCUMENTS_RELEASED")
    validate_transition("DOCUMENTS_RELEASED", "SIGNING_IN_PROGRESS")
    validate_transition("SIGNING_IN_PROGRESS", "FUNDING_IN_PROGRESS")
    validate_transition("FUNDING_IN_PROGRESS", "PLAN_IMPLEMENTED")


def test_invalid_transition_rejected():
    with pytest.raises(ValidationAppError):
        validate_transition("DRAFT", "ATTORNEY_REVIEW")
    with pytest.raises(ValidationAppError):
        validate_transition("INTAKE_IN_PROGRESS", "PLAN_IMPLEMENTED")
    with pytest.raises(ValidationAppError):
        validate_transition("ARCHIVED", "DRAFT")


def test_clarification_loop():
    validate_transition("ATTORNEY_REVIEW", "CLARIFICATION_REQUESTED")
    validate_transition("CLARIFICATION_REQUESTED", "ATTORNEY_REVIEW")


def test_escalation_loop():
    validate_transition("INTAKE_SUBMITTED", "ESCALATED")
    validate_transition("ESCALATED", "ATTORNEY_REVIEW")


def test_archived_is_terminal():
    assert allowed_transitions(MatterStatus.ARCHIVED) == []


def test_implemented_plan_can_return_to_review():
    # Annual checkup / life-event updates (FR-FUND-005).
    validate_transition("PLAN_IMPLEMENTED", "ATTORNEY_REVIEW")


def test_gates_declared_for_sensitive_transitions():
    assert gate_required_for("ATTORNEY_REVIEW", "APPROVED_FOR_ASSEMBLY") == "intake_approval"
    assert gate_required_for("APPROVED_FOR_ASSEMBLY", "DOCUMENTS_RELEASED") == "document_release"
    assert gate_required_for("INTAKE_IN_PROGRESS", "INTAKE_SUBMITTED") is None


# ---------------------------------------------------------------------------
# Rule expressions
# ---------------------------------------------------------------------------


def test_equality_and_inequality():
    facts = {"has_child_with_special_needs": True, "state": "VA"}
    assert evaluate_rule_expression("has_child_with_special_needs == true", facts)
    assert evaluate_rule_expression("has_child_with_special_needs != false", facts)
    assert not evaluate_rule_expression("has_child_with_special_needs == false", facts)
    assert evaluate_rule_expression("state == 'VA'", facts)


def test_boolean_and_or_not():
    facts = {"a": True, "b": False}
    assert evaluate_rule_expression("a == true and b == false", facts)
    assert evaluate_rule_expression("a == true or b == true", facts)
    assert evaluate_rule_expression("not b == true", facts)
    assert evaluate_rule_expression("(a == false or b == false) and not a == false", facts)


def test_missing_facts_fail_closed():
    # A rule referencing an unknown fact must NOT fire.
    assert not evaluate_rule_expression("unknown_flag == true", {})
    assert not evaluate_rule_expression("nested.deep.flag == true", {"nested": {}})


def test_dotted_lookup():
    facts = {"assets": {"real_property_outside_jurisdiction": True}}
    assert evaluate_rule_expression(
        "assets.real_property_outside_jurisdiction == true", facts
    )


def test_malformed_expression_rejected():
    with pytest.raises(ValueError):
        evaluate_rule_expression("", {})
    with pytest.raises(ValueError):
        evaluate_rule_expression("a === true", {"a": True})
    with pytest.raises(ValueError):
        evaluate_rule_expression("(a == true", {"a": True})


# ---------------------------------------------------------------------------
# Pack loading
# ---------------------------------------------------------------------------


def test_va_pack_manifest():
    pack = get_pack("US-VA")
    assert pack.manifest.pack_id == "us-va"
    assert pack.manifest.jurisdiction_code == "US-VA"
    assert pack.manifest.semantic_version == "0.1.0"
    assert len(pack.escalation_rules) == 7
    assert all(r.id.startswith("VA-ESC-") for r in pack.escalation_rules)


def test_va_will_profile_requires_two_witnesses():
    pack = get_pack("US-VA")
    profile = next(p for p in pack.execution_profiles if p["id"] == "VA-WILL-V0")
    assert profile["requirements"]["witnesses"]["required_count"] == 2
    assert profile["requirements"]["notary"]["legally_required"] is False


def test_xz_pack_differs_without_code_changes():
    """FR-JUR-005: synthetic config must visibly differ from the VA pack."""
    xz = get_pack("US-XZ")
    assert xz.manifest.pack_id == "us-xz-synthetic"
    profile = xz.execution_profiles[0]
    assert profile["requirements"]["witnesses"]["required_count"] == 3
    assert profile["requirements"]["notary"]["legally_required"] is True
    rule_ids = [r.id for r in xz.escalation_rules]
    assert rule_ids == ["XZ-ESC-001"]
    # Same loader, same API — different behavior comes only from pack data.
    va = get_pack("US-VA")
    va_ids = [r.id for r in va.escalation_rules]
    assert not set(rule_ids) & set(va_ids)


def test_xz_escalation_rule_fires_on_its_own_fact():
    xz = get_pack("US-XZ")
    rule = xz.escalation_rules[0]
    assert evaluate_rule_expression(rule.when, {"has_three_or_more_minor_children": True})
    assert not evaluate_rule_expression(rule.when, {"has_three_or_more_minor_children": False})


def test_question_sets_present():
    va = get_pack("US-VA")
    assert "household" in va.question_sets
    assert "guardians" in va.question_sets
    assert "assets" in va.question_sets
    household = va.question_sets["household"]
    assert household["question_set"]["version"] == "1.0.0"


def test_pack_listing():
    manifests = list_packs()
    by_code = {m.jurisdiction_code for m in manifests}
    assert "US-VA" in by_code
    assert "US-XZ" in by_code


def test_unknown_jurisdiction_raises():
    with pytest.raises(FileNotFoundError):
        get_pack("US-QQ")
