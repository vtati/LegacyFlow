"""Batch 2 (set-7 failure anatomy): Lever E prompt inventory, person
fallback role parity, and multi-ask topicality for the text fallback.

Set 7 (9676782) failed the bar on turns and wrong-source proposals.
Every wrong-source proposal traced to the deterministic fallbacks at
confidence 0.80:

- the executor paragraph minted backup_guardian_person = the
  executor's name (person fallback licensed from the previous
  reply's guardian wording, with no role discipline on its output —
  the G3 scoped path applies _person_role_mismatch, the unscoped
  path did not);
- the trust paragraph was stamped whole onto guardian_reason in a
  multi-ask regime (the key's label has no distinctive words, so the
  topic gate passed it unconditionally);
- off-focus paragraphs extracted ~zero because the prompt only ever
  shows the focus fields' labels — Lever E renders the remaining
  applicable fields as a reference inventory (prompt v9).
"""

from __future__ import annotations

from app.domains.interview import prompts, service as svc


def _q(key, label, qtype="text", required=True):
    return {"key": key, "label": label, "type": qtype, "required": required}


GUARDIAN_SETS = {
    "guardians": {
        "sections": [{
            "id": "g",
            "questions": [
                _q("primary_guardian_person",
                   "Who should care for your minor children if neither parent can?",
                   "person"),
                _q("backup_guardian_person",
                   "Who should serve if your first choice cannot?", "person"),
                _q("guardian_reason",
                   "Why is this person a good fit?", "text", required=False),
            ],
        }],
    },
    "executors": {
        "sections": [{
            "id": "e",
            "questions": [
                _q("primary_executor_person",
                   "Who should serve as the executor of your will?", "person"),
                _q("residuary_beneficiaries",
                   "Who should receive everything else in your estate?", "text"),
            ],
        }],
    },
}

EXECUTOR_PARAGRAPH = (
    "My wife Priya Sharma should be the executor of my will. "
    "If she cannot serve, Amit Sharma should serve as the backup executor."
)

TRUST_PARAGRAPH = (
    "Yes, I want a revocable living trust. Amit Sharma is the successor "
    "trustee. Put everything — all assets — into the trust. The children "
    "should receive their inheritance outright at age 25. Everything "
    "should go to my spouse outright as the marital share."
)


