"""Deterministic extraction guards (Interview v2 A/B follow-up, 2026-10-24).

The v2 A/B (see goals/legacyflow-product-build/hidden_files/
interview-v2-ab-results-2026-10-24.md) showed the deterministic machinery
was correct in every run while gpt-4o's extraction variance kept
breaking the bar. Three server-side guards close the observed classes:

- G1 (person grounding): a model proposal for a person field whose name
  does not appear in the CURRENT user message is dropped; a
  successor/backup proposal needs successor wording paired with the
  name (or a bare-name answer) in that message. Kills the run-4
  successor copies of "Priya Sharma" at the agents-details turn and the
  confirmation run's successor_trustee smear.
- G1c (short-value grounding): the same discipline for short
  text/notes/select values — run 4's trust_initial_property "All
  assets", asserted from a paragraph that never mentioned assets.
- G2 (copy guard, focus-authoritative): on v2 turns the asked-test is
  the server's own persisted focus window ONLY — no label-word
  archaeology, no <=25-word exemption (run 4's marital-share copy was
  saved by label drift; the confirmation run's distribution-age copy
  escaped through the word cap).
- G3 (completeness fallback): on cluster turns, focus fields the model
  proposed nothing for get one scoped run of the existing deterministic
  fallbacks (run 4's empty turns 4 and 11).

Bundle-verdict fills are server-generated (source "bundle") and never
pass through the model-draft validation loop, so they are exempt from
G1 by construction.
"""

from __future__ import annotations

# Persona paragraphs, verbatim from the A/B harness (Sharma script).
GUARDIAN_PARA = (
    "My brother Amit Sharma should be the guardian for our children — "
    "he is trusted, he is close to the children, and he shares our "
    "values. If he can't serve, Neha Kapoor is the backup guardian."
)
EXECUTOR_PARA = (
    "My wife Priya Sharma should be the executor of my will. If she "
    "can't serve, my brother Amit Sharma is the backup executor."
)
AGENTS_DETAILS_PARA = (
    "A few remaining details: no additional powers beyond the standard "
    "ones for my financial agent. My healthcare agent's phone number "
    "is 512-555-0100. There are no limitations on what she may decide "
    "about my care. For my living will, if I am terminally ill, I want "
    "comfort care only, no heroic measures. Yes, I wish to donate "
    "organs for transplantation."
)
TRUST_PARA = (
    "Yes, I want a revocable living trust. Amit Sharma is the "
    "successor trustee. Put everything — all assets — into the trust. "
    "The children should receive their inheritance outright at age 25. "
    "Everything should go to my spouse outright as the marital share."
)
MONEY_PARA = (
    "We own our home, worth about $750,000, and we have bank accounts, "
    "brokerage and investment accounts, retirement accounts, and life "
    "insurance — about $500,000 in savings and investments total. My "
    "wife is the beneficiary on my retirement accounts and life "
    "insurance. We have a mortgage of about $400,000 and some credit "
    "card debt."
)
IDENTITY_PARA = (
    "My full legal name is Arjun Sharma. I'm married, and my primary "
    "residence is in Texas — I live at 123 Main Street, Austin. "
    "A few quick answers: no child with special needs, no prior "
    "marriages or children outside my current marriage, no real "
    "estate outside Texas, no business interests, and I don't "
    "expect any family conflicts. Yes, I want a durable financial "
    "power of attorney, and yes, I want an advance healthcare directive."
)
# Pruned script (Jordan Lee): one sentence names both agent roles and
# both successors explicitly.
PRUNED_AGENTS_PARA = (
    "For the financial power of attorney, my agent is Maya Lee and "
    "the successor agent is David Lee. For healthcare, my agent is "
    "Maya Lee and David Lee is the successor healthcare agent."
)


class TestPersonGrounding:
    """G1: the name must appear in the CURRENT message."""

    def test_successor_copies_at_agents_details_turn_are_dropped(self):
        # Run 4, turn 13: the model copied the confirmed primaries into
        # both successor slots from a paragraph naming NOBODY.
        from app.domains.interview.service import _grounding_drop_reason

        for key in ("successor_poa_agent_person", "successor_healthcare_agent_person"):
            assert _grounding_drop_reason(
                question_key=key,
                value="Priya Sharma",
                question_type="person",
                user_message=AGENTS_DETAILS_PARA,
            ) == "person_name_absent"

    def test_absent_name_on_primary_is_dropped(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_guardian_person",
            value="Rahul Verma",
            question_type="person",
            user_message=GUARDIAN_PARA,
        ) == "person_name_absent"

    def test_name_present_on_primary_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_guardian_person",
            value="Amit Sharma",
            question_type="person",
            user_message=GUARDIAN_PARA,
        ) is None

    def test_non_string_person_value_is_not_judged(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_guardian_person",
            value=None,
            question_type="person",
            user_message=GUARDIAN_PARA,
        ) is None


