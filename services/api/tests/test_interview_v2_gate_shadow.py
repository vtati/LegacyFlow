"""Lever batch (2026-10-25 mandate): gate-shadow extraction + bleed fixes.

Failures encoded here, all from the recorded set-6 A/B runs:

- GATE SHADOW: a paragraph that answers a boolean gate AND its
  conditional fields in one breath (trust: "Yes, I want a revocable
  living trust. Amit Sharma is the successor trustee. Put everything
  into the trust...") extracted only the gate, because the conditional
  keys are applies_if-gated and invisible to the prompt until the gate
  flips — the details then needed verbatim re-sends (Sharma trust took
  3 sends; the assets beneficiaries_current keys, gated on
  has_retirement_accounts / has_life_insurance, took until turn 10).
- PHONE BLEED (Pruned turns 8, both set-6 runs): the deterministic
  text fallback stamped the whole trust sentence ("No, I don't want a
  revocable living trust.") as healthcare_agent_phone, because the
  previous reply contained the phone label verbatim and the fallback
  never checked the message itself.
- POA ANTICIPATION (Pruned turn 5, both set-6 runs): the person
  fallback licensed poa_agent_person from two GENERIC label words
  ("should", "handle") inside the executor question, after the model's
  own executor proposals covered the real candidates.
"""

from __future__ import annotations

from app.domains.ai.provider import FieldProposalDraft
from app.domains.interview import prompts, service
from app.domains.interview import service as svc
from app.domains.legal_content.pack_loader import get_pack


def _pack():
    return get_pack("US-VA")


def _q(key, label, typ, applies_if=None, required=True):
    q = {"key": key, "label": label, "type": typ, "required": required,
         "cluster": "x"}
    if applies_if:
        q["applies_if"] = applies_if
    return q


# ---------------------------------------------------------------------------
# Gate shadow: which conditional questions ride along with a focus gate
# ---------------------------------------------------------------------------

class TestShadowQuestions:
    def test_trust_gate_shadows_its_four_dependents(self):
        pack = _pack()
        focus = [{"key": "wants_revocable_trust", "label": "Do you want a revocable living trust to hold your assets and avoid probate?", "type": "boolean"}]
        shadows = svc.shadow_questions_for(pack.question_sets, {}, focus)
        keys = {s["key"] for s in shadows}
        assert keys == {
            "successor_trustee_person",
            "trust_initial_property",
            "trust_distribution_age",
            "trust_marital_share",
        }
        assert all(s["gate_key"] == "wants_revocable_trust" for s in shadows)

    def test_assets_gates_shadow_the_beneficiaries(self):
        pack = _pack()
        focus = [
            {"key": "has_retirement_accounts", "label": "Do you have retirement accounts (401k, IRA, or similar)?", "type": "boolean"},
            {"key": "has_life_insurance", "label": "Do you have a life insurance policy?", "type": "boolean"},
        ]
        shadows = svc.shadow_questions_for(pack.question_sets, {}, focus)
        keys = {s["key"] for s in shadows}
        assert keys == {
            "retirement_beneficiaries_current",
            "life_insurance_beneficiaries_current",
        }

    def test_no_shadow_when_gate_already_true(self):
        pack = _pack()
        focus = [{"key": "wants_revocable_trust", "label": "x", "type": "boolean"}]
        shadows = svc.shadow_questions_for(
            pack.question_sets, {"wants_revocable_trust": True}, focus
        )
        assert shadows == []

    def test_no_shadow_when_gate_pruned_false(self):
        pack = _pack()
        focus = [{"key": "wants_revocable_trust", "label": "x", "type": "boolean"}]
        shadows = svc.shadow_questions_for(
            pack.question_sets, {"wants_revocable_trust": False}, focus
        )
        assert shadows == []

    def test_no_shadow_for_non_gate_focus(self):
        pack = _pack()
        focus = [{"key": "primary_executor_person", "label": "x", "type": "person"}]
        assert svc.shadow_questions_for(pack.question_sets, {}, focus) == []

    def test_shadow_excludes_focus_and_covered_keys(self):
        pack = _pack()
        focus = [
            {"key": "wants_revocable_trust", "label": "x", "type": "boolean"},
            {"key": "successor_trustee_person", "label": "y", "type": "person"},
        ]
        shadows = svc.shadow_questions_for(
            pack.question_sets, {}, focus,
            covered_keys={"trust_initial_property"},
        )
        keys = {s["key"] for s in shadows}
        assert "successor_trustee_person" not in keys
        assert "trust_initial_property" not in keys
        assert keys == {"trust_distribution_age", "trust_marital_share"}


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

