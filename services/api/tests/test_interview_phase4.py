"""Phase 4 interview presentation tests (brief locked 2026-10-26).

The v2 ENGINE is untouched; these pin the presentation layer:

- P2: a turn's focus never asks more than FOCUS_MAX_QUESTIONS (4)
  pieces — larger themes split across consecutive turns, small
  clusters top up from the next cluster, and the consolidated sweep
  only claims a turn when every remaining item fits in it.
- P1: composed turns never present as an enumerated/numbered list of
  sub-questions — not in the prompt's ask instruction (v10), not in
  the provider's no-text fallback, not in bundle-offer text.
- P3: fields already asked in an earlier turn ride the next turn as
  woven follow-ups (a distinct prompt segment + fallback segment),
  not as fresh questions; the end sweep stays a backstop.
- P4 (unit level): the "don't know yet" park — request detection,
  display progress (parked counted neither answered nor missing),
  and gap/bundle exclusion. API-level coverage lives in
  test_interview.py::TestPhase4ParkedItems.
"""

from __future__ import annotations

import re
from unittest import mock

from app.domains.ai import provider as ai_provider
from app.domains.ai.provider import ChatMessage, InterviewContext
from app.domains.interview import prompts
from app.domains.interview import service as interview_service
from app.domains.interview.service import next_gap_cluster, next_gap_focus
from app.domains.legal_content.pack_loader import get_pack


def _v2_sets():
    return get_pack("US-VA", "2.0.0").question_sets


def _keys(focus) -> list[str]:
    return [q["key"] for q in focus["questions"]]


# ---------------------------------------------------------------------------
# Synthetic question sets: full control over cluster sizes.
# ---------------------------------------------------------------------------


def _sets(spec: dict[str, list[str]]) -> dict:
    """spec: cluster name -> question keys (all required text)."""
    questions = []
    for cluster, keys in spec.items():
        for key in keys:
            questions.append(
                {
                    "key": key,
                    "label": f"Question for {key}?",
                    "type": "text",
                    "required": True,
                    "cluster": cluster,
                }
            )
    return {"synthetic": {"sections": [{"id": "s1", "questions": questions}]}}


# ---------------------------------------------------------------------------
# P2 — cluster cap
# ---------------------------------------------------------------------------


def test_focus_caps_first_cluster_at_four():
    sets = _sets({"identity": [f"id_{i}" for i in range(6)], "money": ["m_0"]})
    focus = next_gap_focus(sets, {})
    assert focus is not None
    assert _keys(focus) == ["id_0", "id_1", "id_2", "id_3"]
    assert focus["clusters"] == ["identity"]
    assert not focus.get("sweep")


def test_focus_splits_large_cluster_across_turns():
    sets = _sets({"identity": [f"id_{i}" for i in range(6)], "money": ["m_0", "m_1"]})
    answers: dict = {}
    first = next_gap_focus(sets, answers)
    assert first is not None
    for key in _keys(first):
        answers[key] = "x"
    second = next_gap_focus(sets, answers)
    assert second is not None
    # The rest of identity, topped up from money — no repeats, in order.
    assert _keys(second) == ["id_4", "id_5", "m_0", "m_1"]
    assert second["clusters"] == ["identity", "money"]


def test_focus_tops_up_small_cluster_from_next():
    sets = _sets({"children": ["c_0"], "money": ["m_0", "m_1", "m_2", "m_3"]})
    focus = next_gap_focus(sets, {})
    assert focus is not None
    assert _keys(focus) == ["c_0", "m_0", "m_1", "m_2"]
    assert focus["clusters"] == ["children", "money"]


