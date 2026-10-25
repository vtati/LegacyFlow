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
        # role ("if she can't serve" names Amit via pronoun). Since
        # fix iteration 1 the role check fires first here (the
        # paragraph never speaks of trustees at all) — same drop,
        # more specific reason.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "person_role_mismatch"

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

    def test_incumbent_named_in_the_same_sentence_is_not_signalled(self):
        # A/B set 1, Pruned run 1 (the deadlock): the roles paragraph
        # names the incumbent AND the successor in ONE sentence ("my
        # agent is Maya Lee and the successor agent is David Lee").
        # Sentence-level pairing licensed a successor copy of MAYA —
        # the harness rejected it, the key became decided, the bundle
        # fill skipped it forever, and the interview looped. The
        # signal must pair with the name inside the same CLAUSE.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_poa_agent_person",
            value="Maya Lee",
            question_type="person",
            user_message=PRUNED_AGENTS_PARA,
        ) == "successor_unsignalled"
        assert _grounding_drop_reason(
            question_key="successor_healthcare_agent_person",
            value="Maya Lee",
            question_type="person",
            user_message=PRUNED_AGENTS_PARA,
        ) == "successor_unsignalled"

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
        # licenses a successor copy of the same name. (The role check
        # fires first: the paragraph never mentions agents/financial
        # roles — the drop is the same.)
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_poa_agent_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "person_role_mismatch"


class TestPersonRoleDiscipline:
    """Fix iteration 1 (A/B set 1, Sharma run 2): the model filed the
    GUARDIAN paragraph's nominee under primary_executor_person — the
    name is in the message, so token grounding kept it. In a fat
    paragraph, a person proposal needs the message to speak the key's
    role; short direct answers are exempt (their role comes from the
    question). And a primary nominee named ONLY in successor clauses
    is a primary/backup swap."""

    def test_cross_role_smear_is_dropped(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=GUARDIAN_PARA,
        ) == "person_role_mismatch"
        assert _grounding_drop_reason(
            question_key="primary_guardian_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "person_role_mismatch"

    def test_matching_role_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_guardian_person",
            value="Amit Sharma",
            question_type="person",
            user_message=GUARDIAN_PARA,
        ) is None
        assert _grounding_drop_reason(
            question_key="healthcare_agent_person",
            value="Maya Lee",
            question_type="person",
            user_message=PRUNED_AGENTS_PARA,
        ) is None

    def test_bare_name_answer_is_exempt_from_role_check(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Priya Sharma",
            question_type="person",
            user_message="Priya Sharma",
        ) is None

    def test_primary_backup_swap_is_dropped(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "primary_role_swapped"
        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) is None