class TestShadowPrompt:
    def _gap(self):
        return {
            "cluster": "trust",
            "clusters": ["trust"],
            "questions": [
                {"key": "wants_revocable_trust",
                 "label": "Do you want a revocable living trust to hold your assets and avoid probate?",
                 "type": "boolean"},
            ],
        }

    def test_shadow_section_rendered(self):
        shadows = [
            {"key": "successor_trustee_person",
             "label": "Who should take over managing the trust if you become unable to do so, or after your death?",
             "type": "person", "gate_key": "wants_revocable_trust"},
        ]
        text = prompts.build_system_prompt(
            pack_id="us-va", jurisdiction_code="US-VA",
            missing_required_keys=["wants_revocable_trust"],
            answered_required=30, total_required=41,
            answered_keys=[], cluster_gap=self._gap(),
            shadow_questions=shadows,
        )
        assert "successor_trustee_person" in text
        assert "answers yes" in text.lower() or "answers YES" in text

    def test_no_shadow_param_is_byte_identical(self):
        kw = dict(
            pack_id="us-va", jurisdiction_code="US-VA",
            missing_required_keys=["wants_revocable_trust"],
            answered_required=30, total_required=41,
            answered_keys=[], cluster_gap=self._gap(),
        )
        assert prompts.build_system_prompt(**kw) == prompts.build_system_prompt(
            **kw, shadow_questions=None
        )
        assert prompts.build_system_prompt(**kw) == prompts.build_system_prompt(
            **kw, shadow_questions=[]
        )

    def test_shadow_line_names_its_gate(self):
        # Review M-1: with two gates in one focus, each shadow line
        # names the gate that unlocks it.
        shadows = [
            {"key": "successor_trustee_person",
             "label": "Who should take over managing the trust?",
             "type": "person", "gate_key": "wants_revocable_trust",
             "gate_label": "Do you want a revocable living trust?"},
        ]
        text = prompts.build_system_prompt(
            pack_id="us-va", jurisdiction_code="US-VA",
            missing_required_keys=["wants_revocable_trust"],
            answered_required=30, total_required=41,
            answered_keys=[], cluster_gap=self._gap(),
            shadow_questions=shadows,
        )
        assert "unlocked by a yes to: Do you want a revocable living trust?" in text

    def test_next_gap_shadows_render_on_verdict_turns(self):
        # Review C-2: the verdict branch renders the then-gap questions
        # as "ask next" — its gates' shadow fields must render too.
        offer = {
            "anchor_key": "primary_executor_person",
            "anchor_name": "Priya Sharma",
            "fill_keys": ["primary_executor_address"],
            "fill_labels": ["Executor address"],
            "question_text": "Quick question: also use your address?",
            "presented_before": True,
        }
        next_gap = {
            "cluster": "trust",
            "clusters": ["trust"],
            "questions": [
                {"key": "wants_revocable_trust",
                 "label": "Do you want a revocable living trust to hold your assets and avoid probate?",
                 "type": "boolean"},
            ],
        }
        shadows = [
            {"key": "successor_trustee_person",
             "label": "Who should take over managing the trust?",
             "type": "person", "gate_key": "wants_revocable_trust",
             "gate_label": "Do you want a revocable living trust?"},
        ]
        kw = dict(
            pack_id="us-va", jurisdiction_code="US-VA",
            missing_required_keys=["wants_revocable_trust"],
            answered_required=30, total_required=41,
            answered_keys=[], bundle_offers=[offer], next_gap=next_gap,
        )
        text = prompts.build_system_prompt(
            **kw, next_gap_shadow_questions=shadows
        )
        assert "successor_trustee_person" in text
        assert "GATE FIELDS" in text
        # And without the param the verdict prompt is unchanged.
        assert prompts.build_system_prompt(**kw) == prompts.build_system_prompt(
            **kw, next_gap_shadow_questions=None
        )


# ---------------------------------------------------------------------------
# Gate-truth licensing: a dependent proposal survives only if its gate
# is true in the answers or in this turn's accepted proposals.
# ---------------------------------------------------------------------------