def test_focus_sweep_only_when_everything_remaining_fits():
    sets = _sets(
        {
            "identity": ["a_0", "a_1"],
            "money": ["b_0", "b_1"],
            "wishes": ["w_0"],
        }
    )
    # Five remain overall: within the old sweep threshold (6) but over
    # the per-turn cap — a capped tail ask, NOT a sweep.
    focus = next_gap_focus(sets, {})
    assert focus is not None
    assert len(focus["questions"]) == 4
    assert not focus.get("sweep")
    # Four remain: the turn really is the whole remainder -> sweep.
    focus = next_gap_focus(sets, {"w_0": "x"})
    assert focus is not None
    assert focus.get("sweep") is True
    assert {q["key"] for q in focus["questions"]} == {"a_0", "a_1", "b_0", "b_1"}


def test_focus_cap_holds_on_the_real_pack_until_complete():
    sets = _v2_sets()
    answers: dict = {}
    asked: list[str] = []
    for _ in range(40):
        focus = next_gap_focus(sets, answers)
        if focus is None:
            break
        assert len(focus["questions"]) <= 4, focus
        for q in focus["questions"]:
            assert q["key"] not in asked, f"re-asked {q['key']}"
            asked.append(q["key"])
            answers[q["key"]] = True if q["type"] == "boolean" else "x"
    assert next_gap_focus(sets, answers) is None
    # The capped walk still covers the whole applicable required set.
    _pct_missing = interview_service._progress(
        get_pack("US-VA", "2.0.0"), answers
    )[1]
    assert _pct_missing == []


def test_focus_real_pack_first_turn_is_identity_head():
    sets = _v2_sets()
    gap = next_gap_cluster(sets, {})
    focus = next_gap_focus(sets, {})
    assert gap is not None and focus is not None
    assert focus["cluster"] == "identity"
    assert _keys(focus) == [q["key"] for q in gap["questions"]][:4]


# ---------------------------------------------------------------------------
# Enumerated-list detector (P1): a composed turn must never present as
# a numbered list of sub-questions or a run of question lines.
# ---------------------------------------------------------------------------

_NUMBERED_SEQ = re.compile(r"(?:^|\s)1[).]\s.*?(?:^|\s)2[).]\s", re.DOTALL)


def assert_not_enumerated(text: str) -> None:
    assert not _NUMBERED_SEQ.search(text), f"numbered list in: {text!r}"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    question_lines = [ln for ln in lines if ln.endswith("?")]
    assert len(question_lines) <= 2, f"question-line dump in: {text!r}"
    bullet_questions = [
        ln for ln in lines if ln[:2] in ("- ", "• ", "* ") and ln.endswith("?")
    ]
    assert len(bullet_questions) <= 1, f"bulleted questions in: {text!r}"


# ---------------------------------------------------------------------------
# P1/P3 — prompt v10 (natural questions)
# ---------------------------------------------------------------------------

_BASE_KW = dict(
    pack_id="us-va",
    jurisdiction_code="US-VA",
    answered_required=3,
    total_required=41,
)


def _cluster_gap(keys_labels: list[tuple[str, str]], **extra):
    gap = {
        "cluster": "people",
        "clusters": ["people"],
        "questions": [
            {"key": k, "label": label, "type": "text"} for k, label in keys_labels
        ],
    }
    gap.update(extra)
    return gap


def test_v10_cluster_prompt_asks_one_natural_question():
    gap = _cluster_gap(
        [
            ("primary_executor_person", "Who should be your executor?"),
            ("backup_executor_person", "Who should be your backup executor?"),
            ("primary_executor_address", "What is your executor's address?"),
        ]
    )
    text = prompts.build_system_prompt(
        missing_required_keys=[q["key"] for q in gap["questions"]],
        cluster_gap=gap,
        natural_questions=True,
        **_BASE_KW,
    )
    assert "ONE natural question" in text
    # The v7 numbered-list instruction is gone from the ask text.
    assert "as a short numbered list" not in text
    assert "one line per field" not in text