class TestRoleGuardLaypersonPhrasing:
    """Review round 2, I-1/I-2/I-3/I-5/I-6: the role and signal
    disciplines must survive how laypeople actually write — plurals,
    synonyms, appositives, colon/em-dash clauses — while still
    dropping the smears."""

    def test_plural_and_synonym_role_phrasing_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        cases = [
            ("healthcare_agent_person", "Maya Lee",
             "My sister Maya Lee should make all the medical decisions "
             "for me if I am unable to communicate my wishes myself."),
            ("poa_agent_person", "Maya Lee",
             "I want my sister Maya Lee to have power of attorney and "
             "make decisions about money and bills on my behalf."),
            ("primary_executor_person", "Priya Sharma",
             "My wife Priya Sharma should settle my estate and carry "
             "out everything I have laid out for after I am gone."),
            ("successor_trustee_person", "Amit Sharma",
             "Amit Sharma should manage the trust if I cannot manage "
             "it myself any longer at some point in the future."),
            ("backup_guardian_person", "Neha Kapoor",
             "Neha Kapoor is one of the backup guardians we would "
             "want for the children if something happens to us."),
        ]
        for key, value, message in cases:
            assert _grounding_drop_reason(
                question_key=key, value=value, question_type="person",
                user_message=message,
            ) is None, key

    def test_appositive_successor_phrasing_survives(self):
        from app.domains.interview.service import _grounding_drop_reason

        cases = [
            ("backup_guardian_person", "Neha Kapoor",
             "If he can't serve, Neha Kapoor, as the backup guardian, "
             "should take care of the children in our home."),
            ("backup_executor_person", "Amit Sharma",
             "My backup, Amit Sharma, should serve if Priya cannot "
             "do it when the time comes for whatever reason."),
            ("backup_guardian_person", "Neha Kapoor",
             "As backup guardian, Neha Kapoor should serve if Amit "
             "cannot do it when the time comes for the children."),
        ]
        for key, value, message in cases:
            assert _grounding_drop_reason(
                question_key=key, value=value, question_type="person",
                user_message=message,
            ) is None, message

    def test_colon_and_dash_clauses_do_not_license_the_incumbent(self):
        from app.domains.interview.service import _grounding_drop_reason

        for message in (
            "Roles: Maya Lee is my healthcare agent: David Lee is "
            "the successor healthcare agent for me going forward.",
            "My healthcare agent is Maya Lee — David Lee is the "
            "successor healthcare agent if she cannot serve me.",
        ):
            assert _grounding_drop_reason(
                question_key="successor_healthcare_agent_person",
                value="Maya Lee",
                question_type="person",
                user_message=message,
            ) == "successor_unsignalled", message
            assert _grounding_drop_reason(
                question_key="successor_healthcare_agent_person",
                value="David Lee",
                question_type="person",
                user_message=message,
            ) is None, message

    def test_relation_only_primary_still_catches_the_swap(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=(
                "My wife should be the executor of my will. If she "
                "can't serve, my brother Amit Sharma is the backup "
                "executor."
            ),
        ) == "primary_role_swapped"

    def test_incidental_capitalized_pairs_do_not_trigger_the_swap(self):
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=(
                "Amit Sharma was my backup choice last year but I "
                "have changed my mind completely and want him as "
                "primary executor now, and my accounts are at Wells "
                "Fargo bank in town."
            ),
        ) is None

    def test_list_punctuation_does_not_license_the_incumbent(self):
        # Review round 3, C-B: the appositive window crossed
        # semicolons, colons, spaced hyphens (punctuation attached to
        # words, not standalone tokens) and inverted comma order —
        # the Pruned deadlock shape in ordinary list punctuation.
        from app.domains.interview.service import _grounding_drop_reason

        for message in (
            "My agent is Maya Lee; the successor agent is David Lee, "
            "who lives nearby in town.",
            "My agent is Maya Lee: the successor agent is David Lee, "
            "who lives nearby in town.",
            "My agent is Maya Lee - the successor agent is David "
            "Lee, who lives nearby in town.",
            "David Lee is the successor, Maya Lee is the agent for "
            "healthcare matters in our family plan.",
        ):
            assert _grounding_drop_reason(
                question_key="successor_healthcare_agent_person",
                value="Maya Lee",
                question_type="person",
                user_message=message,
            ) == "successor_unsignalled", message
            assert _grounding_drop_reason(
                question_key="successor_healthcare_agent_person",
                value="David Lee",
                question_type="person",
                user_message=message,
            ) is None, message

    def test_first_person_conditional_is_trustee_only(self):
        # Review round 3, I-A: "if I cannot …" defines when the
        # PRIMARY agent's role activates — it is successor wording
        # only for the trustee, where the testator is the incumbent.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="successor_healthcare_agent_person",
            value="Maya Lee",
            question_type="person",
            user_message=(
                "Maya Lee should make medical decisions for me if I "
                "cannot communicate them myself, for as long as that "
                "situation lasts."
            ),
        ) == "successor_unsignalled"
        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="David Lee",
            question_type="person",
            user_message=(
                "David Lee should manage the trust if I cannot manage "
                "it myself any longer at some point in the future."
            ),
        ) is None

    def test_generic_backup_sentence_needs_the_asked_role(self):
        # Review round 3, I-B: a role-free "my backup" sentence must
        # not license EVERY successor key — the role comes from the
        # question the user was answering (post_turn passes the last
        # assistant message as context).
        from app.domains.interview.service import _grounding_drop_reason

        message = (
            "My backup, Amit Sharma, should serve if Priya cannot "
            "do it when the time comes for whatever reason at all."
        )
        context = "Quick question: who should be your backup executor?"
        assert _grounding_drop_reason(
            question_key="backup_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=message,
            assistant_context=context,
        ) is None
        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="Amit Sharma",
            question_type="person",
            user_message=message,
            assistant_context=context,
        ) == "person_role_mismatch"

    def test_summary_context_only_the_tail_question_counts(self):
        # Review round 4, I-B residual: post_turn passes the whole
        # last assistant message, and assistant messages recap ALL
        # roles before asking. Only the tail (the actual question)
        # may supply the role — a summary must not re-license every
        # successor key from one generic backup sentence.
        from app.domains.interview.service import _grounding_drop_reason

        message = (
            "My backup, Amit Sharma, should serve if Priya cannot "
            "do it when the time comes for whatever reason at all."
        )
        context = (
            "Thanks, here is a summary: Priya is executor, Amit is "
            "guardian, David is successor trustee, Maya is "
            "healthcare agent. Quick question: who should be your "
            "backup executor?"
        )
        assert _grounding_drop_reason(
            question_key="backup_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=message,
            assistant_context=context,
        ) is None
        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="Amit Sharma",
            question_type="person",
            user_message=message,
            assistant_context=context,
        ) == "person_role_mismatch"

    def test_incidental_weak_words_do_not_speak_a_role(self):
        # Review round 4, I-E regression: the round-3 evidence
        # additions (wishes / little / doctors / money) as BARE
        # tokens re-licensed cross-role smears on incidental
        # mentions. Weak words need a phrase or a second weak word.
        from app.domains.interview.service import _grounding_drop_reason

        cases = [
            ("primary_executor_person", "Maya Lee",
             "My sister Maya Lee should make medical decisions for "
             "me if I am unable to communicate, and I trust her to "
             "carry out my wishes about my care in the hospital."),
            ("primary_guardian_person", "Maya Lee",
             "My sister Maya Lee should make medical decisions for "
             "me if I am unable to communicate, and she knows a "
             "little about my treatment preferences from our talks."),
            ("healthcare_agent_person", "Priya Sharma",
             EXECUTOR_PARA + " My doctors already have a copy of "
             "the will at their office downtown for safekeeping."),
            ("poa_agent_person", "Amit Sharma",
             "Amit Sharma should look after the money we set aside "
             "for our kids if I cannot look after it myself any "
             "longer at some point later in my life."),
        ]
        for key, value, message in cases:
            assert _grounding_drop_reason(
                question_key=key, value=value, question_type="person",
                user_message=message,
            ) == "person_role_mismatch", key

    def test_phrase_evidence_needs_token_boundaries(self):
        # Final re-review: the phrase tier matched raw substrings —
        # "my will" inside "my willingness", "set aside" inside
        # "set asides", "little ones" inside "little onesie" all
        # re-licensed the smears the tier exists to stop.
        from app.domains.interview.service import _grounding_drop_reason

        cases = [
            ("primary_executor_person", "Maya Lee",
             "My sister Maya Lee should make medical decisions for "
             "me if I am unable to communicate, and my willingness "
             "to follow the hospital plan is well known to everyone "
             "in our family who has seen it happen before."),
            ("successor_trustee_person", "Maya Lee",
             "My sister Maya Lee should make medical decisions for "
             "me if I am unable to communicate, and the budget set "
             "asides are handled by our accountant at his office "
             "downtown near the courthouse on main street."),
            ("primary_guardian_person", "Maya Lee",
             "My sister Maya Lee should make medical decisions for "
             "me if I am unable to communicate, and she bought a "
             "little onesie for my niece at the store last week "
             "when they were visiting us at our house here."),
        ]
        for key, value, message in cases:
            assert _grounding_drop_reason(
                question_key=key, value=value, question_type="person",
                user_message=message,
            ) == "person_role_mismatch", key

    def test_summary_context_joined_by_newline_still_uses_the_tail(self):
        # Final re-review, Minor 1: a recap joined to its question
        # by a bare newline (no terminal punctuation) must still
        # resolve to the question as the context tail.
        from app.domains.interview.service import _grounding_drop_reason

        message = (
            "My backup, Amit Sharma, should serve if Priya cannot "
            "do it when the time comes for whatever reason at all."
        )
        context = (
            "Summary: Priya is executor, David is successor trustee\n"
            "Quick question: who should be your backup executor?"
        )
        assert _grounding_drop_reason(
            question_key="successor_trustee_person",
            value="Amit Sharma",
            question_type="person",
            user_message=message,
            assistant_context=context,
        ) == "person_role_mismatch"

    def test_accounts_clause_with_unlisted_firm_is_scenery(self):
        # Review round 4, I-D residual: a denylist of institutions
        # loses to the next firm name. An accounts clause with no
        # role word is scenery regardless of the name in it.
        from app.domains.interview.service import _grounding_drop_reason

        for firm in ("Raymond James", "Edward Jones"):
            assert _grounding_drop_reason(
                question_key="primary_executor_person",
                value="Amit Sharma",
                question_type="person",
                user_message=(
                    "Amit Sharma was my backup choice last year but "
                    "I have changed my mind completely and want him "
                    f"as primary executor now, and we should have "
                    f"{firm} handle our accounts going forward from "
                    "here on out for the whole family."
                ),
            ) is None, firm

    def test_lead_in_appositives_survive(self):
        # Review round 4, C-B minor: "I want …" lead-ins around the
        # name are still appositive nominations.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="backup_guardian_person",
            value="Neha Kapoor",
            question_type="person",
            user_message=(
                "I want Neha Kapoor, as the backup guardian, to "
                "serve if Amit cannot do it for the children."
            ),
        ) is None
        assert _grounding_drop_reason(
            question_key="backup_guardian_person",
            value="Neha Kapoor",
            question_type="person",
            user_message=(
                "As backup guardian, I want Neha Kapoor to serve "
                "if Amit cannot do it for the children at all."
            ),
        ) is None

    def test_middle_initial_primary_still_catches_the_swap(self):
        # Review round 3, I-C: sentence splitting amputated the
        # primary's clause at "K." — the span existed but the swap
        # check never saw a whole clause.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=(
                "My wife Priya K. Sharma should be the executor of "
                "my will. If she can't serve, my brother Amit Sharma "
                "is the backup executor."
            ),
        ) == "primary_role_swapped"

    def test_more_incidental_institutions_do_not_trigger_the_swap(self):
        # Review round 3, I-D: the other-nominee test rode on the
        # verb "should" plus any capitalized pair — scenery clauses
        # about accounts are not nominations.
        from app.domains.interview.service import _grounding_drop_reason

        for institution in ("Goldman Sachs", "Charles Schwab"):
            assert _grounding_drop_reason(
                question_key="primary_executor_person",
                value="Amit Sharma",
                question_type="person",
                user_message=(
                    "Amit Sharma was my backup choice last year but "
                    "I have changed my mind completely and want him "
                    f"as primary executor now, and I should also note "
                    f"that our accounts sit with {institution} and "
                    "they hold most of our investments."
                ),
            ) is None, institution
        assert _grounding_drop_reason(
            question_key="primary_executor_person",
            value="Amit Sharma",
            question_type="person",
            user_message=(
                "Amit Sharma was my backup choice last year but I "
                "have changed my mind completely and want him as "
                "primary executor now, and we should keep our "
                "Virginia Beach property in the family for the kids."
            ),
        ) is None

    def test_more_lay_role_phrasing_survives(self):
        # Review round 3, I-E: the evidence vocabulary grows by the
        # words these sentences actually use.
        from app.domains.interview.service import _grounding_drop_reason

        cases = [
            ("healthcare_agent_person", "Maya Lee",
             "My sister Maya Lee should speak with my doctors and "
             "make treatment decisions for me if I am ever unable "
             "to communicate my wishes to anyone at all."),
            ("primary_guardian_person", "Amit Sharma",
             "My brother Amit Sharma should look after our little "
             "ones if anything happens to us both while they are "
             "still young and growing up in our home town."),
            ("primary_executor_person", "Priya Sharma",
             "My wife Priya Sharma should handle everything in my "
             "will after I pass away and make sure all of my wishes "
             "are carried out fully for our family in the end."),
            ("successor_trustee_person", "Amit Sharma",
             "Amit Sharma should look after the money we set aside "
             "for our kids if I cannot look after it myself any "
             "longer at some point later in my life."),
        ]
        for key, value, message in cases:
            assert _grounding_drop_reason(
                question_key=key, value=value, question_type="person",
                user_message=message,
            ) is None, key

    def test_long_appositive_successor_phrasing_survives(self):
        # Review round 3, Minor: the signal can sit more than four
        # words from the name inside a genuine appositive fragment.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="backup_guardian_person",
            value="Neha Kapoor",
            question_type="person",
            user_message=(
                "Neha Kapoor, as the really very trusted backup "
                "guardian for our family, should take care of the "
                "children if anything ever happens to us both."
            ),
        ) is None

    def test_incumbent_as_backup_stays_unsignalled(self):
        # The conditional-signal widening (first-person "if I cannot")
        # must not license the incumbent: in the executor paragraph
        # Priya is named as the PRIMARY, and the "if she can't serve"
        # clause pairs the condition with Amit, not with her.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="backup_executor_person",
            value="Priya Sharma",
            question_type="person",
            user_message=EXECUTOR_PARA,
        ) == "successor_unsignalled"


