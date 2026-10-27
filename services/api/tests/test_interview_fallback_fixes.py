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


# ---------------------------------------------------------------------------
# Round 3 — proof re-run findings (2026-10-24, matter 01439f8a)
# ---------------------------------------------------------------------------


class TestPackNominationBindings:
    def test_all_nomination_bindings_use_db_allowed_types(self) -> None:
        """The nominations table CHECK allows only guardian / trustee /
        executor / financial_agent / healthcare_agent. The us-va pack
        bound poa_agent.* and successor_trustee.* — every save touching
        those keys died with a 500 (the insert error poisoned the
        transaction even though the sync swallowed the exception)."""
        from app.domains.legal_content.pack_loader import get_pack

        pack = get_pack("US-VA")
        allowed = {"guardian", "trustee", "executor", "financial_agent", "healthcare_agent"}
        bad = []
        from app.domains.intake import question_sets as qs

        for _, _, q in qs.iter_questions(pack.question_sets):
            maps_to = q.get("maps_to") or ""
            if isinstance(maps_to, str) and maps_to.startswith("nominations."):
                parts = maps_to.split(".")
                if len(parts) == 3 and parts[1] not in allowed:
                    bad.append((q.get("key"), maps_to))
        assert bad == []


class TestNominationSyncSavepoint:
    def test_failed_insert_cannot_poison_the_save(self, monkeypatch) -> None:
        """A nomination insert that raises must roll back to a savepoint
        so the surrounding answer-save transaction stays usable, and the
        sync must continue with the next answer."""
        from app.domains.intake import service as intake_service

        statements: list[str] = []

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                statements.append(str(sql))

        class FakeConn:
            def cursor(self):
                return FakeCursor()

        monkeypatch.setattr(
            intake_service, "_ensure_person_for_name",
            lambda conn, household_id, name: {"id": "11111111-1111-1111-1111-111111111111"},
        )
        # R1: the sync first tries ID-shape resolution against the
        # household persons list; an empty list = name shape, so the
        # (mocked) ensure-by-name path above is what runs.
        import app.domains.household.repository as household_repo

        monkeypatch.setattr(
            household_repo, "list_persons_for_household", lambda conn, hid: []
        )
        import app.domains.nominations.repository as nom_repo

        monkeypatch.setattr(nom_repo, "list_nominations", lambda conn, mid: [])
        inserted: list[str] = []

        def fake_insert(conn, **kw):
            inserted.append(kw["nomination_type"])
            if kw["nomination_type"] == "trustee":
                raise RuntimeError("check constraint violated")

        monkeypatch.setattr(nom_repo, "insert_nomination", fake_insert)

        class FakePack:
            question_sets = {
                "s": {"sections": [{"questions": [
                    {"key": "successor_trustee_person", "type": "person",
                     "maps_to": "nominations.trustee.1"},
                    {"key": "primary_guardian_person", "type": "person",
                     "maps_to": "nominations.guardian.1"},
                ]}]}
            }

        from uuid import uuid4

        intake_service._sync_nominations_from_answers(
            FakeConn(),
            matter_id=uuid4(), household_id=uuid4(), firm_id=uuid4(),
            actor_id=uuid4(),
            answers={
                "successor_trustee_person": "Amit Sharma",
                "primary_guardian_person": "Amit Sharma",
            },
            pack=FakePack(),
        )
        # Both inserts were attempted (failure did not abort the loop)…
        assert inserted == ["trustee", "guardian"]
        # …and the failed one was rolled back to a savepoint.
        assert any("ROLLBACK TO SAVEPOINT" in s for s in statements)


class TestOverallProgress:
    def test_overall_sums_sets_and_uses_required_pct(self) -> None:
        from app.domains.intake.question_sets import (
            QuestionSetProgress,
            build_overall_progress,
        )

        per_set = {
            "a": QuestionSetProgress(total_questions=10, answered=5, required_total=8, required_answered=4, percent_complete=50.0),
            "b": QuestionSetProgress(total_questions=4, answered=4, required_total=4, required_answered=4, percent_complete=100.0),
        }
        overall = build_overall_progress(per_set, 66.67)
        assert overall.total_questions == 14
        assert overall.answered == 9
        assert overall.required_total == 12
        assert overall.required_answered == 8
        assert overall.percent_complete == 66.67