def test_legacy_cluster_prompt_keeps_v7_wording():
    gap = _cluster_gap([("primary_executor_person", "Who should be your executor?")])
    text = prompts.build_system_prompt(
        missing_required_keys=["primary_executor_person"],
        cluster_gap=gap,
        **_BASE_KW,
    )
    # Byte-compat posture: without the Phase 4 flag the v7 instruction
    # renders exactly as before.
    assert "numbered list" in text


def test_v10_prompt_weaves_follow_up_fields():
    gap = _cluster_gap(
        [
            ("asset_categories_present", "Which assets do you have?"),
            ("has_retirement_accounts", "Do you have retirement accounts?"),
            ("primary_executor_phone", "What is your executor's phone number?"),
        ]
    )
    text = prompts.build_system_prompt(
        missing_required_keys=["asset_categories_present"],
        cluster_gap=gap,
        natural_questions=True,
        follow_up_keys={"primary_executor_phone"},
        **_BASE_KW,
    )
    assert "STILL OPEN FROM EARLIER" in text
    assert "from earlier" in text.lower()
    assert "weave" in text.lower()
    # Both segments still enumerate their fields for extraction scope.
    assert "primary_executor_phone" in text
    assert "asset_categories_present" in text


def test_v10_prompt_pure_follow_up_turn():
    gap = _cluster_gap(
        [
            ("primary_executor_phone", "What is your executor's phone number?"),
            ("backup_executor_person", "Who should be your backup executor?"),
        ]
    )
    text = prompts.build_system_prompt(
        missing_required_keys=["primary_executor_phone", "backup_executor_person"],
        cluster_gap=gap,
        natural_questions=True,
        follow_up_keys={"primary_executor_phone", "backup_executor_person"},
        **_BASE_KW,
    )
    assert "short follow-up" in text
    assert "ONE natural question" not in text


def test_v10_sweep_prompt_is_a_wrap_up_not_an_interrogation():
    gap = _cluster_gap(
        [("organ_donation_wishes", "What are your organ donation wishes?")],
        sweep=True,
    )
    text = prompts.build_system_prompt(
        missing_required_keys=["organ_donation_wishes"],
        cluster_gap=gap,
        natural_questions=True,
        **_BASE_KW,
    )
    assert "wrap-up" in text
    assert "ask for ALL of them" not in text


def test_v10_verdict_turn_next_gap_is_natural():
    offer = {
        "bundle": "primary",
        "anchor_key": "primary_executor_person",
        "anchor_name": "Priya Sharma",
        "fill_keys": ["poa_agent_person"],
        "fill_labels": ["Financial agent"],
        "question_text": "Should Priya Sharma also be your financial agent?",
        "presented_before": True,
    }
    next_gap = _cluster_gap(
        [("has_retirement_accounts", "Do you have retirement accounts?")]
    )
    text = prompts.build_system_prompt(
        missing_required_keys=["has_retirement_accounts"],
        bundle_offers=[offer],
        next_gap=next_gap,
        natural_questions=True,
        **_BASE_KW,
    )
    assert "as a short numbered list" not in text
    assert "ONE natural question" in text


def test_v10_merged_offers_presented_as_sentences():
    offers = [
        {
            "bundle": "primary",
            "anchor_key": "primary_executor_person",
            "anchor_name": "Priya Sharma",
            "fill_keys": ["poa_agent_person"],
            "fill_labels": ["Financial agent"],
            "question_text": "Should Priya Sharma also be your financial agent?",
        },
        {
            "bundle": "backup",
            "anchor_key": "backup_executor_person",
            "anchor_name": "Amit Sharma",
            "fill_keys": ["successor_trustee_person"],
            "fill_labels": ["Successor trustee"],
            "question_text": "Should Amit Sharma also be your successor trustee?",
        },
    ]
    text = prompts.build_system_prompt(
        missing_required_keys=["poa_agent_person", "successor_trustee_person"],
        bundle_offers=offers,
        natural_questions=True,
        **_BASE_KW,
    )
    assert "as its own numbered line" not in text
    assert "its own sentence" in text
    # The verbatim question texts still reach the model.
    assert "Should Priya Sharma also be your financial agent?" in text
    assert "Should Amit Sharma also be your successor trustee?" in text