class TestPersonFallbackRoleParity:
    def test_executor_paragraph_cannot_mint_backup_guardian(self):
        # Set 7, Sharma #2 turn 6: the previous reply asked for the
        # guardian's address (guardian wording), the user answered
        # with the executor paragraph, and the fallback filed the
        # executor as backup guardian.
        out = svc._deterministic_person_proposal(
            user_message=EXECUTOR_PARAGRAPH,
            assistant_reply=(
                "Thanks — I've recorded 2 values. For Amit Sharma, what "
                "is the best address to list for him as guardian?"
            ),
            question_sets=GUARDIAN_SETS,
            existing_proposal_keys={"primary_guardian_person"},
        )
        assert out is None

    def test_guardian_paragraph_still_captures_primary_guardian(self):
        # The sanctioned capture: the message speaks the guardian role
        # and names the nominee for it — parity must not block it.
        out = svc._deterministic_person_proposal(
            user_message=(
                "My brother Amit Sharma should be the guardian for our "
                "children. If Amit cannot serve, Neha Kapoor should be "
                "the backup guardian."
            ),
            assistant_reply=(
                "Next, a few things: 1) Who should care for your minor "
                "children if neither parent can? 2) Who should serve if "
                "your first choice cannot?"
            ),
            question_sets=GUARDIAN_SETS,
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "primary_guardian_person"
        assert out["value"] == "Amit Sharma"


class TestRoleWordLicensing:
    BEQUESTS = (
        "No, I don't have any specific bequests. Everything else — "
        "the residue of my estate — should go to my wife, Priya Sharma."
    )

    def test_residuary_sentence_cannot_mint_the_executor(self):
        # Set 7, both Sharma runs, turn 4: "should" + "estate" (label
        # scenery) licensed primary_executor_person from the message
        # channel. A primary key now needs one of its own ROLE words
        # among the matched label words.
        out = svc._deterministic_person_proposal(
            user_message=self.BEQUESTS,
            assistant_reply=(
                "Next, a few things: 1) Are there specific items or "
                "amounts you want to leave to particular people or "
                "charities? 2) Who should receive everything else — the "
                "rest of your estate after debts, expenses, and specific "
                "gifts are handled?"
            ),
            question_sets=GUARDIAN_SETS,
            existing_proposal_keys=set(),
        )
        assert out is None

    def test_role_free_successor_phrasing_still_captures(self):
        # Review I-1: successor answers that speak no role family are
        # successor licensing's whole purpose — the inside role check
        # must not kill them (only a different role NOUN blocks).
        scoped = {
            "trust": {
                "sections": [{
                    "id": "t",
                    "questions": [
                        _q("successor_trustee_person",
                           "Who should take over managing the trust if you "
                           "become unable to do so, or after your death?",
                           "person"),
                    ],
                }],
            }
        }
        out = svc._deterministic_person_proposal(
            user_message=(
                "Amit Sharma should be the one to step in and take over "
                "if I cannot serve any longer for health reasons, and "
                "he should manage all of it carefully from then on."
            ),
            assistant_reply=(
                "Who should take over managing the trust if you become "
                "unable to do so, or after your death?"
            ),
            question_sets=scoped,
            existing_proposal_keys=set(),
        )
        assert out is not None
        assert out["question_key"] == "successor_trustee_person"
        assert out["value"] == "Amit Sharma"


class TestAddressAnnotation:
    KW = dict(
        pack_id="us-va", jurisdiction_code="US-VA",
        missing_required_keys=["primary_guardian_address"],
        answered_required=24, total_required=41, answered_keys=[],
    )

    def _gap(self, key, label, sweep=False):
        gap = {
            "cluster": "people", "clusters": ["people"],
            "questions": [{"key": key, "label": label}],
        }
        if sweep:
            gap["sweep"] = True
        return gap

    def test_role_address_line_is_annotated(self):
        text = prompts.build_system_prompt(
            **self.KW,
            cluster_gap=self._gap(
                "primary_guardian_address",
                "What is the city and state where your guardian lives?",
            ),
        )
        assert "do NOT ask this directly" in text

    def test_testator_address_is_never_annotated(self):
        text = prompts.build_system_prompt(
            **self.KW,
            cluster_gap=self._gap(
                "testator_street_address", "What is your street address?",
            ),
        )
        assert "do NOT ask this directly" not in text

    def test_sweep_turns_keep_direct_asks(self):
        text = prompts.build_system_prompt(
            **self.KW,
            cluster_gap=self._gap(
                "primary_guardian_address",
                "What is the city and state where your guardian lives?",
                sweep=True,
            ),
        )
        assert "do NOT ask this directly" not in text


class TestMultiAskTopicality:
    MULTI_ASK_REPLY = (
        "Next, a few things: 1) Why is this person a good fit? "
        "2) Who should serve if your first choice cannot? "
        "3) What is the best phone number to reach your healthcare agent? "
        "4) Do you wish to donate organs after death?"
    )

    def test_no_distinctive_key_not_stamped_in_multi_ask_regime(self):
        # Set 7, both Sharma runs, turn 8: guardian_reason's label has
        # no distinctive words; the trust paragraph was stamped whole.
        assert (
            svc._message_names_topic(
                "guardian_reason", TRUST_PARAGRAPH, GUARDIAN_SETS,
                multi_ask=True,
            )
            is False
        )

    def test_single_ask_regime_unchanged(self):
        # One question, one answer: the message IS the answer even
        # when the label carries no distinctive words.
        assert (
            svc._message_names_topic(
                "guardian_reason", TRUST_PARAGRAPH, GUARDIAN_SETS,
                multi_ask=False,
            )
            is True
        )

    def test_text_fallback_abstains_on_multi_ask_stamp(self):
        out = svc._deterministic_text_proposal(
            user_message=TRUST_PARAGRAPH,
            assistant_reply=self.MULTI_ASK_REPLY,
            question_sets=GUARDIAN_SETS,
            existing_proposal_keys=set(),
        )
        assert out is None


class TestOtherFieldsInventory:
    KW = dict(
        pack_id="us-va", jurisdiction_code="US-VA",
        missing_required_keys=["primary_guardian_person"],
        answered_required=24, total_required=41,
        answered_keys=[],
        cluster_gap={
            "cluster": "guardians",
            "clusters": ["guardians"],
            "questions": [
                {"key": "primary_guardian_person",
                 "label": "Who should care for your minor children?"},
            ],
        },
    )

    def test_other_fields_block_renders(self):
        text = prompts.build_system_prompt(
            **self.KW,
            other_questions=[
                {"key": "wants_revocable_trust",
                 "label": "Do you want a revocable living trust?"},
            ],
        )
        assert "OTHER FIELDS" in text
        assert "wants_revocable_trust" in text

    def test_no_other_fields_is_byte_identical(self):
        assert prompts.build_system_prompt(
            **self.KW
        ) == prompts.build_system_prompt(**self.KW, other_questions=[])