class TestSuccessorSignal:
    """G1 successor rule: reuse is a confirmation, never an inference.
    A successor/backup proposal survives only when the CURRENT message
    pairs the name with successor wording, or is a bare-name answer."""

    def test_unsignalled_copy_is_dropped_even_when_name_present(self):
        # Confirmation run, turn 7: successor_trustee = "Priya Sharma"
        # proposed from the executor paragraph. Priya IS named there —
        # as the (primary) executor; nothing pairs her with a successor
        # role ("if she can't serve" names Amit via pronoun).
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "successor_unsignalled"

    def test_explicit_backup_wording_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="backup_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) is None

    def test_explicit_successor_wording_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="Amit Sharma",
            question_type="person",
            user_message=TRUST_PARA,
        ) is None

    def test_pruned_successors_survive(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_healthcare_agent_person",
            value="David Lee",
            question_type="person",
            user_message=PRUNED_AGENTS_PARA,
        ) is None
        assert _grounding_drop_reason(
            question_key="poa_agent_person",
            value="Maya Lee",
            question_type="person",
            user_message=PRUNED_AGENTS_PARA,
        ) is None

    def test_bare_name_successor_answer_survives(self):
        # The assistant asked "and who as successor?" and the user
        # answered with just the name — a direct answer, not a copy.
        # The license requires the PRIMARY role to be settled already
        # (review I-3): only then is a bare name unambiguously the
        # successor's answer.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_poa_agent_person",
            value="Amit Sharma",
            question_type="person",
            user_message="Amit Sharma",
            primary_covered=True,
        ) is None

    def test_bare_name_without_primary_coverage_is_dropped(self):
        # Review I-3: a bare "Maya Lee" answering the PRIMARY agent
        # question must not simultaneously license a successor copy of
        # the same name.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_poa_agent_person",
            value="Maya Lee",
            question_type="person",
            user_message="Maya Lee",
        ) == "successor_unsignalled"

    def test_nomination_sentence_for_another_role_is_not_a_bare_answer(self):
        # "My wife Priya Sharma should be executor" nominates her as
        # PRIMARY executor; it must not read as a bare-name answer that
        # licenses a successor copy of the same name.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_poa_agent_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "successor_unsignalled"