def test_v10_prompt_handles_dont_know_without_pressing():
    gap = _cluster_gap([("primary_executor_person", "Who should be your executor?")])
    text = prompts.build_system_prompt(
        missing_required_keys=["primary_executor_person"],
        cluster_gap=gap,
        natural_questions=True,
        **_BASE_KW,
    )
    assert "don't know" in text.lower()
    assert "sets the item aside" in text


def test_v10_deferral_bullet_absent_when_turn_cannot_park():
    # Review I-2: on offer/verdict turns a deferral is consumed as a
    # verdict read and nothing is set aside — the prompt must not
    # promise parking there. post_turn passes deferral_parks=False
    # exactly when presented/verdict offers own the turn (the same
    # condition that disables conversational parking).
    gap = _cluster_gap([("primary_executor_person", "Who should be your executor?")])
    text = prompts.build_system_prompt(
        missing_required_keys=["primary_executor_person"],
        cluster_gap=gap,
        natural_questions=True,
        deferral_parks=False,
        **_BASE_KW,
    )
    assert "sets the item aside" not in text


def test_prompt_version_v10_constant_exists():
    assert prompts.PROMPT_TEMPLATE_VERSION_V10 == "v10"


# ---------------------------------------------------------------------------
# P1/P3 — provider no-text fallback composition
# ---------------------------------------------------------------------------


class _FakeResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "propose_field_values",
                                    "arguments": '{"proposals": []}',
                                }
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {},
        }


class _FakeClient:
    def post(self, *args, **kwargs):
        return _FakeResp()


def _chat(monkeypatch, context: InterviewContext):
    monkeypatch.setattr(ai_provider, "get_http_client", lambda: _FakeClient())
    provider = ai_provider.OpenAIChatProvider(api_key="test", model="gpt-test")
    return provider.chat(
        system_prompt="SYS",
        messages=[ChatMessage(role="user", content="some answer")],
        context=context,
    )


def test_fallback_multi_ask_is_flowing_prose(monkeypatch):
    context = InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=["a_key", "b_key", "c_key"],
        question_labels={
            "a_key": "Who should be your executor?",
            "b_key": "Who should be your backup executor?",
            "c_key": "What is your executor's phone number?",
        },
        focus_keys=["a_key", "b_key", "c_key"],
    )
    resp = _chat(monkeypatch, context)
    assert_not_enumerated(resp.reply)
    assert "1)" not in resp.reply and "2)" not in resp.reply
    for label in (
        "Who should be your executor?",
        "Who should be your backup executor?",
        "What is your executor's phone number?",
    ):
        assert label in resp.reply


def test_fallback_follow_up_segment_is_woven(monkeypatch):
    context = InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=["topic_key", "old_key"],
        question_labels={
            "topic_key": "Which assets do you have?",
            "old_key": "What is your executor's phone number?",
        },
        focus_keys=["topic_key", "old_key"],
        focus_follow_up_keys=["old_key"],
    )
    resp = _chat(monkeypatch, context)
    assert_not_enumerated(resp.reply)
    assert "Which assets do you have?" in resp.reply
    assert "from earlier" in resp.reply.lower()
    assert "What is your executor's phone number?" in resp.reply


def test_fallback_single_follow_up_only(monkeypatch):
    context = InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=["old_key"],
        question_labels={"old_key": "What is your executor's phone number?"},
        focus_keys=["old_key"],
        focus_follow_up_keys=["old_key"],
    )
    resp = _chat(monkeypatch, context)
    assert "from earlier" in resp.reply.lower()
    assert "What is your executor's phone number?" in resp.reply