class TestRejectedPairBlock:
    """The decider rejected (residuary, "spouse outright as the marital
    share") three turns running in Sharma run 2 — the model re-proposed
    the identical pair from context each time. Canonicalization makes
    the pair comparison case/punctuation-insensitive."""

    def test_canonical_values_compare_equal(self):
        from app.domains.interview.service import _canonical_proposal_value

        assert _canonical_proposal_value(
            "Spouse outright as the marital share"
        ) == _canonical_proposal_value("spouse outright as the marital share.")
        assert _canonical_proposal_value(True) != _canonical_proposal_value("true story")

    def test_canonicalization_normalizes_formatting_variants(self):
        # Review round 2, M-2: fixation must not escape the block by
        # reformatting the same value — list order, int/float, digit
        # punctuation, and Decimal (psycopg's jsonb numeric) all
        # canonicalize identically.
        from decimal import Decimal

        from app.domains.interview.service import _canonical_proposal_value

        assert _canonical_proposal_value(["b", "a"]) == _canonical_proposal_value(["a", "b"])
        assert _canonical_proposal_value(1) == _canonical_proposal_value(1.0)
        assert _canonical_proposal_value("512-555-0100") == _canonical_proposal_value("5125550100")
        assert _canonical_proposal_value(Decimal("25")) == _canonical_proposal_value(25)
        assert _canonical_proposal_value(25) == _canonical_proposal_value("25")
        # Decimal-point strings canonicalize as numbers, not as
        # digit strings (review round 3, M-2): "25.5" is not "255".
        assert _canonical_proposal_value("25.5") == _canonical_proposal_value(25.5)
        assert _canonical_proposal_value("25.0") == _canonical_proposal_value(25)
        assert _canonical_proposal_value("750.50") == _canonical_proposal_value(750.50)
        # Currency/thousands formatting is still the same number
        # (review round 4, Minor).
        assert _canonical_proposal_value("$750.50") == _canonical_proposal_value(750.5)
        assert _canonical_proposal_value("1,000.00") == _canonical_proposal_value(1000)
        assert _canonical_proposal_value(".5") == _canonical_proposal_value(0.5)
        assert _canonical_proposal_value(".5") != _canonical_proposal_value(5)

    def test_restatement_in_a_fresh_message_lifts_the_block(self):
        # Review round 2, I-4: the user rejected a card, then changed
        # their mind and dictated the identical value in NEW words.
        # That is a reversal, not model fixation — the pair must be
        # recordable again.
        from app.domains.interview.service import _rejected_pair_restated

        assert _rejected_pair_restated(
            "Priya Sharma",
            "Actually, use Priya Sharma as executor after all.",
            [EXECUTOR_PARA],
        ) is True
        assert _rejected_pair_restated(
            True,
            "Yes, I do want the trust after all.",
            [TRUST_PARA],
        ) is True

    def test_resent_paragraph_does_not_lift_the_block(self):
        # The Sharma run-2 fixation shape: the value is a fragment of
        # a paragraph the user RE-SENT — the model re-asserting it is
        # fixation even though the words appear in the message.
        from app.domains.interview.service import _rejected_pair_restated

        assert _rejected_pair_restated(
            "spouse outright as the marital share",
            TRUST_PARA,
            [TRUST_PARA],
        ) is False

    def test_resent_paragraph_with_appended_sentence_stays_blocked(self):
        # Review round 3, I-F: set-Jaccard novelty was defeated by
        # appending ONE new sentence to a re-send ("the page seemed
        # to freeze…") — token containment of the prior message is
        # the re-send test, not symmetric similarity.
        from app.domains.interview.service import _rejected_pair_restated

        for tail in (
            " Please go ahead and record exactly this for our "
            "family, thank you so much.",
            " I am sending this again because the page seemed to "
            "freeze on me earlier today and nothing was saved at "
            "all, sorry about that.",
        ):
            assert _rejected_pair_restated(
                "spouse outright as the marital share",
                TRUST_PARA + tail,
                [TRUST_PARA],
            ) is False, tail

    def test_numeric_value_can_be_restated(self):
        # Review round 3, Minor: numeric reversals were
        # session-permanently un-recordable.
        from app.domains.interview.service import _rejected_pair_restated

        assert _rejected_pair_restated(
            25,
            "The children should receive their inheritance at age 25.",
            [TRUST_PARA],
        ) is True

    def test_ungrounded_value_does_not_lift_the_block(self):
        from app.domains.interview.service import _rejected_pair_restated

        assert _rejected_pair_restated(
            "Rahul Verma",
            "Actually, use Priya Sharma as executor after all.",
            [],
        ) is False

    def test_post_turn_applies_the_block_with_the_restatement_check(self):
        import inspect

        from app.domains.interview import service

        src = inspect.getsource(service.post_turn)
        assert "rejected_proposal_values" in src
        assert "previously_rejected" in src
        assert "_rejected_pair_restated" in src


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

    def test_person_name_is_not_an_address_value(self):
        # A/B set 1, Pruned run 1 turn 9: poa_agent_address = "David
        # Lee" — a bare person name stored as an address (the name WAS
        # in the message, so token grounding kept it). Role-address
        # values need address shape: a digit or a street token.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="poa_agent_address",
            value="David Lee",
            question_type="text",
            user_message=(
                "My sister Maya Lee should be the executor. David Lee "
                "is the backup executor."
            ),
        ) == "value_not_in_message"
        assert _grounding_drop_reason(
            question_key="poa_agent_address",
            value="456 Oak Avenue, Richmond, Virginia",
            question_type="text",
            user_message="My agent lives at 456 Oak Avenue, Richmond, Virginia.",
        ) is None
        assert _grounding_drop_reason(
            question_key="primary_guardian_address",
            value="PO Box 482, Austin, Texas",
            question_type="text",
            user_message="Use PO Box 482, Austin, Texas for the guardian.",
        ) is None


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