class TestShortValueGrounding:
    """G1c: short text/notes/select values must be grounded in the
    current message (the "All assets" class). Long values (snippets,
    faithful notes) are the copy guard's domain and are not judged."""

    def test_all_assets_asserted_from_money_paragraph_is_dropped(self):
        # Run 4, turn 3: trust_initial_property = "All assets" proposed
        # while the user was talking about accounts and debts — the
        # word "assets" never appears. (The key is notes, NOT person:
        # G1 does not apply; G1c does.)
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="trust_initial_property",
            value="All assets",
            question_type="notes",
            user_message=MONEY_PARA,
        ) == "value_not_in_message"

    def test_all_assets_stated_in_trust_paragraph_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="trust_initial_property",
            value="All assets",
            question_type="notes",
            user_message=TRUST_PARA,
        ) is None

    def test_short_scalar_values_grounded_in_message_survive(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="trust_distribution_age",
            value="25",
            question_type="text",
            user_message=TRUST_PARA,
        ) is None
        assert _grounding_drop_reason(
            question_key="healthcare_agent_phone",
            value="512-555-0100",
            question_type="text",
            user_message=AGENTS_DETAILS_PARA,
        ) is None
        assert _grounding_drop_reason(
            question_key="marital_status",
            value="married",
            question_type="single_select",
            user_message=IDENTITY_PARA,
        ) is None

    def test_unpunctuated_phone_value_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="healthcare_agent_phone",
            value="5125550100",
            question_type="text",
            user_message=AGENTS_DETAILS_PARA,
        ) is None

    def test_long_values_use_the_fraction_rule(self):
        # Review I-2 replaced the five-token cliff with a uniform
        # fraction: >5 judgeable tokens need >=80% present. A faithful
        # long paraphrase survives; a long value barely overlapping
        # the message is an ungrounded assertion and drops.
        from app.domains.interview.service import _grounding_drop_reason

        value = (
            "Everything to my wife Priya Sharma, then equally to "
            "our three children"
        )
        assert _grounding_drop_reason(
            question_key="residuary_beneficiaries",
            value=value,
            question_type="notes",
            user_message=(
                "Everything else goes to my wife Priya Sharma, and "
                "if she can't take it, equally to our three children."
            ),
        ) is None
        assert _grounding_drop_reason(
            question_key="residuary_beneficiaries",
            value=value,
            question_type="notes",
            user_message="Everything to my wife.",
        ) == "value_not_in_message"

    def test_non_string_and_list_values_are_not_judged(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="has_debts",
            value=True,
            question_type="boolean",
            user_message=MONEY_PARA,
        ) is None
        assert _grounding_drop_reason(
            question_key="asset_categories_present",
            value=["bank", "brokerage"],
            question_type="multi_select",
            user_message=MONEY_PARA,
        ) is None

    def test_spouse_synonym_paraphrase_survives(self):
        # Review I-2: the model normalizes "my wife" -> "spouse" and
        # "all of it" -> "everything"; the fact WAS stated.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="trust_marital_share",
            value="Everything to my spouse outright",
            question_type="notes",
            user_message="All of it goes to my wife, outright.",
        ) is None

    def test_negation_code_value_survives(self):
        # Review I-2: a semantic "None" value for a limitations field
        # whose user text says "I don't want any limits...".
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="healthcare_agent_limitations",
            value="None",
            question_type="notes",
            user_message="I don't want any limits on what she may decide.",
        ) is None

    def test_date_format_variants_survive(self):
        # Review I-2: "March 5, 1980" vs the user's "3/5/1980".
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="testator_date_of_birth",
            value="March 5, 1980",
            question_type="text",
            user_message="I was born on 3/5/1980.",
        ) is None

    def test_code_shaped_select_values_are_not_judged(self):
        # Review M-1: option codes are pack data, not prose; a value
        # with code shape (underscores) can't be token-grounded.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="property_ownership_form",
            value="tenants_in_common",
            question_type="single_select",
            user_message="We hold it together, the two of us.",
        ) is None

    def test_role_address_matching_the_bundle_anchor_survives(self):
        # Review C-1: the user pastes the composed anchor address with
        # abbreviations ("St.", "TX") — the value IS the server's own
        # composed anchor for the address bundle, so it stands even
        # though "street"/"texas" never literally appear.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="poa_agent_address",
            value="123 Main Street, Austin, Texas",
            question_type="text",
            user_message="My agent is at 123 Main St., Austin, TX.",
            anchor_address="123 Main Street, Austin, Texas",
        ) is None

    def test_anchor_exemption_is_scoped_to_role_address_keys(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="testator_street_address",
            value="123 Main Street, Austin, Texas",
            question_type="text",
            user_message="My agent is at 123 Main St., Austin, TX.",
            anchor_address="123 Main Street, Austin, Texas",
        ) == "value_not_in_message"


