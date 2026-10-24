"""Regression tests for the 2026-10-24 E2E interview failures.

Pure unit tests (no DB, no network). Each test encodes a failure observed
in production on the Sharma walkthrough matter:

- "No, we do not own a business." produced a boolean proposal Yes @0.95.
- "Texas." produced Primary Executor Person = "Texas" @0.90.
- A rejected wrong proposal was re-created verbatim by a later answer.
- Auto-confirm never ran: ProposalDraftOut had no ``status`` field, so
  the router's filter raised AttributeError (swallowed) every turn.
- The prompt's Still-needed list truncated at 12 keys, hiding the
  healthcare / POA / trust sections from the model entirely.
- Nomination sync always threw: insert_person requires kwargs the
  intake sync never passed.
"""

from __future__ import annotations

import inspect


def _questions() -> dict:
    return {
        "household": {
            "sections": [
                {
                    "id": "basics",
                    "questions": [
                        {
                            "key": "expects_family_conflict",
                            "type": "boolean",
                            "label": "Do you expect disagreement among family members about your plan?",
                        },
                        {
                            "key": "owns_or_controls_business_interest",
                            "type": "boolean",
                            "label": "Do you own or control a business interest?",
                        },
                        {
                            "key": "primary_executor_person",
                            "type": "person",
                            "label": "Who would you like to name as the primary executor of your estate?",
                        },
                        {
                            "key": "primary_guardian_person",
                            "type": "person",
                            "label": "Who would you like to name as the primary guardian for your children?",
                        },
                        {
                            "key": "backup_guardian_person",
                            "type": "person",
                            "label": "Who would you like to name as the backup guardian for your children?",
                        },
                    ],
                }
            ]
        }
    }