class TestCompletenessRoleDiscipline:
    """Review round 2, C-1: G3's covered-set subtraction in post_turn
    un-covers keys whose MODEL proposal was guard-dropped — so a
    scoped person capture must apply the same role discipline as the
    model guard, or G3 re-mints the exact cross-role smear the guard
    dropped (Sharma run 2: Amit filed as executor from the guardian
    paragraph). The subtraction itself stays: for text/copy drops it
    is what lets G3 capture the field the server asked about."""

    _TYPES = {
        "primary_executor_person": "person",
        "primary_guardian_person": "person",
    }

    def _pack_sets(self):
        from app.domains.legal_content.pack_loader import get_pack

        return get_pack("US-VA", "2.0.0").question_sets

    def test_executor_focus_not_captured_from_guardian_paragraph(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["primary_executor_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=GUARDIAN_PARA,
            assistant_reply=(
                "Let's cover the people who will serve: who should "
                "be the guardian for your children, and who should "
                "be the executor of your will?"
            ),
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_guardian_focus_not_captured_from_executor_paragraph(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["primary_guardian_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=EXECUTOR_PARA,
            assistant_reply="Who should be the guardian for your children?",
            question_sets=self._pack_sets(),
        )
        assert out == []

    def test_matching_role_capture_still_fires(self):
        from app.domains.interview.service import _focus_completeness_proposals

        out = _focus_completeness_proposals(
            focus_keys=["primary_executor_person"],
            covered_keys=set(),
            question_types=self._TYPES,
            user_message=EXECUTOR_PARA,
            assistant_reply="Who should be the executor of your will?",
            question_sets=self._pack_sets(),
        )
        assert len(out) == 1
        assert out[0]["question_key"] == "primary_executor_person"
        assert out[0]["value"] == "Priya Sharma"


class TestAddressShape:
    """Review round 2, M-1: common street suffixes (Place/Way/Alley…)
    make an address shaped even with spelled-out house numbers; a
    bare two-word person name ending in a street-token surname
    (David Lane, James Street) does not."""

    def test_spelled_out_addresses_survive(self):
        from app.domains.interview.service import _grounding_drop_reason

        for address in (
            "Twelve Elm Place, Austin, Texas",
            "Four Oak Way, Richmond, Virginia",
            "One Hundred Maple Alley, Richmond, Virginia",
        ):
            assert _grounding_drop_reason(
                question_key="poa_agent_address",
                value=address,
                question_type="text",
                user_message=f"My agent's address is {address}.",
            ) is None, address

    def test_street_token_surnames_are_not_addresses(self):
        from app.domains.interview.service import _grounding_drop_reason

        for name in ("David Lane", "James Street"):
            assert _grounding_drop_reason(
                question_key="poa_agent_address",
                value=name,
                question_type="text",
                user_message=f"{name} is my financial agent.",
            ) == "value_not_in_message", name

    def test_three_word_name_ending_in_street_token_is_not_an_address(self):
        # Review round 3, M-1 residual: "Anna Maria Lane" is a
        # person (or at best a bare street fragment) — a role address
        # needs a number or a city/state tail.
        from app.domains.interview.service import _grounding_drop_reason

        assert _grounding_drop_reason(
            question_key="poa_agent_address",
            value="Anna Maria Lane",
            question_type="text",
            user_message="Anna Maria Lane is my financial agent.",
        ) == "value_not_in_message"


class TestBundleVerdictParsing:
    """Fix iteration 1 (A/B set 1, both Pruned runs): a bundle verdict
    was consumed from a NON-verdict message — the user answered a
    presented bundle offer's turn with the agents-details paragraph,
    whose early "no additional powers" parsed as NO, recording a
    decline for an offer the user never declined; the real "Yes" one
    turn later then had no live offer and the successors never
    filled. Only a message whose HEAD reads as a verdict is one."""

    def test_verdict_with_ridealong_paragraph_counts(self):
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "Yes, please — that works for all of those.\n\nAlso: "
            + GUARDIAN_PARA
        ) is True

    def test_details_paragraph_is_not_a_verdict(self):
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(AGENTS_DETAILS_PARA) is None

    def test_trust_refusal_paragraph_is_not_a_verdict(self):
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "No, I don't want a revocable living trust. My sister "
            "should handle everything through the will instead, and "
            "I have thought about this carefully for a long time."
        ) is None

    def test_short_verdicts_still_parse(self):
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict("Yes, please — that works for all of those.") is True
        assert _bundle_verdict("No, not for us, thanks.") is False
        assert _bundle_verdict("Maybe later.") is None

    def test_no_about_another_topic_then_bundle_yes_counts(self):
        # Review round 2, I-7(a): the Pruned root-cause vocabulary in
        # miniature — a terse "No …" about ANOTHER topic followed by
        # the actual bundle verdict must not record a decline.
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "No additional powers. Yes, use him for all of those "
            "roles please"
        ) is True
        assert _bundle_verdict(
            "No limitations on her decisions. Yes, for all of those "
            "roles"
        ) is True

    def test_long_single_sentence_verdict_counts(self):
        # Review round 2, I-7(b): a clear verdict is not lost for
        # exceeding a word cap when it speaks about the bundle itself.
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "Yes, I would like David Lee to also serve in all of "
            "those roles for our family"
        ) is True
        assert _bundle_verdict(
            "Yes please, that works for all of those roles and I am "
            "happy with that choice"
        ) is True

    def test_verdict_with_single_newline_ridealong_counts(self):
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "Yes, please\nAlso my phone is 512-555-0100."
        ) is True

    def test_bundle_phrasings_the_yes_patterns_miss(self):
        # Review round 2, M-3: affirmative bundle phrasings whose yes
        # is not start-anchored still read as verdicts in the verdict
        # context (an offer was presented and is awaiting one).
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict("For all of them, yes please") is True
        assert _bundle_verdict("Please do — for all of them") is True
        assert _bundle_verdict("Same person for all, please") is True

    def test_bundle_scoped_decline_counts(self):
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict("No, not for all of those roles.") is False

    def test_negated_bundle_sentences_are_not_accepted(self):
        # Review round 3, C-A: the bundle-ref scan decided sentences
        # with _parse_yes_no's whole-sentence fallback and an
        # UNGUARDED "same person for all" shortcut — explicit declines
        # and uncertainty read as YES, and bundle fills auto-confirm
        # at 1.0. The verdict must come from the bundle-ref CLAUSE.
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "Not the same person for all of those roles — I want "
            "Maya Lee for healthcare and David Lee for finances "
            "instead."
        ) is False
        assert _bundle_verdict(
            "I would not use the same person for all of those roles, "
            "because that is too much responsibility for one person "
            "to carry."
        ) is False
        assert _bundle_verdict(
            "I am not sure the same person for all of those roles "
            "makes sense, let me think about who should handle the "
            "medical side first."
        ) is None
        assert _bundle_verdict(
            "Different people for each role please, not the same "
            "person for all of those positions at all, that would "
            "be far too much for one person."
        ) is False

    def test_details_sentences_with_bundle_words_defer(self):
        # Review round 3, C-A (other direction): a details sentence
        # that merely MENTIONS the roles is not a verdict either way.
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "For all of those positions, I do not have any "
            "additional information to add right now, and my "
            "healthcare agent's phone number is 512-555-0100."
        ) is None
        assert _bundle_verdict(
            "Yes, I do want organ donation noted, and for all of "
            "those roles my contact details are unchanged from what "
            "I gave you earlier in this session."
        ) is None

    def test_terse_details_sentences_defer(self):
        # Review round 4, C-A residual: TERSE details sentences —
        # length was the only thing saving the long forms. A
        # details-state predicate ("unchanged", "details", "address",
        # "phone") with no bundle action is not a verdict, in either
        # direction, in step 1 or in the short-head fallback.
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "Yes, my address for all of those roles is unchanged."
        ) is None
        assert _bundle_verdict(
            "For all of those roles, my details are unchanged, yes."
        ) is None
        assert _bundle_verdict(
            "For all of those roles, contact details are unchanged, "
            "yes please note that."
        ) is None
        assert _bundle_verdict(
            "No new details for all of those roles, everything is "
            "unchanged from before."
        ) is None
        assert _bundle_verdict(
            "Maybe yes, use him for all of those roles please."
        ) is None

    def test_action_verdict_with_details_word_still_counts(self):
        # The details exclusion must not eat a genuine verdict that
        # carries bundle ACTION wording.
        from app.domains.interview.service import _bundle_verdict

        assert _bundle_verdict(
            "Yes, use my address for all of those roles."
        ) is True


