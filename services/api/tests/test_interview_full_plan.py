"""Tests for v1 pack: full estate plan coverage.

Every document template the product ships must have its placeholders
collectable through the interview. No stone unturned.
"""
from __future__ import annotations


def _v1_keys() -> set[str]:
    from app.domains.legal_content.pack_loader import get_pack
    from app.domains.intake.question_sets import all_question_keys

    pack = get_pack("US-VA", version="0.2.0")
    return all_question_keys(pack.question_sets)


class TestHealthcareDirectiveQuestions:
    def test_healthcare_agent_questions(self) -> None:
        keys = _v1_keys()
        assert "healthcare_agent_person" in keys
        assert "healthcare_agent_phone" in keys
        assert "successor_healthcare_agent_person" in keys

    def test_living_will_questions(self) -> None:
        keys = _v1_keys()
        assert "living_will_directives" in keys
        assert "organ_donation_wishes" in keys


class TestPowerOfAttorneyQuestions:
    def test_poa_agent_questions(self) -> None:
        keys = _v1_keys()
        assert "poa_agent_person" in keys
        assert "poa_agent_address" in keys
        assert "successor_poa_agent_person" in keys


class TestTrustQuestions:
    def test_trust_intent_and_roles(self) -> None:
        keys = _v1_keys()
        assert "wants_revocable_trust" in keys
        assert "successor_trustee_person" in keys
        assert "trust_distribution_age" in keys

    def test_trust_property_and_marital_share(self) -> None:
        keys = _v1_keys()
        assert "trust_initial_property" in keys
        assert "trust_marital_share" in keys


class TestBeneficiaryReviewQuestions:
    def test_beneficiary_review(self) -> None:
        keys = _v1_keys()
        assert "retirement_beneficiaries_current" in keys
        assert "life_insurance_beneficiaries_current" in keys


class TestFinalWishesQuestions:
    def test_funeral_and_digital(self) -> None:
        keys = _v1_keys()
        assert "funeral_wishes" in keys
        assert "digital_asset_wishes" in keys

    def test_guardianship_wishes(self) -> None:
        keys = _v1_keys()
        assert "guardian_relationship" in keys
        assert "children_upbringing_wishes" in keys
        assert "children_education_wishes" in keys


class TestFullTemplateMapping:
    def test_all_new_tokens_mapped(self) -> None:
        import inspect

        from app.domains.documents import service as doc_service

        source = inspect.getsource(doc_service.build_template_context)
        for token in [
            "healthcare_agent_name",
            "successor_healthcare_agent_name",
            "agent_name",  # POA agent
            "successor_agent_name",
            "successor_trustee_name",
            "trust_name",
            "distribution_age",
            "organ_donation_agent",
        ]:
            assert token in source, f"token {token} not mapped"


class TestAdaptiveFollowUpExtended:
    def test_healthcare_successor_after_primary(self) -> None:
        from app.domains.interview.service import _next_adaptive_question

        # Phone is asked before the successor (contact info first).
        result = _next_adaptive_question(
            answered_keys={"healthcare_agent_person"},
            pending_keys=set(),
            section="healthcare",
        )
        assert result == "healthcare_agent_phone"
        result = _next_adaptive_question(
            answered_keys={"healthcare_agent_person", "healthcare_agent_phone"},
            pending_keys=set(),
            section="healthcare",
        )
        assert result == "successor_healthcare_agent_person"

    def test_trust_details_after_yes(self) -> None:
        from app.domains.interview.service import _next_adaptive_question

        result = _next_adaptive_question(
            answered_keys={"wants_revocable_trust"},
            pending_keys=set(),
            section="trust",
        )
        # After saying yes to a trust, ask for the successor trustee.
        assert result == "successor_trustee_person"

    def test_poa_successor_after_primary(self) -> None:
        from app.domains.interview.service import _next_adaptive_question

        # Address is asked before the successor.
        result = _next_adaptive_question(
            answered_keys={"poa_agent_person"},
            pending_keys=set(),
            section="poa",
        )
        assert result == "poa_agent_address"
        result = _next_adaptive_question(
            answered_keys={"poa_agent_person", "poa_agent_address"},
            pending_keys=set(),
            section="poa",
        )
        assert result == "successor_poa_agent_person"