def test_bundle_questions_text_flows_as_sentences():
    offers = [
        {
            "bundle": "primary",
            "anchor_key": "primary_executor_person",
            "anchor_name": "Priya Sharma",
            "fill_keys": ["poa_agent_person"],
        },
        {
            "bundle": "primary",
            "anchor_key": "primary_executor_person",
            "anchor_name": "Priya Sharma",
            "fill_keys": ["healthcare_agent_person"],
        },
        {
            "bundle": "backup",
            "anchor_key": "backup_executor_person",
            "anchor_name": "Amit Sharma",
            "fill_keys": ["successor_trustee_person"],
        },
    ]
    text = interview_service._bundle_questions_text(offers)  # type: ignore[arg-type]
    assert_not_enumerated(text)
    assert "\n" not in text
    # Each offer's verbatim question survives the composition.
    for offer in offers:
        assert interview_service._bundle_question_text(offer) in text


# ---------------------------------------------------------------------------
# P4 — parked items ("don't know yet"), unit level
# ---------------------------------------------------------------------------


def test_park_request_detection_positives():
    for message in (
        "I don't know",
        "I don't know yet",
        "i dont know",
        "Not sure",
        "I'm not sure",
        "No idea",
        "skip",
        "Skip it",
        "Skip this one",
        "I'll have to check",
        "I need to check on that",
        "Hmm, I don't know",
    ):
        assert interview_service._is_park_request(message), message


def test_park_request_detection_negatives():
    for message in (
        # Contains an actual answer — a park must never swallow facts.
        "I don't know his phone number but his email is amit@example.com",
        "I'm not sure whether the house is in the trust — it is, since 2019",
        "I don't know, Priya Sharma is my executor though",
        # Ordinary answers and verdicts are not parks.
        "No",
        "Yes",
        "Priya Sharma",
        "I already answered this",
        "What does that mean?",
    ):
        assert not interview_service._is_park_request(message), message