class TestGuardWiring:
    def test_post_turn_applies_the_guards(self):
        import inspect

        from app.domains.interview import service

        src = inspect.getsource(service.post_turn)
        assert "_grounding_drop_reason(" in src
        assert "_focus_completeness_proposals(" in src
        assert "focus_authoritative" in src
        assert "dropped_grounding_keys" in src
        # Fix iteration 1: G3's covered set must NOT inherit the
        # guard-dropped keys — a dropped MODEL copy for a focus field
        # is exactly the case G3 exists to capture deterministically
        # (A/B set 1, Sharma run 1 turns 19-20).
        assert "all_covered - dropped_copy_keys - dropped_grounding_keys" in src

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


class TestCopyGuardMultiTopicMessage:
    """Fix iteration 1 (A/B set 3, Pruned run 2 turn 8): the agents
    paragraph was proposed VERBATIM as the value for
    healthcare_agent_phone, healthcare_agent_limitations and
    poa_additional_powers. All three keys sat in the focus window,
    so the authoritative path's asked-exemption kept the copies —
    sound for a single-question focus, unsound under v7's merged
    focus: the user answered SOME window keys (the agent persons)
    and the message is demonstrably multi-topic (it grounded many
    proposals at once). A whole-message copy in a multi-topic turn
    (>=2 sibling drafts) is poison even for a window key."""

    _AGENTS_PARA = (
        "For the financial power of attorney, my agent is Maya Lee "
        "and the successor agent is David Lee. For healthcare, my "
        "agent is Maya Lee and David Lee is the successor healthcare "
        "agent."
    )
    _WINDOW = frozenset({
        "poa_agent_person", "successor_poa_agent_person",
        "healthcare_agent_person", "successor_healthcare_agent_person",
        "healthcare_agent_phone", "healthcare_agent_limitations",
        "poa_additional_powers",
    })

    def _is_copy(self, key, qtype, siblings):
        from app.domains.interview.service import _is_whole_message_copy

        return _is_whole_message_copy(
            question_type=qtype,
            value=self._AGENTS_PARA,
            user_message=self._AGENTS_PARA,
            last_assistant_text="Who should be your agents?",
            question_label="label",
            asked_keys=self._WINDOW,
            question_key=key,
            focus_authoritative=True,
            sibling_draft_count=siblings,
        )

    def test_multi_topic_copies_dropped_for_window_keys(self):
        assert self._is_copy("healthcare_agent_phone", "text", 6) is True
        assert self._is_copy("healthcare_agent_limitations", "notes", 6) is True
        assert self._is_copy("poa_additional_powers", "notes", 6) is True

    def test_multi_topic_boundary_two_siblings_drops(self):
        assert self._is_copy("healthcare_agent_phone", "text", 2) is True

    def test_single_topic_copy_for_window_key_still_kept(self):
        assert self._is_copy("healthcare_agent_phone", "text", 0) is False
        assert self._is_copy("healthcare_agent_phone", "text", 1) is False