class TestCopyGuardFocusAuthoritative:
    """G2: on v2 turns the asked-test is the persisted focus window
    only. The legacy label-word path is kept for v1 turns."""

    _SUMMARY_REPLY = (
        "Thanks, those address details are set. Here's where we stand "
        "so far: Your spouse Priya Sharma will receive everything "
        "first; anything remaining goes equally to your three "
        "children, Aarav, Diya, and Kabir. Any property placed in a "
        "trust will follow the trust instructions. You can review or "
        "change any of these at any time, without needing to complete "
        "everything at once."
    )
    _MARITAL_LABEL = (
        "What share of the trust should go to your spouse outright, "
        "if any?"
    )
    _PEOPLE_FOCUS = frozenset({
        "primary_guardian_person", "backup_guardian_person",
        "guardian_reason", "primary_executor_person",
        "backup_executor_person",
    })

    def test_legacy_path_still_saves_the_run4_copy(self):
        # Documents the residual G2 closes: with the label/drift test,
        # a summary reply mentioning "spouse" + "trust" counts as
        # having asked about the marital share, so the guardian
        # paragraph copy survives on the v1 path.
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="notes",
            value=GUARDIAN_PARA,
            user_message=GUARDIAN_PARA,
            last_assistant_text=self._SUMMARY_REPLY,
            question_label=self._MARITAL_LABEL,
            asked_keys=self._PEOPLE_FOCUS,
            question_key="trust_marital_share",
        ) is False

    def test_authoritative_path_drops_the_run4_copy(self):
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="notes",
            value=GUARDIAN_PARA,
            user_message=GUARDIAN_PARA,
            last_assistant_text=self._SUMMARY_REPLY,
            question_label=self._MARITAL_LABEL,
            asked_keys=self._PEOPLE_FOCUS,
            question_key="trust_marital_share",
            focus_authoritative=True,
        ) is True

    def test_authoritative_keeps_copies_for_window_keys(self):
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="notes",
            value=GUARDIAN_PARA,
            user_message=GUARDIAN_PARA,
            last_assistant_text=self._SUMMARY_REPLY,
            question_label=self._MARITAL_LABEL,
            asked_keys=self._PEOPLE_FOCUS | {"trust_marital_share"},
            question_key="trust_marital_share",
            focus_authoritative=True,
        ) is False

    def test_authoritative_drops_short_message_copy(self):
        # Confirmation run: trust_distribution_age = the whole
        # executor paragraph (24 words) — under the legacy <=25-word
        # cap it was never even evaluated.
        from app.domains.interview.service import _is_whole_message_copy

        kwargs = dict(
            question_type="text",
            value=EXECUTOR_PARA,
            user_message=EXECUTOR_PARA,
            last_assistant_text="Who should be the executor of your will?",
            question_label=(
                "At what age should your children receive their "
                "inheritance outright? (common choices: 21, 25, 30)"
            ),
            asked_keys=self._PEOPLE_FOCUS,
            question_key="trust_distribution_age",
        )
        assert _is_whole_message_copy(**kwargs) is False  # legacy cap
        assert _is_whole_message_copy(**kwargs, focus_authoritative=True) is True

    def test_authoritative_keeps_snippet_extraction(self):
        from app.domains.interview.service import _is_whole_message_copy

        assert _is_whole_message_copy(
            question_type="notes",
            value="trusted, close to the children",
            user_message=GUARDIAN_PARA,
            last_assistant_text="Tell me about your wishes.",
            question_label=self._MARITAL_LABEL,
            asked_keys=self._PEOPLE_FOCUS,
            question_key="guardian_reason",
            focus_authoritative=True,
        ) is False