class TestSelfDescribingAnswers:
    def test_user_message_naming_the_role_drives_person_fallback(self) -> None:
        """Misaligned turn (the assistant's last message was about
        bequests): the user's own words name the backup-executor role,
        and the nominee is the span before the 'if … cannot' clause."""
        from app.domains.interview.service import _deterministic_person_proposal

        qs = _questions()
        qs["household"]["sections"][0]["questions"].append(
            {"key": "backup_executor_person", "type": "person",
             "label": "Who should serve as executor if your first choice cannot?"}
        )
        result = _deterministic_person_proposal(
            user_message=(
                "Amit Sharma, my brother, should serve as backup executor "
                "if Priya Sharma cannot serve."
            ),
            assistant_reply=(
                "Thanks — I've recorded that. Next: Do you have any specific "
                "bequests you'd like to make?"
            ),
            question_sets=qs,
            existing_proposal_keys={"primary_executor_person"},
        )
        assert result is not None
        assert result["question_key"] == "backup_executor_person"
        assert result["value"] == "Amit Sharma"

    def test_user_message_naming_the_topic_drives_boolean_fallback(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal

        qs = _questions()
        qs["household"]["sections"][0]["questions"].append(
            {"key": "has_specific_bequests", "type": "boolean",
             "label": "Do you have any specific bequests?"}
        )
        result = _deterministic_boolean_proposal(
            user_message="No, we do not have any specific bequests.",
            assistant_reply="Who are the residuary beneficiaries?",
            question_sets=qs,
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "has_specific_bequests"
        assert result["value"] is False


class TestBundledMessageFallbacks:
    """Interview v2 bundled answers: ONE user message answers MANY topics
    at once. Whole-message polarity/value attribution — safe in the v1
    one-question world — fabricates values for whichever topic the model
    did not record (Part 3 live verification, 2026-10-24: an identity
    paragraph produced has_minor_children=False and a poa_agent_address
    proposal whose value was the entire paragraph)."""

    _IDENTITY_MESSAGE = (
        "My full legal name is Arjun Sharma. I'm married, and my primary "
        "residence is in Texas — I live at 123 Main Street, Austin. "
        "A few quick answers: no child with special needs, no prior "
        "marriages or children outside my current marriage, no real "
        "estate outside Texas, no business interests, and I don't "
        "expect any family conflicts. Yes, I want a durable financial "
        "power of attorney, and yes, I want an advance healthcare directive."
    )
    _PREV_REPLY = (
        "Great! Let's get started with your estate planning interview. "
        "You're signed in as test.recording — is that the full legal "
        "name you'd like on the documents?"
    )
    _COVERED = {
        "testator_full_name", "marital_status", "primary_residence_state",
        "testator_street_address", "testator_city",
        "has_child_with_special_needs",
        "has_prior_marriage_or_children_outside_current_relationship",
        "owns_real_estate_outside_primary_jurisdiction",
        "owns_or_controls_business_interest", "expects_family_conflict",
        "wants_healthcare_directive", "wants_poa_document",
    }

    def test_bundled_message_produces_no_boolean_fallback(self) -> None:
        from app.domains.interview.service import _deterministic_boolean_proposal
        from app.domains.legal_content.pack_loader import get_pack

        pack = get_pack("US-VA", "2.0.0")
        result = _deterministic_boolean_proposal(
            user_message=self._IDENTITY_MESSAGE,
            assistant_reply=self._PREV_REPLY,
            question_sets=pack.question_sets,
            existing_proposal_keys=set(self._COVERED),
        )
        assert result is None

    def test_bundled_message_produces_no_text_fallback(self) -> None:
        from app.domains.interview.service import _deterministic_text_proposal
        from app.domains.legal_content.pack_loader import get_pack

        pack = get_pack("US-VA", "2.0.0")
        result = _deterministic_text_proposal(
            user_message=self._IDENTITY_MESSAGE,
            assistant_reply=self._PREV_REPLY,
            question_sets=pack.question_sets,
            existing_proposal_keys=set(self._COVERED) | {"has_minor_children"},
        )
        assert result is None

    def test_boolean_value_comes_from_the_topic_clause(self) -> None:
        """A leading 'Yes' about a covered topic must not flip the one
        undecided boolean whose own clause says No (and vice versa)."""
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message=(
                "Yes, we do own a business interest. And no — I don't "
                "expect any disagreement among family members."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=_questions(),
            existing_proposal_keys={"owns_or_controls_business_interest"},
        )
        assert result is not None
        assert result["question_key"] == "expects_family_conflict"
        assert result["value"] is False

        result = _deterministic_boolean_proposal(
            user_message=(
                "No, we do not own a business. Yes, I do expect "
                "disagreement among family members about the plan."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=_questions(),
            existing_proposal_keys={"owns_or_controls_business_interest"},
        )
        assert result is not None
        assert result["question_key"] == "expects_family_conflict"
        assert result["value"] is True

    def test_short_drifted_text_answer_still_proposes(self) -> None:
        """Channel 2 (user message names the topic) survives for short,
        single-fact messages — only long bundled paragraphs are barred."""
        from app.domains.interview.service import _deterministic_text_proposal

        class _TextQs:
            pass

        qs = {
            "addresses": {
                "sections": [
                    {
                        "id": "a",
                        "questions": [
                            {
                                "key": "testator_street_address",
                                "type": "text",
                                "label": "What is your street address?",
                            }
                        ],
                    }
                ]
            }
        }
        result = _deterministic_text_proposal(
            user_message="123 Main Street is my street address",
            assistant_reply="Let's talk about your wishes.",
            question_sets=qs,
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "testator_street_address"


class TestWholeMessageCopyGuard:
    """The model sometimes 'extracts' a notes/text value by copying the
    user's ENTIRE multi-topic paragraph into an unrelated field (live
    A/B run 1: the guardian paragraph became trust_marital_share and the
    stuck-open card blocked the key for the rest of the interview).
    Whole-message copies for a key the assistant did not just ask about
    are dropped at proposal validation."""

    _PARAGRAPH = (
        "My brother Amit Sharma should be the guardian for our children — "
        "he is trusted, he is close to the children, and he shares our "
        "values. If he can't serve, Neha Kapoor is the backup guardian."
    )

    def test_copy_for_unasked_key_is_dropped(self) -> None:
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="notes",
            value=self._PARAGRAPH,
            user_message=self._PARAGRAPH,
            last_assistant_text="Who should care for your minor children if neither parent can?",
            question_label="What share of the trust should go to your spouse outright, if any?",
        ) is True

    def test_copy_for_the_asked_key_is_kept(self) -> None:
        from app.domains.interview.service import _is_whole_message_copy

        msg = (
            "For my living will: if I am terminally ill or permanently "
            "unconscious, withhold life-sustaining treatment and keep me "
            "on comfort care only, and I want no heroic measures at all."
        )
        assert _is_whole_message_copy(
            question_type="notes",
            value=msg,
            user_message=msg,
            last_assistant_text="Do you have wishes about life-prolonging treatment?",
            question_label="Do you have wishes about life-prolonging treatment? For example, under what conditions?",
        ) is False

    def test_short_messages_are_never_dropped(self) -> None:
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="text",
            value="123 Main Street",
            user_message="123 Main Street",
            last_assistant_text="What is your street address?",
            question_label="What share of the trust should go to your spouse?",
        ) is False

    def test_partial_extraction_is_kept(self) -> None:
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="notes",
            value="trusted, close to the children, shares our values",
            user_message=self._PARAGRAPH,
            last_assistant_text="Who should care for your minor children?",
            question_label="Why is this person the right guardian?",
        ) is False

    def test_non_text_types_are_never_dropped(self) -> None:
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="person",
            value=self._PARAGRAPH,
            user_message=self._PARAGRAPH,
            last_assistant_text="Who should care for your minor children?",
            question_label="What share of the trust should go to your spouse?",
        ) is False

    def test_generic_word_overlap_does_not_count_as_asked(self):
        # A/B run 2 regression: an acknowledgment containing "should"
        # and "spouse" made the old >=2-generic-words test treat an
        # un-asked key as asked, keeping a whole-message copy alive.
        from app.domains.interview.service import _is_whole_message_copy

        message = (
            "My brother Amit Sharma should be the guardian for our "
            "children because he is trusted and he is close to the "
            "children and he shares our values completely and if he "
            "cannot serve then Neha Kapoor is the backup guardian for "
            "the children in every respect that matters to us both"
        )
        assert _is_whole_message_copy(
            question_type="notes",
            value=message,
            user_message=message,
            last_assistant_text=(
                "Thanks. Should your spouse Priya be listed anywhere "
                "else for the will signing?"
            ),
            question_label=(
                "What share of the trust should go to your spouse "
                "outright, if any?"
            ),
        )

    def test_drift_answer_to_two_turns_ago_question_is_kept(self):
        # Guard reviewer (ca4c8fa): the assistant asked about the living
        # will two turns ago, then moved to funeral wishes; the user's
        # long reply answers the LIVING WILL question faithfully. The
        # older message carries two distinctive label words
        # (prolonging/treatment) -> treated as asked -> kept.
        from app.domains.interview.service import _is_whole_message_copy

        message = (
            "About the life-prolonging treatment question from before: "
            "if I am terminally ill or permanently unconscious then "
            "withhold life-sustaining treatment and keep me on comfort "
            "care only because that is what I would want for myself "
            "and for my family at that time in every circumstance"
        )
        assert not _is_whole_message_copy(
            question_type="notes",
            value=message,
            user_message=message,
            last_assistant_text=(
                "Thank you. Do you have any funeral or burial wishes "
                "you would like recorded?"
            ),
            prior_assistant_text=(
                "What are your wishes about life-prolonging treatment, "
                "for example under what conditions should treatment be "
                "withheld?"
            ),
            question_label=(
                "What are your wishes about life-prolonging treatment, "
                "for example under what conditions?"
            ),
        )

    def test_prior_window_needs_two_distinctive_words(self):
        # One distinctive word in the older message is not enough.
        from app.domains.interview.service import _is_whole_message_copy

        message = (
            "My brother Amit Sharma should be the guardian for our "
            "children because he is trusted and he is close to the "
            "children and he shares our values completely and if he "
            "cannot serve then Neha Kapoor is the backup guardian for "
            "the children in every respect that matters to us both"
        )
        assert _is_whole_message_copy(
            question_type="notes",
            value=message,
            user_message=message,
            last_assistant_text="Do you have significant debts?",
            prior_assistant_text=(
                "Earlier we mentioned your spouse. Anything else?"
            ),
            question_label=(
                "What share of the trust should go to your spouse "
                "outright, if any?"
            ),
        )