class TestValueShapeGate:
    """Fix iteration 2 (A/B set 4, Sharma run 1): the model proposed
    boolean True for the TEXT keys retirement_beneficiaries_current
    and life_insurance_beneficiaries_current (answering the label's
    'Are these up to date?' half) — and True PERSISTED as the final
    intake answer, displacing the descriptive fact in the generated
    documents. Symmetrically it proposed the string 'No specific
    bequests' for a BOOLEAN key. A proposal whose value shape
    contradicts the pack type is never a faithful extraction; the
    deterministic fallbacks own the coerced forms."""

    def test_bool_for_text_notes_person_is_rejected(self):
        from app.domains.interview.service import _value_shape_ok

        for qtype in ("text", "notes", "person"):
            assert _value_shape_ok(qtype, True) is False
            assert _value_shape_ok(qtype, False) is False

    def test_string_for_boolean_is_rejected(self):
        from app.domains.interview.service import _value_shape_ok

        assert _value_shape_ok("boolean", "No specific bequests") is False
        assert _value_shape_ok("boolean", "true") is False

    def test_matching_shapes_pass(self):
        from app.domains.interview.service import _value_shape_ok

        assert _value_shape_ok("boolean", True) is True
        assert _value_shape_ok("boolean", False) is True
        assert _value_shape_ok("text", "512-555-0100") is True
        assert _value_shape_ok("notes", "No limitations") is True
        assert _value_shape_ok("person", "Maya Lee") is True

    def test_ungated_types_untouched(self):
        from app.domains.interview.service import _value_shape_ok

        # Selects carry pack option codes / lists — not observed as
        # poison; the gate does not invent rules for them.
        assert _value_shape_ok("single_select", "married") is True
        assert _value_shape_ok("multi_select", ["bank_accounts"]) is True
        assert _value_shape_ok("", True) is True


