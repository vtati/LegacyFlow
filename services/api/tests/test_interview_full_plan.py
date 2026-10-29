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


def _build_context(monkeypatch, answers: dict):
    """Exercise the real context builder with representative facts.

    Replaces the original source-text scrape (which asserted each
    token string appeared in ``build_template_context``'s source):
    the tokens are now assembled via the ``_ANSWER_SLOT_TABLE``
    mapping plus composed slots inside that builder, so the strings
    no longer literally appear in its source even though every token
    IS produced at runtime. The honest assertion is on the produced
    context itself — base render and household reads are stubbed,
    the slot-table/composition path under test runs for real.
    """
    from contextlib import contextmanager
    from uuid import uuid4

    from app.domains.documents import service as doc_service
    from app.domains.household import repository as household_repo
    from app.domains.intake import repository as intake_repo

    class _Cursor:
        def __init__(self, row):
            self._row = row

        def execute(self, *_a, **_k):
            pass

        def fetchone(self):
            return self._row

    class _Conn:
        def __init__(self, row):
            self._row = row

        @contextmanager
        def cursor(self):
            yield _Cursor(self._row)

    matter_id = uuid4()
    household_id = uuid4()
    roster = [
        "Raj Sharma", "Neha Kapoor", "Tom Becker", "Laura Chen",
        "George Osei",
    ]
    persons = [
        {"id": str(uuid4()), "kind": "adult", "full_name": name}
        for name in roster
    ]
    conn = _Conn({
        "id": str(matter_id), "household_id": str(household_id),
        "title": "Synthetic Matter",
    })
    monkeypatch.setattr(
        doc_service, "_render_context", lambda c, m, answers=None: {})
    monkeypatch.setattr(
        household_repo, "list_persons_for_household",
        lambda c, hid: persons)
    monkeypatch.setattr(
        household_repo, "list_members_for_household",
        lambda c, hid: [{
            "relationship_role": "primary_client",
            "person_id": persons[0]["id"],
        }])
    monkeypatch.setattr(
        household_repo, "list_relationships_for_household",
        lambda c, hid: [])
    monkeypatch.setattr(
        intake_repo, "list_answers",
        lambda c, mid: [
            {"question_key": k, "answer": v} for k, v in answers.items()
        ])
    return doc_service.build_template_context(conn, matter_id)


class TestFullTemplateMapping:
    def test_all_new_tokens_mapped(self, monkeypatch) -> None:
        # Every declared token must be produced, with the value its
        # source fact carries, by the current context-building path.
        ctx = _build_context(monkeypatch, {
            "testator_full_name": "Raj Sharma",
            "healthcare_agent_person": "Neha Kapoor",
            "successor_healthcare_agent_person": "Tom Becker",
            "poa_agent_person": "Laura Chen",
            "successor_poa_agent_person": "Tom Becker",
            "successor_trustee_person": "George Osei",
            "trust_distribution_age": "25",
            "organ_donation_wishes": "Donate any needed organs",
        })
        expected = {
            "healthcare_agent_name": "Neha Kapoor",
            "successor_healthcare_agent_name": "Tom Becker",
            "agent_name": "Laura Chen",  # POA agent
            "successor_agent_name": "Tom Becker",
            "successor_trustee_name": "George Osei",
            "trust_name": "The Sharma Family Revocable Trust",
            "distribution_age": "25",
            "organ_donation_agent": "Neha Kapoor",
        }
        for token, value in expected.items():
            assert ctx.get(token) == value, (
                f"token {token} not mapped: {ctx.get(token)!r} != {value!r}"
            )


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