class TestCopyGuardReviewFixes:
    """Reviewer findings on ca4c8fa (full verdict delivered post-build)."""

    GUARDIAN_PARA = (
        "My brother Amit Sharma should be the guardian for our children "
        "— he is trusted, he is close to the children, and he shares "
        "our values. If he can't serve, Neha Kapoor is the backup "
        "guardian."
    )

    def test_duplicate_label_word_counts_once(self):
        # living_will_directives' label repeats "treatment"; one distinct
        # word in an ack about another topic must not count as asked.
        from app.domains.interview.service import _is_whole_message_copy

        message = (
            "About the life-prolonging treatment question from before: "
            "if I am terminally ill or permanently unconscious then "
            "withhold life-sustaining treatment and keep me on comfort "
            "care only because that is what I would want for myself "
            "and for my family at that time in every circumstance"
        )
        assert _is_whole_message_copy(
            question_type="notes",
            value=self.GUARDIAN_PARA,
            user_message=self.GUARDIAN_PARA,
            last_assistant_text=(
                "Earlier we mentioned treatment. Who is your backup "
                "guardian?"
            ),
            question_label=(
                "What are your wishes about life-prolonging treatment, "
                "for example under what conditions should treatment be "
                "withheld?"
            ),
        )

    def test_near_copy_with_one_word_edit_is_dropped(self):
        from app.domains.interview.service import _is_whole_message_copy

        edited = self.GUARDIAN_PARA.replace("trusted", "dependable")
        assert _is_whole_message_copy(
            question_type="notes",
            value=edited,
            user_message=self.GUARDIAN_PARA,
            last_assistant_text="Do you have significant debts?",
            question_label=(
                "What share of the trust should go to your spouse "
                "outright, if any?"
            ),
        )

    def test_focus_membership_counts_as_asked(self):
        from app.domains.interview.service import _is_whole_message_copy

        assert not _is_whole_message_copy(
            question_type="notes",
            value=self.GUARDIAN_PARA,
            user_message=self.GUARDIAN_PARA,
            last_assistant_text="Tell me more about your wishes.",
            question_label=(
                "What share of the trust should go to your spouse "
                "outright, if any?"
            ),
            asked_keys=frozenset({"trust_marital_share"}),
            question_key="trust_marital_share",
        )

    def test_text_fallback_does_not_recreate_the_incident_copy(self):
        # The guard drops the model's copy; the deterministic TEXT
        # fallback must not re-create the same proposal in the same
        # turn (36-word incident paragraph, channel 2).
        from app.domains.interview.service import _deterministic_text_proposal
        from app.domains.legal_content.pack_loader import get_pack

        pack = get_pack("US-VA", "2.0.0")
        result = _deterministic_text_proposal(
            user_message=self.GUARDIAN_PARA,
            assistant_reply=(
                "Who should care for your minor children if neither "
                "parent can?"
            ),
            question_sets=pack.question_sets,
            existing_proposal_keys=set(),
        )
        assert result is None