class TestCompletenessTextGate:
    """Fix iteration 2 (A/B set 4): G3's scoped text capture takes
    the WHOLE message as the value. Under v6's single-key focus
    that was the sanctioned lapse-catcher (A/B run 4: zero proposals
    stored). Under v7's merged focus it stamped one paragraph into
    EVERY uncovered notes key of the focus (set-4 Sharma turns 7/9:
    guardian_reason, living_will_directives, organ_donation_wishes,
    poa_additional_powers all = the agents paragraph at 0.80). The
    capture is now gated to lapse turns: when the turn already
    produced >=2 proposals, the message is multi-topic and bundled
    extraction is the model's job — the fallback abstains."""

    _AGENTS_PARA = (
        "For the financial power of attorney, my agent is Priya Sharma "
        "and the successor agent is Amit Sharma. For healthcare, my "
        "agent is Priya Sharma and Amit Sharma is the successor "
        "healthcare agent."
    )
    _GUARDIAN_PARA = (
        "My brother Amit Sharma should be the guardian for our "
        "children — he is trusted, he is close to the children, and "
        "he shares our values. If he can't serve, Neha Kapoor is the "
        "backup guardian."
    )
    _MULTI_ASK = (
        "Next, a few things: 1) Who should care for your minor "
        "children if neither parent can? 2) Why is this person a good "
        "fit? (optional) 3) Do you have wishes about life-prolonging "
        "treatment? 4) Do you wish to donate organs, tissue, or eyes "
        "after death? 5) Are there any specific financial powers you "
        "want to grant or withhold? (optional)"
    )

    def _run(self, focus, message, prior):
        from app.domains.interview.service import (
            _focus_completeness_proposals,
        )
        from app.domains.legal_content.pack_loader import get_pack

        sets = get_pack("US-VA", "2.0.0").question_sets
        types = {}
        from app.domains.intake import question_sets as qs

        for _, _, q in qs.iter_questions(sets):
            types[str(q["key"])] = str(q.get("type") or "")
        return _focus_completeness_proposals(
            focus_keys=focus,
            covered_keys=set(),
            question_types=types,
            user_message=message,
            assistant_reply=self._MULTI_ASK,
            question_sets=sets,
            answered_keys=set(),
            prior_proposal_count=prior,
        )

    def test_multi_topic_turn_stamps_nothing(self):
        out = self._run(
            [
                "guardian_reason",
                "living_will_directives",
                "organ_donation_wishes",
                "poa_additional_powers",
            ],
            self._AGENTS_PARA,
            prior=4,
        )
        assert out == []

    def test_lapse_turn_capture_kept(self):
        out = self._run(["guardian_reason"], self._GUARDIAN_PARA, prior=0)
        assert [p["question_key"] for p in out] == ["guardian_reason"]

    def test_boundary_one_prior_keeps_two_priors_abstain(self):
        out = self._run(["guardian_reason"], self._GUARDIAN_PARA, prior=1)
        assert [p["question_key"] for p in out] == ["guardian_reason"]
        out = self._run(["guardian_reason"], self._GUARDIAN_PARA, prior=2)
        assert out == []