class TestGateTruthDropModes:
    """Review round 2 (I-1/I-2): the dependents map must cover gates
    already answered False (their dependents are pruned, not pending)
    and must license opt-out (`!= false`) sections unless the gate is
    explicitly false."""

    def test_pruned_dependent_of_false_gate_is_dropped(self):
        pack = _pack()
        deps = svc.gate_dependents_map(
            pack.question_sets, {"wants_revocable_trust": False}
        )
        assert "successor_trustee_person" in deps
        draft = FieldProposalDraft(
            question_key="successor_trustee_person", value="Amit Sharma",
            confidence=0.95, source_snippet="x",
        )
        kept = svc._drop_unlicensed_gate_proposals(
            [draft], {"wants_revocable_trust": False}, deps
        )
        assert kept == []

    def test_opt_out_dependent_kept_while_gate_unanswered(self):
        pack = _pack()
        deps = svc.gate_dependents_map(pack.question_sets, {})
        draft = FieldProposalDraft(
            question_key="poa_agent_person", value="Maya Lee",
            confidence=0.95, source_snippet="x",
        )
        kept = svc._drop_unlicensed_gate_proposals([draft], {}, deps)
        assert kept == [draft]

    def test_opt_out_dependent_dropped_when_gate_false(self):
        pack = _pack()
        deps = svc.gate_dependents_map(pack.question_sets, {})
        draft = FieldProposalDraft(
            question_key="poa_agent_person", value="Maya Lee",
            confidence=0.95, source_snippet="x",
        )
        kept = svc._drop_unlicensed_gate_proposals(
            [draft], {"wants_poa_document": False}, deps
        )
        assert kept == []

    def test_current_turn_draft_outranks_stale_base_value(self):
        # Review round 3 (I-1): the licensing base (recorded answers
        # merged with PENDING proposal values) must not outrank what
        # the user says THIS turn — in either direction.
        deps = {"successor_trustee_person": "wants_revocable_trust"}
        drafts = [
            FieldProposalDraft(question_key="wants_revocable_trust",
                               value=True, confidence=0.9, source_snippet="x"),
            FieldProposalDraft(question_key="successor_trustee_person",
                               value="Amit Sharma", confidence=0.9, source_snippet="x"),
        ]
        kept = svc._drop_unlicensed_gate_proposals(
            drafts, {"wants_revocable_trust": False}, deps
        )
        assert kept == drafts
        deps_out = {"poa_agent_person": (("wants_poa_document", "opt_out"),)}
        drafts_out = [
            FieldProposalDraft(question_key="wants_poa_document",
                               value=False, confidence=0.9, source_snippet="x"),
            FieldProposalDraft(question_key="poa_agent_person",
                               value="Maya Lee", confidence=0.9, source_snippet="x"),
        ]
        kept_out = svc._drop_unlicensed_gate_proposals(
            drafts_out, {"wants_poa_document": True}, deps_out
        )
        assert [d.question_key for d in kept_out] == ["wants_poa_document"]

    def test_first_gate_draft_wins_licensing(self):
        # Storage keeps the FIRST draft per key; licensing truth must
        # agree, or a dependent can be licensed by a gate value that
        # is never stored (review M-1).
        deps = {"successor_trustee_person": "wants_revocable_trust"}
        drafts = [
            FieldProposalDraft(question_key="wants_revocable_trust",
                               value=False, confidence=0.9, source_snippet="x"),
            FieldProposalDraft(question_key="successor_trustee_person",
                               value="Amit Sharma", confidence=0.9, source_snippet="x"),
            FieldProposalDraft(question_key="wants_revocable_trust",
                               value=True, confidence=0.9, source_snippet="x"),
        ]
        kept = svc._drop_unlicensed_gate_proposals(drafts, {}, deps)
        assert [d.question_key for d in kept] == [
            "wants_revocable_trust", "wants_revocable_trust",
        ]