class TestBooleanBorrowReviewFixes:
    """The polarity borrow must be polarity-ONLY and same-sentence."""

    def test_borrow_fragment_with_another_topic_does_not_fabricate(self):
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message=(
                "No business interests, I expect disagreement among "
                "family members about the plan is something my brother "
                "keeps raising."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=_questions(),
            existing_proposal_keys={"owns_or_controls_business_interest"},
        )
        assert result is None

    def test_borrow_across_a_period_does_not_fabricate(self):
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message=(
                "No. Family members expect disagreement among "
                "themselves about the plan, according to my brother."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=_questions(),
            existing_proposal_keys={"owns_or_controls_business_interest"},
        )
        assert result is None

    def test_compound_label_unanimous_clauses_propose(self):
        from app.domains.interview.service import _deterministic_boolean_proposal

        qs = {
            "household": {
                "sections": [
                    {
                        "id": "basics",
                        "questions": [
                            {
                                "key": "has_prior_marriage_or_children_outside",
                                "type": "boolean",
                                "label": (
                                    "Do you have a prior marriage or "
                                    "children outside your current "
                                    "relationship?"
                                ),
                            }
                        ],
                    }
                ]
            }
        }
        result = _deterministic_boolean_proposal(
            user_message=(
                "No, I have no prior marriages, and no children "
                "outside my current marriage."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=qs,
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["value"] is False


class TestBooleanBorrowSeparatorShapes:
    """Batch review M-1: the borrow must survive the natural
    'Yes, and ...' / 'Yes; ...' shapes while still refusing to cross
    a sentence boundary."""

    def test_borrow_across_comma_and(self):
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message=(
                "Yes, and I expect disagreement among family members "
                "about the plan."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["question_key"] == "expects_family_conflict"
        assert result["value"] is True

    def test_borrow_across_semicolon(self):
        from app.domains.interview.service import _deterministic_boolean_proposal

        result = _deterministic_boolean_proposal(
            user_message=(
                "Yes; I expect disagreement among family members "
                "about the plan."
            ),
            assistant_reply="Tell me about your household.",
            question_sets=_questions(),
            existing_proposal_keys=set(),
        )
        assert result is not None
        assert result["value"] is True