def test_display_progress_counts_parked_neither_answered_nor_missing():
    pack = get_pack("US-VA", "2.0.0")
    answers = {
        "testator_full_name": "Arjun Sharma",
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
    _pct, raw_missing, raw_total, raw_answered = interview_service._progress(
        pack, answers
    )
    assert "has_minor_children" in raw_missing
    pct, missing, total, answered, parked_required = (
        interview_service._display_progress(
            pack, answers, {"has_minor_children"}
        )
    )
    assert parked_required == ["has_minor_children"]
    assert "has_minor_children" not in missing
    assert total == raw_total - 1
    assert answered == raw_answered
    expected = round(100.0 * answered / total, 2) if total else 100.0
    assert pct == expected


def test_display_progress_without_parks_matches_raw_progress():
    pack = get_pack("US-VA", "2.0.0")
    answers = {"testator_full_name": "Arjun Sharma"}
    raw = interview_service._progress(pack, answers)
    pct, missing, total, answered, parked_required = (
        interview_service._display_progress(pack, answers, set())
    )
    assert (pct, missing, total, answered) == raw
    assert parked_required == []


def test_gaps_exclude_parked_keys():
    sets = _sets(
        {"identity": ["id_0", "id_1"], "money": ["m_0"], "wishes": ["w_0"]}
    )
    focus = next_gap_focus(sets, {}, parked_keys={"id_0", "id_1"})
    assert focus is not None
    assert "id_0" not in _keys(focus) and "id_1" not in _keys(focus)
    assert focus["cluster"] == "money"


def test_bundle_offers_never_fill_parked_keys():
    from app.domains.interview.service import pending_bundle_offers

    answers = {
        "testator_full_name": "Arjun Sharma",
        "marital_status": "married",
        "primary_residence_state": "Texas",
        "testator_street_address": "123 Main Street",
        "testator_city": "Austin",
        "has_minor_children": True,
        "wants_revocable_trust": True,
        "wants_poa_document": True,
        "wants_healthcare_directive": True,
        "primary_executor_person": "Priya Sharma",
        "backup_executor_person": "Amit Sharma",
    }
    offers = pending_bundle_offers(
        _v2_sets(), answers, parked_keys={"poa_agent_person"}
    )
    for offer in offers:
        assert "poa_agent_person" not in offer["fill_keys"]
    # Parking every fill of the primary bundle retires the offer itself.
    offers = pending_bundle_offers(
        _v2_sets(),
        answers,
        parked_keys={"poa_agent_person", "healthcare_agent_person"},
    )
    assert all(o["bundle"] != "primary" for o in offers)


def test_effective_parked_keys_drop_answered():
    parked = interview_service.effective_parked_keys(
        {"a_key", "b_key"}, {"a_key": "some answer", "b_key": None}
    )
    assert parked == {"b_key"}


def test_parked_gate_still_blocks_document_generation_inputs():
    # The documents gate reads compute_progress's missing list: a parked
    # item is unanswered, so it stays in missing and keeps generation
    # gated until the family resolves it. This pins that interplay at
    # the shared-partition level the gate consumes.
    from app.domains.intake import question_sets as qs

    pack = get_pack("US-VA", "2.0.0")
    answers = {"testator_full_name": "Arjun Sharma"}
    _per_set, _pct, missing, _pruned = qs.compute_progress(
        pack.question_sets, answers
    )
    assert "marital_status" in missing  # parked or not, still unanswered
    # ...while the interview's own display progress sets it apart.
    _dpct, dmissing, _dt, _da, parked_required = (
        interview_service._display_progress(pack, answers, {"marital_status"})
    )
    assert "marital_status" not in dmissing
    assert parked_required == ["marital_status"]



# ---------------------------------------------------------------------------
# Review I-1: the sweep framing never races pending bundle verdicts
# ---------------------------------------------------------------------------


def _sweep_gap():
    gap = _cluster_gap(
        [
            ("has_debts", "Do you have any debts?"),
            ("has_life_insurance", "Do you have life insurance?"),
        ]
    )
    gap["sweep"] = True
    return gap


def test_sweep_suppressed_while_verdicts_pending():
    focus = _sweep_gap()
    out = interview_service._focus_without_sweep_on_verdict_turns(
        focus, [{"decline_key": "declined_x"}]
    )
    assert out is not None
    assert "sweep" not in out
    # The questions themselves are untouched — only the framing goes.
    assert [q["key"] for q in out["questions"]] == [
        q["key"] for q in focus["questions"]
    ]


def test_sweep_kept_without_verdicts_and_noops():
    focus = _sweep_gap()
    assert (
        interview_service._focus_without_sweep_on_verdict_turns(focus, [])
        is focus
    )
    plain = _cluster_gap([("has_debts", "Do you have any debts?")])
    assert (
        interview_service._focus_without_sweep_on_verdict_turns(
            plain, [{"decline_key": "declined_x"}]
        )
        is plain
    )
    assert (
        interview_service._focus_without_sweep_on_verdict_turns(
            None, [{"decline_key": "declined_x"}]
        )
        is None
    )


def test_suppressed_sweep_renders_no_wrapup_line():
    gap = interview_service._focus_without_sweep_on_verdict_turns(
        _sweep_gap(), [{"decline_key": "declined_x"}]
    )
    text = prompts.build_system_prompt(
        missing_required_keys=["has_debts", "has_life_insurance"],
        cluster_gap=gap,
        natural_questions=True,
        **_BASE_KW,
    )
    assert "last open items in the whole interview" not in text
    # ...while the unsuppressed sweep keeps the Phase 4 wrap-up line.
    text2 = prompts.build_system_prompt(
        missing_required_keys=["has_debts", "has_life_insurance"],
        cluster_gap=_sweep_gap(),
        natural_questions=True,
        **_BASE_KW,
    )
    assert "last open items in the whole interview" in text2