class TestGateTruthDrop:
    DEPS = {"successor_trustee_person": "wants_revocable_trust"}

    def _draft(self, key, value):
        return FieldProposalDraft(
            question_key=key, value=value, confidence=0.95, source_snippet="x"
        )

    def test_dependent_kept_when_gate_proposed_true_same_turn(self):
        drafts = [
            self._draft("wants_revocable_trust", True),
            self._draft("successor_trustee_person", "Amit Sharma"),
        ]
        kept = svc._drop_unlicensed_gate_proposals(drafts, {}, self.DEPS)
        assert [d.question_key for d in kept] == [
            "wants_revocable_trust", "successor_trustee_person",
        ]

    def test_dependent_dropped_when_gate_proposed_false(self):
        drafts = [
            self._draft("wants_revocable_trust", False),
            self._draft("successor_trustee_person", "Amit Sharma"),
        ]
        kept = svc._drop_unlicensed_gate_proposals(drafts, {}, self.DEPS)
        assert [d.question_key for d in kept] == ["wants_revocable_trust"]

    def test_dependent_dropped_when_gate_never_answered(self):
        drafts = [self._draft("successor_trustee_person", "Amit Sharma")]
        kept = svc._drop_unlicensed_gate_proposals(drafts, {}, self.DEPS)
        assert kept == []

    def test_dependent_kept_when_gate_already_true(self):
        drafts = [self._draft("successor_trustee_person", "Amit Sharma")]
        kept = svc._drop_unlicensed_gate_proposals(
            drafts, {"wants_revocable_trust": True}, self.DEPS
        )
        assert len(kept) == 1

    def test_non_dependents_untouched(self):
        drafts = [self._draft("residuary_beneficiaries", "Everything to Maya")]
        kept = svc._drop_unlicensed_gate_proposals(drafts, {}, self.DEPS)
        assert len(kept) == 1


# ---------------------------------------------------------------------------
# C1: deterministic text fallback — phone shape + message-topic gate
# ---------------------------------------------------------------------------

def _fallback_sets():
    return {
        "healthcare": {
            "sections": [{
                "id": "hc",
                "questions": [
                    _q("healthcare_agent_person",
                       "Who should make healthcare decisions for you if you become unable to make them yourself?",
                       "person", required=False),
                    _q("healthcare_agent_phone",
                       "What is the best phone number to reach your healthcare agent?",
                       "text", required=False),
                    _q("residuary_beneficiaries",
                       "Who should receive everything else — the rest of your estate after debts, expenses, and specific gifts are handled?",
                       "text", required=False),
                ],
            }],
        }
    }


_PHONE_ASK = (
    "Thanks — I've recorded 2 values. Next, a few things: "
    "1) Who should make healthcare decisions for you if you become unable "
    "to make them yourself? 2) What is the best phone number to reach "
    "your healthcare agent?"
)


