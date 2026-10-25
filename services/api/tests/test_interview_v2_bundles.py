"""Role bundles + identity capture tests (Interview v2, Part 3).

Bundles are deterministic: exactly one answered member (the anchor) plus
at least one unanswered APPLICABLE member triggers a grouped yes/no offer;
a YES fills the rest by server-created proposals (source "bundle"), a NO
is recorded in the session state and the offer never repeats for that
anchor. Identity capture syncs the confirmed legal name onto the
household's primary-client person.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from app.domains.interview import prompts
from app.domains.interview.service import pending_bundle_offer
from app.domains.legal_content.pack_loader import get_pack


def _v2_sets():
    return get_pack("US-VA", "2.0.0").question_sets


BASE = {
    "testator_full_name": "Arjun Sharma",
    "marital_status": "married",
    "primary_residence_state": "Texas",
    "testator_street_address": "123 Main Street",
    "testator_city": "Austin",
    "has_minor_children": True,
    "wants_revocable_trust": True,
    "wants_poa_document": True,
    "wants_healthcare_directive": True,
}


# --- pending_bundle_offer ----------------------------------------------------


def test_primary_offer_triggers_on_single_anchor():
    answers = dict(BASE, primary_executor_person="Priya Sharma")
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is not None
    assert offer["bundle"] == "primary"
    assert offer["anchor_key"] == "primary_executor_person"
    assert offer["anchor_name"] == "Priya Sharma"
    assert offer["fill_keys"] == ["poa_agent_person", "healthcare_agent_person"]
    assert offer["fill_value"] == "Priya Sharma"
    assert offer["decline_key"] == "primary:priya sharma"


def test_no_offer_without_anchor_or_with_two_answered():
    assert pending_bundle_offer(_v2_sets(), dict(BASE)) is None or (
        pending_bundle_offer(_v2_sets(), dict(BASE))["bundle"] == "address"
    )
    answers = dict(
        BASE, primary_executor_person="Priya Sharma", poa_agent_person="Amit Sharma"
    )
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is None or offer["bundle"] != "primary"


def test_backup_offer_excludes_pruned_trustee():
    answers = dict(
        BASE,
        wants_revocable_trust=False,
        primary_executor_person="Priya Sharma",
        poa_agent_person="Priya Sharma",
        healthcare_agent_person="Priya Sharma",
        backup_executor_person="Amit Sharma",
    )
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is not None and offer["bundle"] == "backup"
    # successor_trustee_person is pruned (no trust) — never a fill target.
    assert offer["fill_keys"] == [
        "successor_poa_agent_person",
        "successor_healthcare_agent_person",
    ]


def test_declined_offer_is_suppressed():
    answers = dict(BASE, primary_executor_person="Priya Sharma")
    offer = pending_bundle_offer(
        _v2_sets(), answers, declined={"primary:priya sharma"}
    )
    assert offer is None or offer["bundle"] != "primary"


def test_address_bundle_composition():
    offer = pending_bundle_offer(_v2_sets(), dict(BASE))
    assert offer is not None and offer["bundle"] == "address"
    assert offer["fill_value"] == "123 Main Street, Austin, Texas"
    assert "primary_executor_address" in offer["fill_keys"]
    assert "poa_agent_address" in offer["fill_keys"]
    # Guardian address applies (minor children) and is missing.
    assert "primary_guardian_address" in offer["fill_keys"]


def test_address_bundle_skips_guardian_when_no_minors():
    answers = dict(BASE, has_minor_children=False)
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is not None and offer["bundle"] == "address"
    assert "primary_guardian_address" not in offer["fill_keys"]


# --- bundle prompt -----------------------------------------------------------


def test_bundle_prompt_block():
    offer = {
        "bundle": "primary",
        "anchor_key": "primary_executor_person",
        "anchor_name": "Priya Sharma",
        "fill_keys": ["poa_agent_person", "healthcare_agent_person"],
        "fill_labels": [
            "Who should make financial decisions for you if you cannot (power of attorney agent)?",
            "Who should make medical decisions for you if you cannot?",
        ],
        "fill_value": "Priya Sharma",
        "decline_key": "primary:priya sharma",
    }
    text = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["poa_agent_person"],
        answered_required=20,
        total_required=42,
        pending_proposal_keys=[],
        answered_keys=[],
        bundle_offer=offer,
    )
    assert "BUNDLE OFFER" in text
    assert "Priya Sharma" in text
    assert "Complete one section before moving to" not in text


# --- identity: signed-in name in the identity cluster brief -------------------


def test_identity_brief_confirms_signed_in_name():
    gap = {
        "cluster": "identity",
        "questions": [
            {"key": "testator_full_name", "label": "What is your full legal name?", "type": "text"},
        ],
    }
    text = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["testator_full_name"],
        answered_required=0,
        total_required=42,
        pending_proposal_keys=[],
        answered_keys=[],
        cluster_gap=gap,
        signed_in_name="Arjun Sharma",
    )
    assert "signed in as Arjun Sharma" in text
    text2 = prompts.build_system_prompt(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["testator_full_name"],
        answered_required=0,
        total_required=42,
        pending_proposal_keys=[],
        answered_keys=[],
        cluster_gap=gap,
    )
    assert "signed in as" not in text2


# --- _sync_testator_name -------------------------------------------------------

from app.domains.household import repository as household_repo  # noqa: E402
from app.domains.intake import service as intake_service  # noqa: E402


class _Cur:
    def execute(self, *_a, **_k):
        pass

    def fetchone(self):
        return None


class _Conn:
    @contextmanager
    def cursor(self):
        yield _Cur()


def _person(name, *, kind="adult", pid=None):
    return {"id": pid or str(uuid4()), "full_name": name, "kind": kind}


def test_sync_updates_unknown_person(monkeypatch):
    updated, inserted = [], []
    monkeypatch.setattr(
        household_repo, "list_persons_for_household",
        lambda c, h: [_person("Unknown person")],
    )
    monkeypatch.setattr(
        household_repo, "update_person",
        lambda c, pid, fields: updated.append((str(pid), fields)) or {},
    )
    monkeypatch.setattr(
        household_repo, "insert_person",
        lambda c, **kw: inserted.append(kw) or {"id": str(uuid4())},
    )
    intake_service._sync_testator_name(
        _Conn(), household_id=uuid4(), answers={"testator_full_name": "Arjun Sharma"}
    )
    assert len(updated) == 1 and updated[0][1] == {"full_name": "Arjun Sharma"}
    assert inserted == []


def test_sync_never_overwrites_real_name(monkeypatch):
    updated, inserted = [], []
    monkeypatch.setattr(
        household_repo, "list_persons_for_household",
        lambda c, h: [_person("Arjun Sharma")],
    )
    monkeypatch.setattr(
        household_repo, "update_person",
        lambda c, pid, fields: updated.append(fields) or {},
    )
    monkeypatch.setattr(
        household_repo, "insert_person",
        lambda c, **kw: inserted.append(kw) or {"id": str(uuid4())},
    )
    intake_service._sync_testator_name(
        _Conn(), household_id=uuid4(), answers={"testator_full_name": "Different Name"}
    )
    assert updated == [] and inserted == []


def test_sync_creates_primary_person_when_household_has_none(monkeypatch):
    inserted = []
    monkeypatch.setattr(
        household_repo, "list_persons_for_household", lambda c, h: []
    )
    monkeypatch.setattr(
        household_repo, "insert_person",
        lambda c, **kw: inserted.append(kw) or {"id": str(uuid4())},
    )
    intake_service._sync_testator_name(
        _Conn(), household_id=uuid4(), answers={"testator_full_name": "Arjun Sharma"}
    )
    assert len(inserted) == 1
    assert inserted[0]["full_name"] == "Arjun Sharma"
    assert inserted[0]["kind"] == "adult"


def test_sync_noop_without_name_answer(monkeypatch):
    called = []
    monkeypatch.setattr(
        household_repo, "list_persons_for_household",
        lambda c, h: called.append(1) or [],
    )
    intake_service._sync_testator_name(_Conn(), household_id=uuid4(), answers={})
    assert called == []


# --- presentation gating: a verdict requires a PRIOR presentation -----------
# Live Part 3 drive (2026-10-24): the address bundle filled on the very
# turn it first became pending — the user's "Yes" answered the PREVIOUS
# visible question (children), because the no-text provider fallback had
# asked a different question than the prompt intended. An offer may only
# consume a yes/no verdict once it has been presented in an earlier turn.

from app.domains.interview import repository as interview_repo  # noqa: E402
from app.domains.interview import service as interview_service  # noqa: E402


def _offer_for(state, monkeypatch):
    monkeypatch.setattr(
        interview_repo, "get_session_state", lambda c, sid: dict(state)
    )
    answers = dict(BASE, primary_executor_person="Priya Sharma")
    return interview_service._bundle_offer_for_turn(
        _Conn(),
        session_id=uuid4(),
        pack=get_pack("US-VA", "2.0.0"),
        answers=answers,
        question_labels={},
    )


def test_first_presentation_is_not_verdict_ready(monkeypatch):
    offer, state = _offer_for({}, monkeypatch)
    assert offer is not None
    assert offer["presented_before"] is False
    assert state["bundles_offered"]["primary:priya sharma"] == 1


def test_second_presentation_is_verdict_ready(monkeypatch):
    offer, state = _offer_for(
        {"bundles_offered": {"primary:priya sharma": 1}}, monkeypatch
    )
    assert offer is not None
    assert offer["presented_before"] is True
    assert state["bundles_offered"]["primary:priya sharma"] == 2


def test_offer_suppressed_after_two_presentations(monkeypatch):
    # At the cap with no freshness proof (no recorded final
    # presentation), the offer is simply gone: roles are asked
    # individually by the cluster flow.
    offer, _state = _offer_for(
        {"bundles_offered": {"primary:priya sharma": 2}}, monkeypatch
    )
    assert offer is None


# --- provider no-text fallback must ask the turn's actual focus -------------

import json as _json  # noqa: E402

from app.domains.ai import provider as ai_provider  # noqa: E402


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
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
                                    "arguments": _json.dumps(
                                        {
                                            "proposals": [
                                                {
                                                    "question_key": "marital_status",
                                                    "value": "married",
                                                    "confidence": 0.95,
                                                    "source_snippet": "I'm married",
                                                }
                                            ]
                                        }
                                    ),
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
    def post(self, *a, **k):
        return _FakeResp()


def _chat_with_context(monkeypatch, context):
    monkeypatch.setattr(ai_provider, "get_http_client", lambda: _FakeClient())
    provider = ai_provider.OpenAIChatProvider(api_key="test", model="gpt-test")
    return provider.chat(
        system_prompt="SYS",
        messages=[ai_provider.ChatMessage(role="user", content="I'm married")],
        context=context,
    )


def test_fallback_reply_uses_focus_question_text(monkeypatch):
    context = ai_provider.InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=["primary_guardian_person"],
        question_labels={"primary_guardian_person": "Who should care for your minor children?"},
        focus_question_text="Should your executor also use your address (123 Main Street, Austin, Texas)?",
    )
    resp = _chat_with_context(monkeypatch, context)
    assert "Should your executor also use your address" in resp.reply
    assert "Who should care" not in resp.reply


def test_fallback_reply_prefers_focus_keys(monkeypatch):
    context = ai_provider.InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=["has_minor_children", "asset_categories_present"],
        question_labels={
            "has_minor_children": "Do you have minor children?",
            "asset_categories_present": "Which assets do you have?",
        },
        focus_keys=["asset_categories_present"],
    )
    resp = _chat_with_context(monkeypatch, context)
    assert "Which assets do you have?" in resp.reply


def test_fallback_reply_never_escapes_the_focus(monkeypatch):
    # Review I-1: focus set + every focus key just proposed -> the reply
    # points at the review cards instead of asking a pack-order question
    # from another cluster (the visible-question divergence shape).
    context = ai_provider.InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=["primary_guardian_person", "marital_status"],
        question_labels={
            "primary_guardian_person": "Who should care for your minor children?",
            "marital_status": "What best describes your household?",
        },
        focus_keys=["marital_status"],
    )
    resp = _chat_with_context(monkeypatch, context)
    assert "Who should care" not in resp.reply
    assert "review the proposed values" in resp.reply


# --- reviewer fixes (full Part 3 verdict, delivered post-build) -------------


def test_pruned_member_cannot_anchor_primary():
    # wants_poa declined: the answered POA agent is PRUNED — it must
    # not anchor an offer filling executor + healthcare.
    answers = dict(
        BASE, wants_poa_document=False, poa_agent_person="Priya Sharma"
    )
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is None or offer["bundle"] != "primary"


def test_pruned_member_cannot_anchor_backup():
    answers = dict(
        BASE,
        wants_revocable_trust=False,
        successor_trustee_person="Amit Sharma",
    )
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is None or offer["bundle"] != "backup"


def test_pruned_answered_member_does_not_suppress_offer():
    # Executor answered; healthcare answered but PRUNED (directive
    # declined). The pruned member must not count toward the
    # exactly-one rule — the executor should still anchor an offer
    # for the POA role.
    answers = dict(
        BASE,
        wants_healthcare_directive=False,
        primary_executor_person="Priya Sharma",
        healthcare_agent_person="Priya Sharma",
    )
    offer = pending_bundle_offer(_v2_sets(), answers)
    assert offer is not None and offer["bundle"] == "primary"
    assert offer["anchor_key"] == "primary_executor_person"
    assert offer["fill_keys"] == ["poa_agent_person"]


def _offer_at_cap(state, turns_now, monkeypatch):
    monkeypatch.setattr(
        interview_repo, "count_turns", lambda c, sid: turns_now
    )
    return _offer_for(state, monkeypatch)


def test_offer_at_cap_is_verdict_only_when_fresh(monkeypatch):
    # Gating review I-1 + batch re-review I-1: at MAX presentations
    # the offer is consumable as a verdict ONLY on the exact next
    # turn boundary after the final presentation (turn rows: the
    # presentation turn's pair makes the count presented_at + 2).
    state = {
        "bundles_offered": {"primary:priya sharma": 2},
        "bundles_last_presented": {"primary:priya sharma": 10},
    }
    offer, out_state = _offer_at_cap(state, 12, monkeypatch)
    assert offer is not None
    assert offer.get("verdict_only") is True
    assert offer["presented_before"] is True
    # Verdict-only turns do not spend further presentations.
    assert out_state["bundles_offered"]["primary:priya sharma"] == 2


def test_offer_at_cap_retired_when_another_exchange_intervened(monkeypatch):
    # An advice refusal (or any completed exchange) between the final
    # presentation and now makes the window stale: the user is no
    # longer answering the bundle question.
    state = {
        "bundles_offered": {"primary:priya sharma": 2},
        "bundles_last_presented": {"primary:priya sharma": 10},
    }
    offer, _state = _offer_at_cap(state, 14, monkeypatch)
    assert offer is None


def test_state_read_failure_returns_no_offer_and_flags_state(monkeypatch):
    # Batch review I-1: a failed state read must not hand post_turn a
    # fresh {} to persist over the stored bundle state.
    def _boom(c, sid):
        raise RuntimeError("db down")

    monkeypatch.setattr(interview_repo, "get_session_state", _boom)
    offer, state = interview_service._bundle_offer_for_turn(
        _Conn(),
        session_id=uuid4(),
        pack=get_pack("US-VA", "2.0.0"),
        answers=dict(BASE, primary_executor_person="Priya Sharma"),
        question_labels={},
    )
    assert offer is None
    assert state.get("_state_unavailable") is True


def test_corrupt_offered_count_does_not_kill_the_turn(monkeypatch):
    # Batch review M-2: a corrupt bundles_offered value must coerce
    # defensively instead of raising out of _bundle_offer_for_turn.
    offer, _state = _offer_for(
        {"bundles_offered": {"primary:priya sharma": "not-a-number"}},
        monkeypatch,
    )
    assert offer is not None
    assert offer["presented_before"] is False


def test_bundle_fill_keys_skip_decided_proposals():
    # Batch review I-1: a fill key the user already adjudicated
    # (confirmed / corrected / REJECTED on its card) must never be
    # re-filled by a later bundle YES — the fallbacks got this guard
    # after the E2E rejected-fabrication incident; the verdict path
    # must hold it too.
    from app.domains.interview.service import _bundle_fill_keys

    offer = {
        "fill_keys": ["poa_agent_person", "healthcare_agent_person"],
    }
    keys = _bundle_fill_keys(
        offer,
        pending=set(),
        open_now=set(),
        decided={"poa_agent_person"},
        answers={},
    )
    assert keys == ["healthcare_agent_person"]