class TestCompletenessFallback:
    """G3: scoped fallback capture for focus fields the model skipped."""

    _TYPES = {
        "wants_revocable_trust": "boolean",
        "successor_trustee_person": "person",
        "trust_initial_property": "notes",
        "trust_distribution_age": "text",
        "trust_marital_share": "notes",
        "asset_categories_present": "multi_select",
        "primary_guardian_person": "person",
        "backup_guardian_person": "person",
        "successor_poa_agent_person": "person",
    }
    _TRUST_FOCUS = [
        "wants_revocable_trust", "successor_trustee_person",
        "trust_initial_property", "trust_distribution_age",
        "trust_marital_share",
    ]
    _TRUST_QUESTION = (
        "Let's cover the trust: do you want a revocable living trust, "
        "who should take over as successor trustee, which assets go "
        "in, at what age do children receive their inheritance "
        "outright, and what share goes to your spouse?"
    )

    def _pack_sets(self):
        from app.domains.legal_content.pack_loader import get_pack

        return get_pack("US-VA", "2.0.0").question_sets

    def test_fat_paragraph_captures_uncovered_focus_fields(self):
        # Run 4 turns 9/15 shape: the trust paragraph is sent, the
        # model extracts a subset (or nothing) — the remaining focus
        # fields are captured deterministically THIS turn instead of
        # forcing a re-send.
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=self._TRUST_FOCUS,
            covered_keys={"trust_initial_property"},
            question_types=self._TYPES,
            user_message=TRUST_PARA,
            assistant_reply=self._TRUST_QUESTION,
            question_sets=self._pack_sets(),
        )
        by_key = {p["question_key"]: p for p in out}
        assert by_key["trust_distribution_age"]["value"] == TRUST_PARA
        assert by_key["trust_marital_share"]["value"] == TRUST_PARA
        # Two boundaries of the reused machinery, pinned explicitly:
        # the person fallback's sentence discipline abstains on this
        # paragraph (its comma guard), and the boolean fallback
        # abstains because the trust topic spans two clauses that
        # carry no polarity token (the "Yes" sits in its own fragment)
        # — the model owns those two extractions, and in every A/B run
        # it performed them; G3 covers the fields it demonstrably
        # skips (the free-text terms).
        assert "successor_trustee_person" not in by_key
        assert "wants_revocable_trust" not in by_key
        assert all(p["confidence"] < 0.9 for p in out)

    def test_boolean_focus_field_captured_from_single_topic_answer(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["has_specific_bequests"],
            covered_keys=set(),
            question_types={**self._TYPES, "has_specific_bequests": "boolean"},
            user_message=(
                "No, I don't want to leave specific items or amounts "
                "to particular people."
            ),
            assistant_reply=(
                "Are there specific items or amounts you want to leave "
                "to particular people or charities?"
            ),
            question_sets=self._pack_sets(),
        )
        assert len(out) == 1
        assert out[0]["question_key"] == "has_specific_bequests"
        assert out[0]["value"] is False

    def test_covered_and_non_focus_keys_are_never_proposed(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["wants_revocable_trust"],
            covered_keys={"wants_revocable_trust"},
            question_types=self._TYPES,
            user_message=TRUST_PARA,
            assistant_reply=self._TRUST_QUESTION,
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_focus_key_without_a_fallback_produces_nothing(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["asset_categories_present"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=MONEY_PARA,
            assistant_reply="Which of these asset categories do you have?",
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_person_focus_without_a_name_produces_nothing(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["successor_poa_agent_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=AGENTS_DETAILS_PARA,
            assistant_reply="A few more agent details, please.",
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_primary_person_captured_from_guardian_paragraph(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["primary_guardian_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=GUARDIAN_PARA,
            assistant_reply="Who should be the guardian for your children?",
            question_sets=self._pack_sets(),
        )
        assert len(out) == 1
        assert out[0]["question_key"] == "primary_guardian_person"
        assert out[0]["value"] == "Amit Sharma"

    def test_successor_key_never_gets_the_primarys_name(self):
        # The person fallback extracts the main-clause nominee (Amit,
        # the PRIMARY guardian) — G3 must not mint him as the backup:
        # the extracted name carries no successor pairing.
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["backup_guardian_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=GUARDIAN_PARA,
            assistant_reply="Tell me about guardianship.",
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_boolean_requires_user_channel_corroboration(self):
        # Review I-1: scoped to one key, the boolean fallback's
        # assistant channel matches trivially (the server asked about
        # the key) and a sibling clause carrying just two of the
        # label's words would mint a value. G3 accepts a boolean only
        # when the USER's own words also clear the candidate bar
        # (>=2 label words AND >=50% of them).
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["has_specific_bequests"],
            covered_keys=set(),
            question_types={**self._TYPES, "has_specific_bequests": "boolean"},
            user_message="No specific items were handed down in my family.",
            assistant_reply=(
                "Are there specific items or amounts you want to leave "
                "to particular people or charities?"
            ),
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_bare_name_successor_needs_the_primary_answered(self):
        # Review I-3 (G3 side): a bare name minting a successor is
        # licensed only when the primary role is already answered —
        # otherwise the name most plausibly answers the PRIMARY slot.
        from app.domains.interview.service import _focus_completeness_proposals

        kwargs = dict(
            focus_keys=["successor_poa_agent_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message="Maya Lee",
            assistant_reply="And who should serve if your first choice cannot?",
            question_sets=self._pack_sets(),
        )
        assert _focus_completeness_proposals(**kwargs) == []
        out = _focus_completeness_proposals(
            **kwargs, answered_keys={"poa_agent_person"}
        )
        assert len(out) == 1
        assert out[0]["question_key"] == "successor_poa_agent_person"
        assert out[0]["value"] == "Maya Lee"


class TestGuardWiring:
    def test_post_turn_applies_the_guards(self):
        import inspect

        from app.domains.interview import service

        src = inspect.getsource(service.post_turn)
        assert "_grounding_drop_reason(" in src
        assert "_focus_completeness_proposals(" in src
        assert "focus_authoritative" in src
        assert "dropped_grounding_keys" in src

    def test_bundle_fills_do_not_pass_through_the_grounding_guard(self):
        # Bundle fills are inserted directly by post_turn with
        # source="bundle"; the fill-key computation neither knows nor
        # cares whether the anchor name appears in the current user
        # message (the YES verdict IS the grounding).
        from app.domains.interview.service import _bundle_fill_keys

        offer = {"fill_keys": ["successor_poa_agent_person"]}
        assert _bundle_fill_keys(
            offer, pending=set(), open_now=set(), decided=set(), answers={}
        ) == ["successor_poa_agent_person"]