class TestTextFallbackBleed:
    def test_trust_sentence_is_not_a_phone_number(self):
        out = svc._deterministic_text_proposal(
            user_message="No, I don't want a revocable living trust.",
            assistant_reply=_PHONE_ASK,
            question_sets=_fallback_sets(),
            existing_proposal_keys=set(),
        )
        assert out is None

    def test_phone_answer_extracts_the_number_not_the_sentence(self):
        out = svc._deterministic_text_proposal(
            user_message="You can reach her at 804-555-0132.",
            assistant_reply=_PHONE_ASK,
            question_sets=_fallback_sets(),
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "healthcare_agent_phone"
        assert out["value"] == "804-555-0132"

    def test_off_topic_message_not_stamped_onto_residuary(self):
        out = svc._deterministic_text_proposal(
            user_message="No, I don't want a revocable living trust.",
            assistant_reply=(
                "Next: Who should receive everything else — the rest of "
                "your estate after debts, expenses, and specific gifts "
                "are handled?"
            ),
            question_sets=_fallback_sets(),
            existing_proposal_keys=set(),
        )
        assert out is None

    def test_on_topic_residuary_still_captured(self):
        out = svc._deterministic_text_proposal(
            user_message="Everything to my sister Maya Lee.",
            assistant_reply=(
                "Next: Who should receive everything else — the rest of "
                "your estate after debts, expenses, and specific gifts "
                "are handled?"
            ),
            question_sets=_fallback_sets(),
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "residuary_beneficiaries"

    def test_short_clauses_are_not_bare_values(self):
        # Review round 3 (I-2): a short CLAUSE is not a bare value —
        # the exemption must not admit verb-bearing off-topic replies
        # (short forms of the recorded trust-decline bleed).
        sets = {
            "executors": {
                "sections": [{
                    "id": "ex",
                    "questions": [
                        _q("primary_executor_address",
                           "What is the city and state where your executor lives?",
                           "text", required=False),
                        _q("residuary_beneficiaries",
                           "Who should receive everything else in your estate?",
                           "text", required=False),
                    ],
                }],
            }
        }
        for message, key in [
            ("Everything goes to charity", "primary_executor_address"),
            ("Skip the trust entirely", "residuary_beneficiaries"),
            ("We declined the trust", "residuary_beneficiaries"),
            ("I changed my mind", "residuary_beneficiaries"),
        ]:
            assert svc._message_names_topic(key, message, sets) is False, (
                message,
                key,
            )

    def test_bare_value_answers_still_captured(self):
        # Review round 2 (C-1): canonical bare-value answers carry
        # none of the label's words — a short, negation-free reply is
        # a value, not the bleed shape (a full off-topic clause).
        sets = {
            "executors": {
                "sections": [{
                    "id": "ex",
                    "questions": [
                        _q("primary_executor_address",
                           "What is the city and state where your executor lives?",
                           "text", required=False),
                        _q("minor_children_details",
                           "Please list each minor child by name and age.",
                           "text", required=False),
                    ],
                }],
            }
        }
        out = svc._deterministic_text_proposal(
            user_message="Richmond, Virginia",
            assistant_reply="What is the city and state where your executor lives?",
            question_sets=sets,
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "primary_executor_address"
        out = svc._deterministic_text_proposal(
            user_message="Reyansh, 7 and Haasini, 13",
            assistant_reply="Please list each minor child by name and age.",
            question_sets=sets,
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "minor_children_details"

    def test_terse_residuary_restatement_still_captured(self):
        # Review I-1: a terse restatement in the label's own words is
        # a genuine answer even when it carries no long distinctive
        # word ("the rest" is the label's phrasing, in a short reply).
        out = svc._deterministic_text_proposal(
            user_message="The rest goes to my brother.",
            assistant_reply=(
                "Next: Who should receive everything else — the rest of "
                "your estate after debts, expenses, and specific gifts "
                "are handled?"
            ),
            question_sets=_fallback_sets(),
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "residuary_beneficiaries"


# ---------------------------------------------------------------------------
# C3: person fallback — generic label words must not license a role
# ---------------------------------------------------------------------------

class TestPersonFallbackAnticipation:
    def test_executor_question_does_not_license_poa_agent(self):
        pack = _pack()
        out = svc._deterministic_person_proposal(
            user_message=(
                "My sister Maya Lee should be the executor of my will. "
                "If she can't serve, my father David Lee is the backup "
                "executor."
            ),
            assistant_reply=(
                "Thanks — I've recorded 2 values. Next, a few things: "
                "1) Who should serve as the executor of your will — the "
                "person who will carry out its instructions and handle "
                "your estate? 2) Who should serve as executor if your "
                "first choice cannot?"
            ),
            question_sets=pack.question_sets,
            existing_proposal_keys={
                "primary_executor_person", "backup_executor_person",
            },
        )
        assert out is None

    def test_successor_label_keeps_its_generic_word_licensing(self):
        # The successor labels are generic by design ("Who should
        # serve if your first choice cannot?"); their discipline is
        # the qualification split, not distinctive words. Killing this
        # licensing broke the sanctioned bare-name successor capture
        # (guards suite regression caught in review of this batch).
        pack = _pack()
        out = svc._deterministic_person_proposal(
            user_message="Amit Sharma.",
            assistant_reply="And who should serve if your first choice cannot?",
            question_sets=pack.question_sets,
            existing_proposal_keys={
                "primary_executor_person", "backup_executor_person",
                "poa_agent_person", "primary_guardian_person",
                "backup_guardian_person", "healthcare_agent_person",
                "successor_healthcare_agent_person",
                "successor_trustee_person",
            },
        )
        assert out is not None
        assert out["question_key"] == "successor_poa_agent_person"

    def test_poa_question_still_captures_bare_name(self):
        pack = _pack()
        out = svc._deterministic_person_proposal(
            user_message="Maya Lee.",
            assistant_reply=(
                "Next: Who should handle your financial affairs — bank "
                "accounts, property, taxes — if you become unable to do "
                "so yourself?"
            ),
            question_sets=pack.question_sets,
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "poa_agent_person"
        assert out["value"] == "Maya Lee"


# ---------------------------------------------------------------------------
# C4/C5 helpers
# ---------------------------------------------------------------------------

class TestCopyGuardAskedKeys:
    def test_v2_window_includes_current_focus(self):
        asked = svc._copy_guard_asked_keys(
            v2_on=True,
            window_focus_keys=frozenset({"a"}),
            prev_focus_keys=frozenset({"z"}),
            focus_keys=["b", "c"],
        )
        assert asked == frozenset({"a", "b", "c"})

    def test_v1_uses_prev_focus_only(self):
        asked = svc._copy_guard_asked_keys(
            v2_on=False,
            window_focus_keys=frozenset({"a"}),
            prev_focus_keys=frozenset({"z"}),
            focus_keys=["b"],
        )
        assert asked == frozenset({"z"})


class TestDuplicateAnswer:
    def test_same_value_is_duplicate(self):
        assert svc._is_duplicate_answer(
            "primary_executor_person", "Maya Lee",
            {"primary_executor_person": "Maya Lee"},
        )

    def test_different_value_is_not_duplicate(self):
        assert not svc._is_duplicate_answer(
            "primary_executor_person", "David Lee",
            {"primary_executor_person": "Maya Lee"},
        )

    def test_unanswered_key_is_not_duplicate(self):
        assert not svc._is_duplicate_answer(
            "primary_executor_person", "Maya Lee", {},
        )

    def test_person_fields_compare_exactly(self):
        # Review round 3 (M-5): legal names are casing-sensitive — a
        # casing-only change is a correction signal, not a duplicate.
        assert svc._is_duplicate_answer(
            "primary_executor_person", "Maya Lee",
            {"primary_executor_person": "Maya Lee"}, "person",
        )
        assert not svc._is_duplicate_answer(
            "primary_executor_person", "maya lee",
            {"primary_executor_person": "Maya Lee"}, "person",
        )
        # Non-person fields keep the canonical comparison.
        assert svc._is_duplicate_answer(
            "testator_city", "richmond",
            {"testator_city": "Richmond"}, "text",
        )


class TestPhoneValueGate:
    def test_phone_shaped_value_extracted(self):
        assert svc._extract_phone_value("Call 804-555-0132 anytime") == "804-555-0132"

    def test_no_phone_shape(self):
        assert svc._extract_phone_value("No, I don't want a trust.") is None
        assert svc._extract_phone_value("age 25") is None

    def test_iso_date_is_not_a_phone_number(self):
        # Review M-3: a date-shaped span must not become the value.
        assert svc._extract_phone_value("Born 2026-10-25") is None
        assert (
            svc._extract_phone_value("On 2026-10-25, call 804-555-0132")
            == "804-555-0132"
        )

    def test_long_contiguous_runs_never_truncate_into_a_phone(self):
        # Review round 3 (C-1): the strict alternative must not match
        # the first 10 digits of a longer run — a card number is not
        # a phone, and a legitimate 11-digit number must survive whole.
        assert svc._extract_phone_value("4111111111111111") is None
        assert svc._extract_phone_value("123456789012345") is None
        assert svc._extract_phone_value("18045550132") == "18045550132"
        # The leading "+" is decoration (review round 3, M-6, accepted):
        # the dialable core survives whole.
        assert svc._extract_phone_value("+1 (804) 555-0132") == "1 (804) 555-0132"

    def test_zip_plus_four_is_not_a_phone_number(self):
        # Review round 2 (I-3): ZIP+4 and bare digit runs of
        # non-phone lengths must not extract as phones.
        assert svc._extract_phone_value("ZIP 23220-1234") is None
        assert svc._extract_phone_value("account 12345678") is None
        assert (
            svc._extract_phone_value("Call 804-555-0132, ZIP 23220-1234")
            == "804-555-0132"
        )
        assert svc._extract_phone_value("dial 5550132") == "5550132"


class TestNumericTextCoercion:
    """The model proposes trust_distribution_age as the int 25 — the
    faithful value in JSON-native form. The shape gate discards it and
    the field costs a re-ask turn. Numeric scalars coerce to strings
    for text/notes keys; booleans (a polarity, not content) never do.
    """

    def test_int_for_text_coerces(self):
        assert svc._numeric_text_value("text", 25) == "25"

    def test_float_for_notes_coerces(self):
        assert svc._numeric_text_value("notes", 2.5) == "2.5"

    def test_bool_never_coerces(self):
        assert svc._numeric_text_value("text", True) is None
        assert svc._numeric_text_value("notes", False) is None

    def test_string_needs_no_coercion(self):
        assert svc._numeric_text_value("text", "25") is None

    def test_other_types_untouched(self):
        assert svc._numeric_text_value("boolean", 1) is None
        assert svc._numeric_text_value("person", 25) is None
        assert svc._numeric_text_value("select", 25) is None