class TestParseYesNoNegations:
    def test_do_not_phrasings_are_no(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert _parse_yes_no("No, we do not own a business.") is False
        assert _parse_yes_no("No, I do not have any specific bequests.") is False
        assert _parse_yes_no("We do not expect any family conflicts.") is False
        assert _parse_yes_no("No, we don't own one.") is False

    def test_clear_yes_still_yes(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert _parse_yes_no("Yes, that's right.") is True
        assert _parse_yes_no("Yes, I do.") is True

    def test_plain_no_still_no(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert _parse_yes_no("No.") is False

    def test_ambiguous_abstains(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert _parse_yes_no("Maybe, it depends.") is None


class TestBooleanFallbackHygiene:
    def test_negated_answer_proposes_false(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="No, we do not own or control a business interest.",
            assistant_reply="Do you own or control a business interest?",
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "owns_or_controls_business_interest"
        assert result["value"] is False

    def test_ambiguous_question_attribution_abstains(self) -> None:
        """Assistant message referencing two undecided boolean topics must
        not let the fallback guess which one was answered."""
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="No.",
            assistant_reply=(
                "Noted on the business interest. Next: do you own or control "
                "a business interest, and do you expect disagreement among "
                "family members about your plan?"
            ),
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is None

    def test_fallback_confidence_below_auto_confirm(self) -> None:
        """A deterministic guess must always be human-reviewed: its
        confidence must sit below the 0.9 auto-confirm threshold."""
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message="Yes, I expect some disagreement.",
            assistant_reply="Do you expect disagreement among family members about your plan?",
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["confidence"] < 0.9


class TestPersonFallbackHygiene:
    def test_place_name_is_not_a_person(self) -> None:
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="Texas.",
            assistant_reply=(
                "Thanks. Your executor will handle the estate in your state "
                "of residence. Which state do you live in?"
            ),
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is None

    def test_extract_rejects_state_names(self) -> None:
        from app.domains.interview.service import _extract_person_name

        assert _extract_person_name("Texas.") is None
        assert _extract_person_name("New York") is None
        assert _extract_person_name("Austin") is None

    def test_extract_bare_name_still_works(self) -> None:
        from app.domains.interview.service import _extract_person_name

        assert _extract_person_name("Priya Sharma") == "Priya Sharma"
        assert _extract_person_name("Amit Sharma") == "Amit Sharma"

    def test_extract_name_from_sentence(self) -> None:
        """Real users answer in sentences; the fallback must find the name
        instead of giving up (backup guardian/executor produced no cards)."""
        from app.domains.interview.service import _extract_person_name

        assert _extract_person_name("My wife Priya Sharma should be executor.") == "Priya Sharma"
        assert _extract_person_name("My brother Amit Sharma is the backup.") == "Amit Sharma"

    def test_sentence_answer_produces_backup_guardian_proposal(self) -> None:
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="My sister Neha Kapoor should be the backup guardian.",
            assistant_reply="Who would you like to name as the backup guardian for your children?",
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "backup_guardian_person"
        assert result["value"] == "Neha Kapoor"
        assert result["confidence"] < 0.9


class TestTextFallback:
    """Text/notes answers had NO deterministic fallback: if the model
    skipped the tool call, a street address or residuary answer vanished.
    The answer itself is the value for these types."""

    def _questions(self) -> dict:
        qs = _questions()
        qs["household"]["sections"][0]["questions"].append(
            {
                "key": "testator_street_address",
                "type": "text",
                "label": "What is your street address?",
            }
        )
        return qs

    def test_address_answer_proposes_value(self) -> None:
        from app.domains.interview.service import _deterministic_text_proposal

        result = _deterministic_text_proposal(
            user_message="123 Main Street",
            assistant_reply="What is your street address?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "testator_street_address"
        assert result["value"] == "123 Main Street"
        assert result["confidence"] < 0.9

    def test_question_back_is_not_an_answer(self) -> None:
        from app.domains.interview.service import _deterministic_text_proposal

        result = _deterministic_text_proposal(
            user_message="What do you mean by that?",
            assistant_reply="What is your street address?",
            question_sets=self._questions(),
            existing_proposal_keys=set(),
        )
        assert result is None


class TestAutoConfirmContract:
    def test_proposal_draft_out_carries_status(self) -> None:
        from uuid import uuid4

        from app.domains.interview.schemas import ProposalDraftOut

        draft = ProposalDraftOut(
            id=uuid4(),
            question_key="has_minor_children",
            proposed_value=True,
            source="user_stated",
            confidence=0.95,
            source_snippet="Yes.",
        )
        assert draft.status == "proposed"
        draft.status = "confirmed"
        assert draft.status == "confirmed"


class TestPromptWindowing:
    def test_still_needed_lists_all_missing_keys(self) -> None:
        """With many keys missing, late-pack keys (healthcare etc.) must
        still appear in the prompt — no first-12 truncation."""
        from app.domains.interview import prompts

        missing = [f"key_{i:02d}" for i in range(26)] + ["healthcare_agent_person"]
        prompt = prompts.build_system_prompt(
            pack_id="us-va",
            jurisdiction_code="US-VA",
            missing_required_keys=missing,
            answered_required=0,
            total_required=len(missing),
            pending_proposal_keys=[],
            answered_keys=[],
        )
        assert "healthcare_agent_person" in prompt
        assert "key_25" in prompt


class TestNominationSyncContract:
    def test_insert_person_optional_kwargs_have_defaults(self) -> None:
        from app.domains.household.repository import insert_person

        params = inspect.signature(insert_person).parameters
        for name in ("date_of_birth", "email", "notes"):
            assert params[name].default is not inspect.Parameter.empty, name


class TestAnswerCoercion:
    def test_boolean_strings_coerce(self) -> None:
        from app.domains.intake.service import _coerce_answer_value

        question = {"key": "expects_family_conflict", "type": "boolean"}
        assert _coerce_answer_value(question, "No") is False
        assert _coerce_answer_value(question, "yes") is True
        assert _coerce_answer_value(question, True) is True
        assert _coerce_answer_value(question, False) is False

    def test_text_values_pass_through(self) -> None:
        from app.domains.intake.service import _coerce_answer_value

        question = {"key": "testator_city", "type": "text"}
        assert _coerce_answer_value(question, "No") == "No"


# ---------------------------------------------------------------------------
# Round 2 — diff-review findings (2026-10-24)
# ---------------------------------------------------------------------------


class TestParseYesNoCompound:
    def test_leading_yes_wins_over_trailing_other_topic_negation(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert (
            _parse_yes_no("Yes, I do own one, and I don't expect any conflicts.")
            is True
        )

    def test_leading_no_wins_over_trailing_other_topic_yes(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert (
            _parse_yes_no("No, I don't own one, but yes, we have savings accounts.")
            is False
        )

    def test_negation_inside_first_clause_still_no(self) -> None:
        from app.domains.interview.service import _parse_yes_no

        assert _parse_yes_no("I do not have one.") is False


class TestPersonFallbackRoleScoping:
    def _questions(self) -> dict:
        qs = _questions()
        qs["household"]["sections"][0]["questions"] += [
            {
                "key": "backup_executor_person",
                "type": "person",
                "label": "Who would you like to name as the backup executor of your estate?",
            }
        ]
        return qs

    def test_cross_role_acknowledgement_does_not_steal_the_question(self) -> None:
        """'backup' in an executor acknowledgement must not select the
        backup GUARDIAN when the question asked is primary guardian."""
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="Priya Sharma",
            assistant_reply=(
                "Got it — your backup executor is Amit. Now, who would you "
                "like to name as the primary guardian for your children?"
            ),
            question_sets=self._questions(),
            existing_proposal_keys={"primary_executor_person", "backup_executor_person"},
        )
        assert result is not None
        assert result["question_key"] == "primary_guardian_person"
        assert result["value"] == "Priya Sharma"

    def test_unqualified_question_falls_to_remaining_undecided_key(self) -> None:
        from app.domains.interview.service import _deterministic_person_proposal

        result = _deterministic_person_proposal(
            user_message="Neha Kapoor",
            assistant_reply="And if Amit can't serve, who should step in as guardian?",
            question_sets=self._questions(),
            existing_proposal_keys={"primary_guardian_person"},
        )
        assert result is not None
        assert result["question_key"] == "backup_guardian_person"


class TestTextFallbackSelectTypes:
    def test_multi_select_gets_no_text_fallback(self) -> None:
        """Raw prose is not a valid multi_select value (option codes are);
        storing it corrupts the facts model while looking 'answered'."""
        from app.domains.interview.service import _deterministic_text_proposal

        qs = _questions()
        qs["household"]["sections"][0]["questions"].append(
            {
                "key": "asset_categories_present",
                "type": "multi_select",
                "label": "Which of these asset categories do you have?",
            }
        )
        result = _deterministic_text_proposal(
            user_message="We have a house, savings accounts, and a business.",
            assistant_reply="Which of these asset categories do you have?",
            question_sets=qs,
            existing_proposal_keys=set(),
        )
        assert result is None


class TestCoerceAnswersMap:
    def _by_key(self) -> dict:
        return {
            "expects_family_conflict": {
                "key": "expects_family_conflict",
                "type": "boolean",
            },
            "testator_city": {"key": "testator_city", "type": "text"},
        }

    def test_batch_save_drops_uncoercible_key_and_keeps_the_rest(self) -> None:
        from app.domains.intake.service import _coerce_answers_map

        out = _coerce_answers_map(
            self._by_key(),
            {"expects_family_conflict": "N/A", "testator_city": "Austin"},
            strict=False,
        )
        assert out == {"testator_city": "Austin"}

    def test_single_key_save_stays_strict(self) -> None:
        import pytest

        from app.core.errors import ValidationAppError
        from app.domains.intake.service import _coerce_answers_map

        with pytest.raises(ValidationAppError):
            _coerce_answers_map(
                self._by_key(), {"expects_family_conflict": "N/A"}, strict=True
            )

    def test_none_boolean_passes_through(self) -> None:
        from app.domains.intake.service import _coerce_answers_map

        out = _coerce_answers_map(
            self._by_key(), {"expects_family_conflict": None}, strict=True
        )
        assert out == {"expects_family_conflict": None}


class TestExtractorBlocklistRound2:
    def test_city_and_greeting_are_not_names(self) -> None:
        from app.domains.interview.service import _extract_person_name

        assert _extract_person_name("Denver.") is None
        assert _extract_person_name("Hello.") is None
        assert _extract_person_name("Chicago") is None

    def test_descriptive_document_statement_is_not_a_nomination(self) -> None:
        from app.domains.interview.service import _extract_person_name

        assert _extract_person_name("My will names John Smith as beneficiary.") is None

    def test_three_word_name_in_sentence(self) -> None:
        from app.domains.interview.service import _extract_person_name

        assert (
            _extract_person_name("My wife Mary Jane Watson should be executor.")
            == "Mary Jane Watson"
        )
