"""Turn-count economics tests (Interview v2 — A/B follow-up iteration).

The deterministic guards closed repeats and wrong-source proposals; the
A/B bar then failed on TURN COUNT (best Sharma 13 vs <=12, best Pruned
15 vs <=8). The structural tax: each bundle offer (primary, backup,
address) consumed its own presentation + verdict exchange, and the
cluster prompt capped its ask at 4 fields. These tests pin the denser
machinery:

- pending_bundle_offers: ALL pending offers, not just the first.
- _bundle_offers_for_turn: per-offer presentation bookkeeping so
  several offers can be presented in ONE message and resolved by ONE
  reply (a single global affirmative resolves all of them).
- _resolve_offer_verdicts: global verdict first (the single-offer
  semantics, unchanged); per-offer scoped reads only when several
  offers are in play and the global read defers.
- next_gap_focus: the first cluster, plus the next cluster when the
  combined ask stays small (L1); every still-missing field of the
  focus in one ask (L3); a consolidated sweep when <=6 applicable
  required fields remain overall (L4).
- Prompt v7: merged offers presented verbatim in one message; a
  verdict-turn prompt that does not re-ask; the single-offer
  first-presentation prompt stays byte-identical to v6.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from uuid import uuid4

from app.domains.interview import prompts
from app.domains.interview import service as interview_service
from app.domains.interview.service import (
    _bundle_offer_for_turn,
    _bundle_offers_for_turn,
    _bundle_question_text,
    _bundle_verdict,
    _resolve_offer_verdicts,
    next_gap_cluster,
    next_gap_focus,
    pending_bundle_offer,
    pending_bundle_offers,
)
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

BOTH_EXECUTORS = dict(
    BASE,
    primary_executor_person="Priya Sharma",
    backup_executor_person="Amit Sharma",
)


# --- pending_bundle_offers ----------------------------------------------------


def test_all_pending_offers_returned_in_priority_order():
    offers = pending_bundle_offers(_v2_sets(), dict(BOTH_EXECUTORS))
    assert [o["bundle"] for o in offers] == ["primary", "backup", "address"]
    primary, backup, address = offers
    assert primary["fill_keys"] == ["poa_agent_person", "healthcare_agent_person"]
    # Trust is wanted, so the successor trustee rides the backup bundle.
    assert backup["fill_keys"] == [
        "successor_poa_agent_person",
        "successor_healthcare_agent_person",
        "successor_trustee_person",
    ]
    assert set(address["fill_keys"]) == {
        "primary_executor_address",
        "poa_agent_address",
        "primary_guardian_address",
    }


def test_pending_bundle_offer_still_returns_the_first():
    offer = pending_bundle_offer(_v2_sets(), dict(BOTH_EXECUTORS))
    assert offer is not None and offer["bundle"] == "primary"


def test_declined_filters_only_that_offer():
    offers = pending_bundle_offers(
        _v2_sets(),
        dict(BOTH_EXECUTORS),
        declined={"primary:priya sharma"},
    )
    assert [o["bundle"] for o in offers] == ["backup", "address"]


def test_no_anchor_means_only_the_address_offer():
    offers = pending_bundle_offers(_v2_sets(), dict(BASE))
    assert [o["bundle"] for o in offers] == ["address"]


# --- _bundle_offers_for_turn ---------------------------------------------------


class _Cur:
    def execute(self, *_a, **_k):
        pass

    def fetchone(self):
        return None


class _Conn:
    @contextmanager
    def cursor(self):
        yield _Cur()


from app.domains.interview import repository as interview_repo  # noqa: E402


def _offers_for(state, monkeypatch, answers=None):
    monkeypatch.setattr(
        interview_repo, "get_session_state", lambda c, sid: dict(state)
    )
    return _bundle_offers_for_turn(
        _Conn(),
        session_id=uuid4(),
        pack=get_pack("US-VA", "2.0.0"),
        answers=dict(BOTH_EXECUTORS) if answers is None else answers,
        question_labels={},
    )


def test_first_presentation_counts_every_offer(monkeypatch):
    presentable, verdict_only, state = _offers_for({}, monkeypatch)
    assert [o["bundle"] for o in presentable] == ["primary", "backup", "address"]
    assert verdict_only == []
    assert all(o["presented_before"] is False for o in presentable)
    offered = state["bundles_offered"]
    assert offered["primary:priya sharma"] == 1
    assert offered["backup:amit sharma"] == 1
    assert offered["address:123 main street, austin, texas"] == 1


def test_second_presentation_is_verdict_ready_for_every_offer(monkeypatch):
    state = {
        "bundles_offered": {
            "primary:priya sharma": 1,
            "backup:amit sharma": 1,
            "address:123 main street, austin, texas": 1,
        }
    }
    presentable, verdict_only, state = _offers_for(state, monkeypatch)
    assert len(presentable) == 3
    assert all(o["presented_before"] is True for o in presentable)


def test_capped_fresh_offer_is_verdict_only_while_others_present(monkeypatch):
    monkeypatch.setattr(interview_repo, "count_turns", lambda c, sid: 12)
    state = {
        "bundles_offered": {
            "primary:priya sharma": 2,
            "backup:amit sharma": 1,
            "address:123 main street, austin, texas": 1,
        },
        "bundles_last_presented": {"primary:priya sharma": 10},
    }
    presentable, verdict_only, _state = _offers_for(state, monkeypatch)
    assert [o["bundle"] for o in verdict_only] == ["primary"]
    assert verdict_only[0]["presented_before"] is True
    assert {o["bundle"] for o in presentable} == {"backup", "address"}


def test_single_offer_wrapper_counts_only_the_first_offer(monkeypatch):
    # _bundle_offer_for_turn is the legacy single-offer seam: with
    # several offers pending it must keep its exact old bookkeeping
    # (only the first offer's presentation is spent).
    monkeypatch.setattr(
        interview_repo, "get_session_state", lambda c, sid: {}
    )
    offer, state = _bundle_offer_for_turn(
        _Conn(),
        session_id=uuid4(),
        pack=get_pack("US-VA", "2.0.0"),
        answers=dict(BOTH_EXECUTORS),
        question_labels={},
    )
    assert offer is not None and offer["bundle"] == "primary"
    assert state["bundles_offered"] == {"primary:priya sharma": 1}


def test_offers_state_read_failure_returns_sentinel(monkeypatch):
    def _boom(c, sid):
        raise RuntimeError("db down")

    monkeypatch.setattr(interview_repo, "get_session_state", _boom)
    presentable, verdict_only, state = _bundle_offers_for_turn(
        _Conn(),
        session_id=uuid4(),
        pack=get_pack("US-VA", "2.0.0"),
        answers=dict(BOTH_EXECUTORS),
        question_labels={},
    )
    assert presentable == [] and verdict_only == []
    assert state.get("_state_unavailable") is True


# --- merged question text -------------------------------------------------------


def test_merged_question_text_carries_every_offer_verbatim():
    offers = pending_bundle_offers(_v2_sets(), dict(BOTH_EXECUTORS))
    text = interview_service._bundle_questions_text(offers)
    for offer in offers:
        assert _bundle_question_text(offer) in text
    lowered = text.lower()
    # The shapes the A/B harness (and a skimming user) key on.
    assert "quick question:" in lowered
    assert "also serve as" in lowered
    assert "also use your address" in lowered


# --- _resolve_offer_verdicts -----------------------------------------------------


def _offer(bundle, anchor_name):
    return {
        "bundle": bundle,
        "anchor_name": anchor_name,
        "decline_key": f"{bundle}:{anchor_name.lower()}",
        "fill_keys": [],
    }


PRIMARY = _offer("primary", "Maya Lee")
BACKUP = _offer("backup", "David Lee")


def test_global_yes_resolves_every_offer():
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, please — that works for all of those."
    )
    assert verdicts == {"primary:maya lee": True, "backup:david lee": True}


def test_global_no_resolves_every_offer():
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "No, not the same person for all of those roles."
    )
    assert verdicts == {"primary:maya lee": False, "backup:david lee": False}


def test_mixed_message_defers_to_reask():
    # V6 CONTRACT (after five review rounds): differentiated
    # statements — designations ("David for both successor roles")
    # and modal appointments/declines ("Maya should not be my
    # healthcare agent") — never DECIDE an offer. Five rounds of
    # scoped-guessing parsers each manufactured a wrong fill or a
    # persisted decline from some natural phrasing; the deciders
    # are only the single-offer read, the anchor-free global read,
    # and pure name+verdict fragments. Everything else defers and
    # the offer is re-asked, where a terse reply resolves through
    # the trusted paths.
    message = (
        "Yes — David for both successor roles. "
        "No — Maya should not be my healthcare agent."
    )
    # Premise: the global read defers on this message (no bundle-ref
    # clause, head too long for the short-head fallback).
    assert _bundle_verdict(message) is None
    verdicts = _resolve_offer_verdicts([PRIMARY, BACKUP], message)
    assert verdicts == {}


def test_undecided_message_resolves_nothing():
    message = (
        "Let me think about it and get back to you on who should serve "
        "in each of these important roles for my family."
    )
    assert _bundle_verdict(message) is None
    assert _resolve_offer_verdicts([PRIMARY, BACKUP], message) == {}


def test_single_offer_never_uses_the_scoped_read():
    # Single-offer turns keep the exact pre-merge semantics: only the
    # global read decides. This message's scoped reads would decide
    # both ways; the global read defers, so nothing resolves.
    message = (
        "Yes — David for both successor roles. "
        "No — Maya should not be my healthcare agent."
    )
    assert _resolve_offer_verdicts([PRIMARY], message) == {}


# --- C-1 counterexamples (review round 1): differentiated replies must
# --- never be collapsed into one global verdict for every offer. -------


def test_short_mixed_reply_is_not_collapsed():
    # _bundle_verdict's short-head fallback reads the FIRST clause
    # ("No for Maya") — applying it to both offers would permanently
    # decline the backup the user accepted.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "No for Maya, yes for David."
    )
    assert verdicts == {"primary:maya lee": False, "backup:david lee": True}


def test_yes_for_one_anchor_never_fills_the_other():
    # "David should not." reduces to a PURE FRAGMENT (name +
    # negation token) — a v6 decider — and David's modal signal
    # agrees, so David is declined. The round-2 property stands:
    # the leading "Yes" for Maya's capability statement never
    # fills Maya, and never leaks into David's read either.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, Maya can serve, but no, David should not."
    )
    assert verdicts == {"backup:david lee": False}


def test_bundle_ref_sentence_for_one_anchor_does_not_fill_the_other():
    # V6: per-anchor bundle-ref statements are signals, not
    # deciders — both defer; crucially neither fills the OTHER.
    message = (
        "Yes — Maya should also serve as healthcare agent. "
        "No — David should not serve as successor."
    )
    verdicts = _resolve_offer_verdicts([PRIMARY, BACKUP], message)
    assert verdicts == {}


def test_global_no_with_per_anchor_exceptions():
    message = "No, not the same person for all of those roles — David yes, Maya no."
    verdicts = _resolve_offer_verdicts([PRIMARY, BACKUP], message)
    assert verdicts == {"primary:maya lee": False, "backup:david lee": True}


def test_undifferentiated_yes_still_resolves_everything():
    # The cooperative shape the merged presentation is built around:
    # bundle-ref language, no anchor named — every offer resolves.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, please — that works for all of those."
    )
    assert verdicts == {"primary:maya lee": True, "backup:david lee": True}


# --- I-1: scoped markers must not collide ---------------------------------


def test_same_given_name_anchors_require_full_names():
    primary = _offer("primary", "Priya Sharma")
    backup = _offer("backup", "Priya Nair")
    # Designations / modal statements defer under the v6 contract…
    message = (
        "Yes — Priya Sharma for the primary roles. "
        "No — Priya Nair should not serve as successor."
    )
    assert _resolve_offer_verdicts([primary, backup], message) == {}
    # …while pure fragments carrying the FULL name decide, and a
    # bare given name shared by two anchors decides nobody.
    verdicts = _resolve_offer_verdicts(
        [primary, backup], "Priya Sharma yes. Priya Nair no."
    )
    assert verdicts == {
        "primary:priya sharma": True,
        "backup:priya nair": False,
    }
    assert _resolve_offer_verdicts([primary, backup], "Priya yes.") == {}


def test_address_offer_not_decided_by_a_sentence_naming_a_person():
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "No — Maya should not use my Main Street address."
    verdicts = _resolve_offer_verdicts([PRIMARY, address], message)
    assert "address:123 main street, austin, texas" not in verdicts


def test_address_offer_decided_by_an_address_only_sentence():
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "No, don't use my address for any of them."
    verdicts = _resolve_offer_verdicts([BACKUP, address], message)
    assert verdicts.get("address:123 main street, austin, texas") is False


# --- Round-2 review counterexamples: the scoped/inheritance layer must
# --- never manufacture a verdict from another person's sentence. -------


def test_leading_verdict_for_a_non_candidate_does_not_fill_a_candidate():
    # The read must not inherit _bundle_verdict's first-clause
    # bias: "Yes" belongs to Carlos's clause, not Maya's. Maya is
    # decided only by her own pure fragment ("Maya should not."
    # -> name + negation token), never by Carlos's affirmative.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, Carlos can serve, but no, Maya should not."
    )
    assert verdicts == {"primary:maya lee": False}


def test_unrecognized_person_blocks_global_inheritance():
    # Carlos is not a candidate: the reply is not undifferentiated,
    # so nobody inherits its verdict.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, Carlos should serve in all of those roles."
    )
    assert verdicts == {}


def test_bare_surname_with_two_holders_decides_nobody():
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, Lee can serve."
    )
    assert verdicts == {}
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "No, Lee should not serve."
    )
    assert verdicts == {}


def test_full_name_matching_survives_punctuation_variance():
    primary = _offer("primary", "Will K. Smith")
    # The marker-matching property this test exists for,
    # exercised through a v6 decider (a pure fragment): the
    # anchor "Will K. Smith" matches the text "Will K Smith".
    verdicts = _resolve_offer_verdicts([primary, BACKUP], "Will K Smith yes.")
    assert verdicts == {"primary:will k. smith": True}


def test_middle_name_omission_decides_nobody():
    primary = _offer("primary", "Maya Ann Lee")
    backup = _offer("backup", "Maya Rose Lee")
    verdicts = _resolve_offer_verdicts(
        [primary, backup], "Maya Lee can serve for all of those roles, yes."
    )
    assert verdicts == {}


def test_explicit_deferral_is_not_a_decline():
    message = (
        "Yes for Maya. No decision on David yet, let me think about "
        "the successor roles and get back to you soon."
    )
    verdicts = _resolve_offer_verdicts([PRIMARY, BACKUP], message)
    assert verdicts == {"primary:maya lee": True}


def test_relationship_correction_is_not_a_decline():
    primary = _offer("primary", "Priya Sharma")
    backup = _offer("backup", "David Lee")
    message = "Priya is not only executor but also my sister."
    assert _resolve_offer_verdicts([primary, backup], message) == {}
    assert (
        _resolve_offer_verdicts([primary, backup], "Priya is not my sister.")
        == {}
    )
    message = (
        "Maya is not my sister, she is my cousin. "
        "I have not decided on the roles yet."
    )
    assert _resolve_offer_verdicts([PRIMARY, BACKUP], message) == {}


def test_details_sentence_does_not_unlock_inheritance():
    message = (
        "Yes, Maya Lee should also serve in all of those roles for our "
        "family. For all of those roles, the contact details are on "
        "file already."
    )
    primary = _offer("primary", "Maya Lee")
    # V6: Maya's appointment statement is a signal (she defers);
    # the property this test exists for is unchanged — the
    # details sentence never unlocks a global fill for David.
    verdicts = _resolve_offer_verdicts([primary, BACKUP], message)
    assert verdicts == {}


def test_address_sentence_decides_neither_person_nor_address_offer():
    address = _offer("address", "123 Main Street, Austin, Texas")
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, address], "No, don't use Maya's address."
    )
    assert verdicts == {}


def test_clause_naming_two_anchors_assigns_no_verdict():
    assert (
        _resolve_offer_verdicts([PRIMARY, BACKUP], "Maya yes alongside David.")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, BACKUP], "Maya yes David.")
        == {}
    )


# --- Round-3 review: same-anchor conflicts defer; factual owned
# --- clauses are not verdicts; judging never uses the head fallback. --


def test_retraction_defers_instead_of_first_wins():
    assert (
        _resolve_offer_verdicts(
            [PRIMARY, BACKUP], "Maya yes. Actually no, Maya should not serve."
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [PRIMARY, BACKUP], "Maya no. Actually yes, Maya can serve."
        )
        == {}
    )


def test_factual_owned_clause_is_not_a_verdict():
    # "does not <fact>" is a biographical predicate, not a decline —
    # no read may run _parse_yes_no's head patterns over extracted
    # clauses. Under v6 the companion acceptances also defer
    # (capability statements are signals at most): the core
    # property is that NOBODY is decided by these messages — in
    # particular Maya is never declined by her own facts.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Maya does not live in Texas anymore. Yes, David can serve."
    )
    assert verdicts == {}
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Maya does not drive. Yes, David can serve."
    )
    assert verdicts == {}
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Maya and I do everything together. No, David should not serve."
    )
    assert verdicts == {}


def test_contraction_opener_does_not_block_inheritance():
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, I'm fine with all of those."
    )
    assert verdicts == {"primary:maya lee": True, "backup:david lee": True}


def test_fact_ride_along_defers_inheritance():
    # Round-9 F1 settlement: inheritance requires a WHOLLY
    # cooperative head block. A substantive fact clause ("we
    # moved to Texas") makes the reply differentiated, so the
    # bundle verdict defers to a re-ask — the bounded versions
    # of this gate (token caps) let any longer name through as
    # a manufactured fill. The round-6 property is preserved a
    # fortiori: a factual sentence is never inherited as a
    # verdict, and now not alongside one either.
    verdicts = _resolve_offer_verdicts(
        [PRIMARY, BACKUP], "Yes, we moved to Texas, and that works for all of those."
    )
    assert verdicts == {}


# --- Round-4 review: R3 groups by SENTENCE (not owner-set);
# --- ownerless retraction sentences contaminate; the modal gate
# --- needs a bundle-scoped object; plain appointments register.
# --- Fixtures here carry REAL fill_keys — the role vocabulary the
# --- gate reads only exists then (the [] fixtures hid C-R4-4). ----


def _real_offer(bundle, anchor_name, fill_keys):
    offer = _offer(bundle, anchor_name)
    offer["fill_keys"] = list(fill_keys)
    return offer


REAL_PRIMARY = _real_offer(
    "primary", "Maya Lee", ["poa_agent_person", "healthcare_agent_person"]
)
REAL_BACKUP = _real_offer(
    "backup",
    "David Lee",
    [
        "successor_poa_agent_person",
        "successor_healthcare_agent_person",
        "successor_trustee_person",
    ],
)


def test_bare_sourced_retraction_across_sentences_defers():
    for message in (
        "Yes, Maya can serve. No, Maya should not serve.",
        "Yes, Maya can serve. Actually no, Maya should not serve.",
        "No, Maya should not serve. Yes, Maya can serve.",
        "No, Maya should not serve. Actually yes, Maya can serve.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_ownerless_retraction_sentence_defers_everything():
    for message in (
        "Yes, Maya can serve. Actually no, she should not serve.",
        "Yes, David can serve. Actually no, he should not serve.",
        "Maya yes. No.",
        "Maya yes. Actually no.",
        "No, Maya should not serve. Actually yes, she can serve.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_modal_gate_needs_a_bundle_scoped_object():
    # V6: the factual Maya clauses manufacture nothing (the v5
    # property), and the companion statements are signals/fragments
    # only — modal companions defer ({}), a pure fragment decides.
    cases = (
        (
            "Maya will definitely handle the taxes this year. "
            "No, David should not serve.",
            {},
        ),
        (
            "Maya will not handle the taxes anymore. Yes, David can serve.",
            {},
        ),
        (
            "Maya should not use the car this weekend. Yes, David can serve.",
            {},
        ),
        (
            "Maya will never serve on the school board again. "
            "Yes, David can serve.",
            {},
        ),
        (
            "Maya can not serve food at the reception. Yes, David can serve.",
            {},
        ),
        (
            "Maya should definitely serve dinner at seven. David yes.",
            {"backup:david lee": True},
        ),
    )
    for message, expected in cases:
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message)
            == expected
        ), message


def test_role_phrases_do_not_collide_with_ordinary_senses():
    # V6: ordinary-sense role words manufacture nothing for Maya
    # or David; the companions are signals at most and defer too.
    cases = (
        (
            "Maya should definitely call her real estate agent tomorrow. "
            "No, David should not serve.",
            {},
        ),
        (
            "Maya should definitely call her financial advisor tomorrow. "
            "No, David should not serve.",
            {},
        ),
        (
            "Maya will not call her agent today. Yes, David can serve.",
            {},
        ),
        (
            "David should definitely call the trustee bank tomorrow. "
            "Yes, Maya can serve.",
            {},
        ),
        (
            "David will not discuss his financial plans today. "
            "Yes, Maya can serve.",
            {},
        ),
    )
    for message, expected in cases:
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message)
            == expected
        ), message


def test_appointment_statements_defer():
    # V6 contract: an appointment statement is a signal, never a
    # decider — against a stray bare verdict (round-4 C-R4-5) and
    # on its own (the v5 positive controls are deliberately
    # superseded: the appointment read was my v4 design choice,
    # never a bar requirement, and five review rounds showed
    # every deciding read over free text eventually manufactures
    # a wrong fill or a persisted decline).
    assert (
        _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "No, David should serve.")
        == {}
    )
    assert (
        _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "No, Maya should serve.")
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "No, Maya should also serve in all of those roles.",
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "David should serve as successor trustee."
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "Maya should be my healthcare agent."
        )
        == {}
    )


def test_identity_fact_is_not_a_bundle_reference():
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "Maya is the same person as the account holder, "
            "yes she confirmed it.",
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "Maya uses the same person for her taxes. Yes, Maya is great.",
        )
        == {}
    )


# --- Round-5 review / v6 contract: contamination gates EVERY
# --- path (including inheritance), contractions are expanded
# --- before token checks, and modal signals veto fragments. -----


def test_contraction_retractions_defer():
    for message in (
        "Yes, Maya can serve. She won't serve.",
        "Yes, Maya can serve. She can't serve.",
        "Yes, Maya can serve. She shouldn't serve.",
        "Yes, Maya can serve. She will not serve.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_retraction_phrases_defer():
    for message in (
        "Yes, Maya can serve. Not her.",
        "Yes, Maya can serve. Scratch that.",
        "Yes, Maya can serve. Never mind.",
        "Maya yes. Wait — scratch that, not her.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_contamination_gates_inheritance():
    # The veto is message-level: an anchor-free global read may
    # not resurrect what a trailing retraction withdrew (C5-3).
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "Yes, that works for all of those. Actually no, she should not serve.",
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "Yes for all of those roles. No."
        )
        == {}
    )


def test_fragment_decides_only_when_signals_agree_or_absent():
    # A disagreeing modal signal vetoes the fragment.
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "Maya yes. Maya should not serve as executor.",
        )
        == {}
    )
    # An agreeing modal signal leaves the fragment's decision.
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP],
        "Maya yes. Maya should also serve as healthcare agent.",
    ) == {"primary:maya lee": True}


def test_third_person_reference_blocks_inheritance():
    # The anchor-free gate reads person-SHAPE (capitalization) —
    # a lowercase third person and a gendered pronoun each make
    # the reply differentiated even though no candidate marker
    # matches: "use her" speaks about ONE person, not both
    # bundles, and "use priya" names someone else entirely.
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "Yes, use priya for all of those roles."
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "Yes, use her for all of those roles."
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "Yes, use him for all of those."
        )
        == {}
    )
    # Control: the genuinely undifferentiated shapes still
    # resolve every offer at once.
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Yes, same person for all of those roles."
    ) == {"primary:maya lee": True, "backup:david lee": True}


def test_serve_in_fact_decides_nobody():
    # C5-4 shapes: under v6 these are signals at most — the
    # family-business decline is NOT persisted, the army
    # affirmation is NOT filled.
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "Maya would never serve in our family business.",
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "Maya will definitely serve in the army."
        )
        == {}
    )


# --- Round-6 review: four narrow decider/veto gaps. --------------


def test_name_like_allowlist_tokens_block_inheritance():
    # C1: "will"/"good"/"fine" are residue-allowlisted AND names.
    # In a name slot (after "use", or clause-initial before
    # "for") they are a person reference, not bundle vocabulary.
    for message in (
        "Yes, use will for all of those roles.",
        "Yes, will for all of those.",
        "Yes, use good for all of those roles.",
        "Yes, use fine for all of those roles.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message
    # Control (also fixes round-6 M2): the verb phrase
    # "that will work for all of those" still resolves.
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Yes, that will work for all of those."
    ) == {"primary:maya lee": True, "backup:david lee": True}


def test_pronoun_retraction_at_sentence_level_defers():
    # C2: the retraction's pronoun and its negation need not
    # share a clause — contamination reads the whole sentence.
    for message in (
        "Maya yes. She has decided not to serve.",
        "Maya yes. She changed her mind and will not serve.",
        "Maya yes. She reconsidered and will not serve.",
        "Maya yes. She no longer serves.",
        "Maya yes. She is not serving.",
        "David yes. He has decided not to serve.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_interrogative_fragments_decide_nothing():
    # C3: a question is definitionally not a decision.
    assert _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "Maya yes?") == {}
    assert _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "David no?") == {}
    assert (
        _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "Is Maya yes?") == {}
    )


def test_address_facts_do_not_decline_the_address_offer():
    # C4: D2 requires an offer-directed frame (bundle ref, a
    # bare verdict clause, or a "use my/the/that address"
    # phrase) — a factual predicate about the address itself,
    # even with a bundle-action verb, decides nothing.
    address = _real_offer(
        "address", "123 Main Street, Austin, Texas", []
    )
    for message in (
        "The address does not handle mail anymore.",
        "The address does not serve as a mailing address.",
        "My address does not handle mail anymore.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, address], message) == {}
        ), message
    # The manufactured decline may not ride along with a
    # legitimate fragment either.
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, address],
        "Maya yes. The address does not handle mail anymore.",
    ) == {"primary:maya lee": True}
    # Nor may a short factual sentence be INHERITED as a decline
    # by every pending offer: multi-offer inheritance requires a
    # genuinely verdict-shaped reply (a bundle-reference clause,
    # or a head made only of verdict/bundle vocabulary).
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "The house does not have a garage."
        )
        == {}
    )
    # Offer-directed control still decides the address offer.
    assert _resolve_offer_verdicts(
        [REAL_BACKUP, address], "Please do not use my address."
    ) == {"address:123 main street, austin, texas": False}


# --- Round-7 review: residual evasions of the round-6 fixes. -----


def test_isolated_name_clause_blocks_inheritance():
    # C-R7-1: a comma can isolate the name into its own clause,
    # away from the bundle reference the C1 slot gate inspects.
    for message in (
        "Yes, will, for all of those.",
        "Yes, will, for all of those roles.",
        "Yes, for will, all of those roles.",
        "Yes, for all of those roles, will.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_lexical_retraction_verbs_defer():
    # C-R7-2: retractions with no negation token and no listed
    # phrase — declined / refused / withdrew / backed out / …
    for message in (
        "Maya yes. She declined to serve.",
        "Maya yes. She refused to serve.",
        "Maya yes. She withdrew.",
        "Maya yes. She changed her mind.",
        "Maya yes. She backed out.",
        "Maya yes. She opted out.",
        "Maya yes. Count her out.",
        "Maya yes. Please remove her.",
        "David yes. He withdrew.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_question_sentence_fragments_decide_nothing():
    # C-R7-3: the "?" skip is sentence-scoped — a fragment does
    # not decide inside any sentence that is (part of) a question.
    assert (
        _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "Is Maya yes, David no?")
        == {}
    )
    assert (
        _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], "Maya yes, David no?")
        == {}
    )
    # Control: a declarative fragment in its own sentence still
    # decides when only the OTHER sentence is a question.
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Maya yes. David no?"
    ) == {"primary:maya lee": True}


def test_address_read_is_clause_scoped_within_a_framed_sentence():
    # C-R7-4: framing is checked per CLAUSE — a factual clause
    # inside a framed sentence may not decline the offer.
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, address], "Use my address, it does not handle mail."
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, address],
            "Use my address for mail, but it does not handle packages.",
        )
        == {}
    )


# --- Round-8 review: structural versions of the round-7 fixes. ---


def test_any_isolated_name_clause_blocks_inheritance():
    # C-R8-1: the isolated-clause rule is not a name LIST — any
    # single non-cooperative content token standing alone in a
    # clause is a substantive reference (a name, capitalized or
    # not), whatever the name is.
    for message in (
        "Yes, priya, for all of those.",
        "Yes; carlos; for all of those.",
        "Yes — priya — for all of those.",
        "Yes, lee, for all of those.",
        "Yes, john, for all of those.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message
    # Assent adjectives standing alone are cooperative, not
    # names (round-8 M-R8-1): "Fine, for all of those." assents.
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Yes, fine, for all of those."
    ) == {"primary:maya lee": True, "backup:david lee": True}


def test_retraction_naming_the_offer_vetoes_its_fragment():
    # C-R8-2: a retraction sentence that NAMES the offer is not
    # contamination (it is differentiated) — it is a per-offer
    # signal vetoing that offer's fragment.
    for message in (
        "Maya yes. Maya withdrew.",
        "Maya yes. Maya declined to serve.",
        "Maya yes. Please remove Maya.",
        "David yes. Take David off.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_retraction_verb_stems_defer():
    # C-R8-3: stems, not surface forms — synonyms and base /
    # future forms retract just as past forms do.
    for message in (
        "Maya yes. She revoked it.",
        "Maya yes. She retracted.",
        "Maya yes. She cancelled.",
        "Maya yes. She pulled out.",
        "Maya yes. She wants out.",
        "Maya yes. She is out.",
        "Maya yes. Strike her.",
        "Maya yes. She will withdraw.",
        "Maya yes. She will refuse to serve.",
        "Maya yes. I withdraw that.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_address_and_fragment_interrogative_gates():
    # C-R8-4: the address decider has the same interrogative
    # gate as the others; fullwidth marks count; a hedged
    # reported-speech sentence decides no fragment.
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts(
            [REAL_BACKUP, address], "No, don't use my address for any of them?"
        )
        == {}
    )
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP], "I wonder if Maya yes, David no."
        )
        == {}
    )


def test_address_subordinate_fact_does_not_decline():
    # C-R8-5: a negation-bearing subordinate segment inside the
    # framed clause is severed before the read — the head of the
    # clause is what the user directed at the offer.
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, address], "Use my address that does not handle packages."
        )
        == {}
    )
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, address],
        "Yes, use my address that does not handle packages.",
    ) == {"address:123 main street, austin, texas": True}
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, address], "Use my address (it does not handle mail)."
        )
        == {}
    )


def test_bare_context_borrowing_is_affirmative_only():
    # C-R8-6: a leading "No" aimed at the person offers may not
    # be borrowed into the address read and flip an affirmative
    # instruction into a decline — the message defers instead.
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP, address],
            "No, use my address for all of those roles.",
        )
        == {}
    )


def test_retraction_phrases_are_token_bounded():
    # M-R8-2: "not her" must not match inside "not here", nor
    # "never mind" inside "never minded".
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Maya yes. The plan is not here."
    ) == {"primary:maya lee": True}
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Maya yes. The team never minded the noise."
    ) == {"primary:maya lee": True}


# --- Round-9 review: structural closures of the round-8 classes. ---


def test_isolated_name_clause_of_any_length_blocks():
    # F1: the isolated-clause gate has no token caps — any
    # substantive non-cooperative content is a reference.
    for message in (
        "Yes, priya ann lee, for all of those.",
        "Yes, for all of those, priya ann lee.",
        "Yes, dr priya lee, for all of those.",
        "Yes, my sister priya lee, for all of those.",
        "Yes, priya will serve, for all of those.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_serve_retraction_verbs_and_contraction_out():
    # F2: resign / quit / step aside / recuse / bow out are
    # canonical role retractions; "'s out" is "is out".
    for message in (
        "Maya yes. Maya resigned.",
        "Maya yes. She quit.",
        "Maya yes. She stepped aside.",
        "Maya yes. She recused herself.",
        "Maya yes. Maya's out.",
        "Maya yes. She's out.",
        "David yes. David resigned.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_dismissal_verbs_take_person_objects():
    # F3: dismissal verbs retract with person objects too —
    # the object slot is structural, not enumerated.
    for message in (
        "Maya yes. Nevermind.",
        "Maya yes. Forget that.",
        "Maya yes. Forget her.",
        "Maya yes. Scratch her.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_hedged_and_reported_fragments_do_not_decide():
    # F4: wondering/whether frames, unpunctuated interrogative
    # word order, attribution, and quotation all keep fragments
    # from deciding; a mid-sentence ¿ scopes to its clause.
    for message in (
        "I was wondering whether Maya yes, David no.",
        "Is Maya yes, David no",
        "He said Maya yes, David no.",
        '"Maya yes, David no," she said.',
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Maya yes, ¿David no."
    ) == {"primary:maya lee": True}


def test_address_sever_covers_causal_subordinators():
    # F5: the sever class is subordination itself, not five
    # words — causal/temporal subordinators and a jammed
    # second predication sever the same way when the tail
    # carries a negation.
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, address],
        "Yes, use my address since it does not handle packages.",
    ) == {"address:123 main street, austin, texas": True}
    for message in (
        "Use my address because it does not handle packages.",
        "Use my address while it does not handle packages.",
        "Use my address it does not handle packages.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, address], message) == {}
        ), message


# --- Round-10 review: inversion of the round-9 gates. ---


def test_cooperative_token_does_not_launder_a_name_clause():
    # C1: a cooperative word predicated of a third person is
    # still a designation — a D4 clause must be WHOLLY
    # cooperative, not merely contain an assent token.
    for message in (
        "Yes, carlos works, for all of those roles.",
        "Yes, that works for priya, for all of those roles.",
        "Yes, priya is fine, for all of those.",
        "No, priya is fine, for all of those.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_retraction_backstop_for_named_predications():
    # C2: phrasal exit verbs are a verb-family × particle
    # product; capacity adjectives retract; and structurally,
    # ANY non-modal predication about exactly the candidate
    # defers its fragment (differentiated prose) — while an
    # agreeing modal confirmation, read first, still stands.
    for message in (
        "Maya yes. Maya stood down.",
        "Maya yes. Maya stepped back.",
        "Maya yes. Maya is unwilling to serve.",
        "Maya yes. Maya is not able to serve.",
        "Maya yes. Maya no longer wants to serve.",
        "Maya yes. Drop Maya.",
        "Maya yes. Maya is a nurse.",
        "Maya yes. She stood down.",
        "Maya yes. She bailed out.",
        "Maya yes. She gave up.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Maya yes. Maya can serve as executor."
    ) == {"primary:maya lee": True}


def test_dismissal_family_beyond_four_verbs():
    # C3: the dismissal register is a verb family, not four
    # verbs — ditch / skip / nix / cross-off retract the same
    # way forget / scratch do.
    for message in (
        "Maya yes. Ditch her.",
        "Maya yes. Skip her.",
        "Maya yes. Nix that.",
        "Maya yes. Cross her off.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_reported_frames_decide_nothing():
    # C4: evidentials, attribution stems, "seems", curious /
    # check-if frames, and single-quote / guillemet quoting
    # are all reported frames; the anchor-first opener
    # exception holds only when the sentence actually carries
    # that anchor's own fragment.
    for message in (
        "Apparently Maya yes, David no.",
        "He thinks Maya yes, David no.",
        "Seems like Maya yes, David no.",
        "I'm curious if Maya yes, David no.",
        "'Maya yes, David no.'",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message
    will_primary = _offer("primary", "Will K. Smith")
    assert (
        _resolve_offer_verdicts([will_primary, REAL_BACKUP], "Will David no")
        == {}
    )


def test_address_sever_jammed_predications_and_whose():
    # C5: the jammed second predication is detected by its
    # negated auxiliary, whatever its subject is; "whose" is
    # a relative like any other.
    address = _offer("address", "123 Main Street, Austin, Texas")
    for message in (
        "Use my address whose mailbox does not accept packages.",
        "Use my address mail does not arrive there.",
        "Use my address she does not live there anymore.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, address], message) == {}
        ), message
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, address],
        "Yes, use my address whose mailbox does not accept packages.",
    ) == {"address:123 main street, austin, texas": True}


# --- Round-11 review: closed-form versions of the five gates. ---


def test_name_in_subject_position_is_not_a_modal():
    # F1: "will works" / "may is fine" designate a person —
    # the token is a modal only after a subject ("that will
    # work"), never in subject position itself.
    for message in (
        "Yes, will works, for all of those roles.",
        "Yes, may is fine, for all of those.",
        "No, will works, for all of those roles.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Yes, that will work for all of those."
    ) == {"primary:maya lee": True, "backup:david lee": True}


def test_any_named_predication_defers_the_fragment():
    # F2: the named-clause signal is closed by construction —
    # a clause referencing exactly the offer is safe only as
    # the decider's own pure fragment or when the modal read
    # disposes of it; any other predication vetoes, whatever
    # verb form it takes.
    for message in (
        "Maya yes. Maya works nights.",
        "Maya yes. Maya lives in Ohio.",
        "Maya yes. Exclude Maya.",
        "Maya yes. Replace Maya.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_pronoun_particle_dismissals_contaminate():
    # F3: the dismissal idiom is <verb> + pronoun + off/out —
    # the verb slot is not enumerated.
    for message in (
        "Maya yes. Cut her off.",
        "Maya yes. Leave her out.",
        "Maya yes. Rule her out.",
        "Maya yes. Write her off.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_fragments_decide_only_in_bare_sentences():
    # F4: a fragment sentence must consist only of fragments
    # and bare verdicts — a frame clause ("According to
    # Priya", "I heard", "Looks like") makes it reported, and
    # curly single quotes quote like any other quotes.
    for message in (
        "According to Priya, Maya yes, David no.",
        "I heard Maya yes, David no.",
        "Looks like Maya yes, David no.",
        "‘Maya yes, David no.’",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_address_sever_before_the_frame():
    # F5: a jammed negated predication BEFORE the frame severs
    # the same way — unless the clause head is itself the
    # imperative frame ("Please do not use my address.").
    address = _offer("address", "123 Main Street, Austin, Texas")
    for message in (
        "It does not handle packages use my address.",
        "She does not live there anymore use my address.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, address], message) == {}
        ), message
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, address],
        "Yes, it does not handle packages use my address.",
    ) == {"address:123 main street, austin, texas": True}


# --- Round-12 review: adjacency/morphology residuals of the
# --- round-11 forms.


def test_preceder_exception_needs_a_base_form_verb():
    # C1: a modal reading needs a BASE-form verb after the
    # name token — "that will works" is ungrammatical as a
    # modal clause and grammatical only as a designation.
    for message in (
        "Yes, that will works for all of those roles.",
        "Yes, that will is fine for all of those.",
        "Yes, that will goes for all of those roles.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_joint_and_spliced_retractions_veto():
    # C2/C3: a retraction clause naming BOTH offers, and a
    # pronoun retraction spliced into a candidate sentence,
    # veto like any other named retraction.
    for message in (
        "Maya yes, David yes. Maya as well as David withdrew.",
        "Maya yes, she just resigned.",
        "David yes, he just resigned.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_pronoun_particle_with_intervening_material():
    # C4: the dismissal idiom tolerates intervening adverbs
    # and nouns between pronoun and particle.
    for message in (
        "Maya yes. Cut her completely off.",
        "Maya yes. Rule her right out.",
        "Maya yes. Take her name off.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_past_tense_attribution_is_a_frame():
    # C5: attribution frames do not expire with tense.
    for message in (
        "Priya suggested, Maya yes, David no.",
        "As Priya recommended, Maya yes, David no.",
        "Maya yes, David no, as Priya recommended.",
        "Maya yes, David no.’",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_borrow_reads_the_severed_tails_own_instruction():
    # C6: when the jammed fact precedes the frame, the tail's
    # OWN instruction decides — a bare "Yes" may not be
    # borrowed over an explicit "do not use".
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, address],
        "Yes, it does not handle packages do not use my address.",
    ) == {"address:123 main street, austin, texas": False}


def test_head_frame_variants_still_decline():
    # M1: the imperative-frame exception covers the framings
    # the address framer itself accepts (this/our determiners,
    # an intervening adverb).
    address = _offer("address", "123 Main Street, Austin, Texas")
    for message in (
        "Please do not ever use my address.",
        "Please do not use our address.",
        "Please do not use this address.",
    ):
        assert _resolve_offer_verdicts(
            [REAL_PRIMARY, address], message
        ) == {"address:123 main street, austin, texas": False}, message


# --- Round-13 review: residuals of the round-12 forms. ---


def test_joint_affirmative_modal_vetoes_fragment_declines():
    # CR1: a joint affirmative modal is an affirmative signal
    # for each named offer — it must veto stale fragment
    # declines, not be misread as a negative signal.
    assert (
        _resolve_offer_verdicts(
            [REAL_PRIMARY, REAL_BACKUP],
            "Maya no, David no. Actually, Maya as well as David should serve.",
        )
        == {}
    )


def test_spliced_pronoun_modal_negation_vetoes():
    # CR2/CR3: the spliced-retraction form includes pronoun +
    # modal negation, the "they" pronoun, a retraction sentence
    # in first position, and subject ellipsis.
    for message in (
        "Maya yes, she will not serve.",
        "David yes, he cannot serve.",
        "Maya yes, they just resigned.",
        "She just resigned. Maya yes, David yes.",
        "Maya yes, just resigned.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_pronoun_particle_wider_intervening_and_reflexive():
    # CR4: up to four intervening tokens; reflexive pronouns
    # fill the pronoun slot.
    for message in (
        "Maya yes. Take her full legal name off.",
        "Maya yes. Cut her completely and totally off.",
        "Maya yes. Cut herself off.",
        "Maya yes. She took herself out.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_attribution_frames_across_sentences_and_verbs():
    # CR5/CR6/CR7: a frame-only sentence adjacent to the
    # fragment sentence scopes over it; finite attribution
    # verbs are frames whatever their stem; a trailing
    # straight quote quotes.
    for message in (
        "Maya yes, David no. As Priya recommended.",
        "Maya yes, David no. Priya suggested it.",
        "Priya proposed, Maya yes, David no.",
        "Priya endorsed, Maya yes, David no.",
        "Maya yes, David no.'",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_tail_frame_adverbs_and_passive():
    # CR8/CR9: the severed tail's own instruction is read with
    # its adverb allowance, and a passive instruction in the
    # tail decides by its own polarity — never by a borrowed
    # bare "Yes".
    address = _offer("address", "123 Main Street, Austin, Texas")
    for message in (
        "Yes, it does not handle packages do not ever use my address.",
        "Yes, it does not handle packages my address should not be used.",
    ):
        assert _resolve_offer_verdicts(
            [REAL_PRIMARY, address], message
        ) == {"address:123 main street, austin, texas": False}, message


def test_base_form_and_head_frame_allowances():
    # MN1/MN3: "will do / will serve" are cooperative modal
    # clauses; the imperative frame tolerates up to three
    # intervening tokens and an adjective before "address".
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP], "Yes, that will do for all of those roles."
    ) == {"primary:maya lee": True, "backup:david lee": True}
    assert _resolve_offer_verdicts(
        [REAL_PRIMARY, REAL_BACKUP],
        "Yes, that will serve for all of those roles.",
    ) == {"primary:maya lee": True, "backup:david lee": True}
    address = _offer("address", "123 Main Street, Austin, Texas")
    for message in (
        "Please do not at all use my address.",
        "Please never ever use my address.",
        "Please do not use my home address.",
    ):
        assert _resolve_offer_verdicts(
            [REAL_PRIMARY, address], message
        ) == {"address:123 main street, austin, texas": False}, message


# --- Round-14 review: the flip's own manufacturing, and the
# --- remaining adjacency caps.


def test_affirmative_modal_clauses_signal_their_polarity():
    # CR14-1/CR14-2: a modal clause the scoped read cannot
    # dispose still signals its own polarity — affirmative
    # modals veto stale declines (incl. via conjunction-split
    # name clauses and joint forms), and a negation governing
    # an EXCLUSION predicate is an inclusion.
    for message in (
        "Maya no, David no. Maya and David should serve.",
        "David no. David will serve.",
        "David no. David can serve.",
        "Maya no, David no. Maya as well as David should not be ruled out.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_subjectless_retractions_with_tails_and_capitals():
    # CR14-3: subject ellipsis survives bundle-ref tails, and
    # a sentence-initial capitalized VERB is not a person.
    for message in (
        "Maya yes, will not serve in any of those roles.",
        "Maya yes, just resigned from all of those roles.",
        "Will not serve. Maya yes, David yes.",
        "Resigned last week. Maya yes, David yes.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_particle_cap_and_frame_and_quote_residuals():
    # CR14-4/5/7: five intervening tokens still dismiss; a
    # long third-person attribution sentence is still a
    # frame; a quote before the final period still quotes.
    for message in (
        "Maya yes. Take her full legal name and title off.",
        "Maya yes, David no. Priya suggested it to me when we talked yesterday.",
        "Maya yes, David no'.",
        "Maya yes, David no’.",
    ):
        assert (
            _resolve_offer_verdicts([REAL_PRIMARY, REAL_BACKUP], message) == {}
        ), message


def test_passive_and_adjective_address_tails():
    # CR14-6 + MN14-1/2: passive tails with "never" and with
    # two adjectives decide by their own polarity; the
    # two-adjective imperative declines; a bare affirmative
    # use-instruction (no sever) accepts.
    address = _offer("address", "123 Main Street, Austin, Texas")
    for message in (
        "Yes, it does not handle packages my address should never be used.",
        "Yes, it does not handle packages my old home address should not be used.",
        "Please do not use my old home address.",
    ):
        assert _resolve_offer_verdicts(
            [REAL_PRIMARY, address], message
        ) == {"address:123 main street, austin, texas": False}, message
    for message in ("Use my address.", "Please use my address."):
        assert _resolve_offer_verdicts(
            [REAL_PRIMARY, address], message
        ) == {"address:123 main street, austin, texas": True}, message


# --- I-2: the verdict turn's "then ask" set excludes resolving fills -----


def test_gap_without_keys_filters_and_empties():
    gap = {
        "cluster": "people",
        "clusters": ["people"],
        "questions": [
            {"key": "primary_executor_address", "label": "Executor address?", "type": "text"},
            {"key": "backup_executor_person", "label": "Backup executor?", "type": "person"},
        ],
    }
    filtered = interview_service._gap_without_keys(
        gap, {"primary_executor_address"}
    )
    assert filtered is not None
    assert [q["key"] for q in filtered["questions"]] == ["backup_executor_person"]
    assert (
        interview_service._gap_without_keys(
            gap, {"primary_executor_address", "backup_executor_person"}
        )
        is None
    )
    assert interview_service._gap_without_keys(None, {"x"}) is None


# --- next_gap_focus (L1 merge / L3 batching / L4 sweep) ---------------------------

IDENTITY_ANSWERS = {
    "testator_full_name": "Arjun Sharma",
    "marital_status": "married",
    # A married identity is complete only once the spouse is named
    # (spouse_full_name applies_if marital_status married/partnered).
    "spouse_full_name": "Neha Kapoor",
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


def test_focus_matches_selector_when_first_cluster_is_large():
    sets = _v2_sets()
    focus = next_gap_focus(sets, {})
    gap = next_gap_cluster(sets, {})
    assert focus is not None and gap is not None
    assert focus["cluster"] == "identity"
    assert focus["clusters"] == ["identity"]
    # Phase 4 (P2, brief 2026-10-26): the focus is the HEAD of the
    # first cluster, capped at FOCUS_MAX_QUESTIONS pieces — a large
    # cluster splits across consecutive turns instead of asking every
    # field at once (this test previously pinned the uncapped ask).
    from app.domains.interview.service import FOCUS_MAX_QUESTIONS

    assert [q["key"] for q in focus["questions"]] == [
        q["key"] for q in gap["questions"]
    ][:FOCUS_MAX_QUESTIONS]
    assert not focus.get("sweep")


def test_focus_merges_small_first_cluster_with_the_next():
    focus = next_gap_focus(_v2_sets(), dict(IDENTITY_ANSWERS))
    assert focus is not None
    assert focus["clusters"] == ["children", "money"]
    keys = [q["key"] for q in focus["questions"]]
    assert "has_minor_children" in keys
    assert "asset_categories_present" in keys
    assert "has_retirement_accounts" in keys


def test_focus_sweeps_when_six_or_fewer_remain():
    sets = _v2_sets()
    skip = {"trust_distribution_age", "organ_donation_wishes", "residuary_beneficiaries"}
    answers: dict = {}
    for _ in range(60):
        gap = next_gap_cluster(sets, answers)
        if gap is None:
            break
        progressed = False
        for q in gap["questions"]:
            if q["key"] in skip:
                continue
            answers[q["key"]] = True if q["type"] == "boolean" else "x"
            progressed = True
        if not progressed:
            # Only skipped keys remain in this cluster: park them with
            # a temporary answer so the drain can move past the
            # cluster; they are removed again before the focus is
            # computed (none of the three gates anything).
            for q in gap["questions"]:
                answers[q["key"]] = "x"
    for key in skip:
        answers.pop(key, None)
    focus = next_gap_focus(sets, answers)
    assert focus is not None and focus.get("sweep") is True
    assert {q["key"] for q in focus["questions"]} == skip
    assert focus["clusters"] == ["wishes", "agents", "trust"]


def test_focus_none_when_complete():
    sets = _v2_sets()
    answers: dict = {}
    for _ in range(40):
        gap = next_gap_cluster(sets, answers)
        if gap is None:
            break
        for q in gap["questions"]:
            answers[q["key"]] = True if q["type"] == "boolean" else "x"
    assert next_gap_focus(sets, answers) is None


def test_focus_for_turn_flag_paths(monkeypatch):
    pack_v2 = get_pack("US-VA", "2.0.0")
    pack_v1 = get_pack("US-VA", "0.2.0")
    monkeypatch.setattr(
        interview_service.feature_flags,
        "is_enabled",
        lambda conn, key, *, firm_id=None, pack_id=None: False,
    )
    assert (
        interview_service._focus_for_turn(
            object(), firm_id=uuid4(), pack=pack_v2, answers={}
        )
        is None
    )
    monkeypatch.setattr(
        interview_service.feature_flags,
        "is_enabled",
        lambda conn, key, *, firm_id=None, pack_id=None: True,
    )
    focus = interview_service._focus_for_turn(
        object(), firm_id=uuid4(), pack=pack_v2, answers={}
    )
    assert focus is not None and focus["cluster"] == "identity"
    # v1 pack has no cluster fields: graceful None, never a crash.
    assert (
        interview_service._focus_for_turn(
            object(), firm_id=uuid4(), pack=pack_v1, answers={}
        )
        is None
    )


# --- prompt v7 -------------------------------------------------------------------

ROLE_OFFER = {
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

ADDRESS_OFFER = {
    "bundle": "address",
    "anchor_key": "testator_street_address",
    "anchor_name": "123 Main Street, Austin, Texas",
    "fill_keys": ["primary_executor_address", "poa_agent_address"],
    "fill_labels": ["Executor address?", "POA address?"],
    "fill_value": "123 Main Street, Austin, Texas",
    "decline_key": "address:123 main street, austin, texas",
}

_ROLE_OFFER_SHA256 = "96c17ccac14235b64998400d3022cb959114e0d0350ada1a5179c0a841f2c9e2"
_ADDRESS_OFFER_SHA256 = "b590ca22eb2d461fd600c6db3bf49debf18ce46e9b9f492532f9d59b695b0826"


def _prompt(**kwargs):
    base = dict(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        missing_required_keys=["poa_agent_person"],
        answered_required=20,
        total_required=41,
        pending_proposal_keys=[],
        answered_keys=[],
    )
    base.update(kwargs)
    return prompts.build_system_prompt(**base)


def test_single_offer_prompt_is_byte_identical_to_v6():
    # The v6 single-offer first-presentation block must not drift: the
    # hashes were captured on the pre-iteration code (ebc9ee4).
    text = _prompt(bundle_offer=dict(ROLE_OFFER))
    assert hashlib.sha256(text.encode()).hexdigest() == _ROLE_OFFER_SHA256
    text = _prompt(bundle_offer=dict(ADDRESS_OFFER))
    assert hashlib.sha256(text.encode()).hexdigest() == _ADDRESS_OFFER_SHA256
    # Passing the same single offer via the new list parameter renders
    # the identical prompt.
    text = _prompt(bundle_offers=[dict(ROLE_OFFER)])
    assert hashlib.sha256(text.encode()).hexdigest() == _ROLE_OFFER_SHA256


def test_merged_offers_prompt_presents_every_question_verbatim():
    primary = dict(ROLE_OFFER)
    primary["question_text"] = _bundle_question_text(primary)
    address = dict(ADDRESS_OFFER)
    address["question_text"] = _bundle_question_text(address)
    text = _prompt(bundle_offers=[primary, address])
    assert primary["question_text"] in text
    assert address["question_text"] in text
    assert "ONE message" in text
    # Still-needed spans both offers' fill keys.
    assert "poa_agent_person" in text
    assert "primary_executor_address" in text


def test_verdict_turn_prompt_does_not_reask():
    offer = dict(ROLE_OFFER)
    offer["presented_before"] = True
    offer["question_text"] = _bundle_question_text(offer)
    next_gap = {
        "cluster": "agents",
        "clusters": ["agents"],
        "questions": [
            {"key": "healthcare_agent_phone", "label": "What is your healthcare agent's phone number?", "type": "text"},
        ],
    }
    text = _prompt(bundle_offers=[offer], next_gap=next_gap)
    assert "Do NOT ask it again" in text
    assert "What is your healthcare agent's phone number?" in text
    assert "Ask ONE grouped yes/no question" not in text


def test_v7_cluster_prompt_asks_every_focus_field_as_a_list():
    gap = {
        "cluster": "children",
        "clusters": ["children", "money"],
        "questions": [
            {"key": "has_minor_children", "label": "Do you have minor children?", "type": "boolean"},
            {"key": "asset_categories_present", "label": "Which of these do you have?", "type": "multi_select"},
        ],
    }
    text = _prompt(cluster_gap=gap)
    assert "CLUSTER FOCUS: children + money" in text
    assert "Do you have minor children?" in text
    assert "Which of these do you have?" in text
    assert "numbered list" in text
    assert "up to 4" not in text


def test_v7_sweep_prompt_names_the_endgame():
    gap = {
        "cluster": "agents",
        "clusters": ["agents", "trust"],
        "sweep": True,
        "questions": [
            {"key": "organ_donation_wishes", "label": "Organ donation?", "type": "text"},
        ],
    }
    text = _prompt(cluster_gap=gap)
    assert "only items still needed" in text


def test_prompt_version_v7_registered_and_v6_unchanged():
    assert prompts.PROMPT_TEMPLATE_VERSION == "v6"
    assert prompts.PROMPT_TEMPLATE_VERSION_V7 == "v7"


# --- Round-16 review fixes (CR15-1..9, MN15-1) --------------------------------
# Severity rule (standing): a WRONG verdict persisting is Critical;
# a lost verdict (defer / re-ask) is Minor. Every case below was a
# persisting wrong verdict in round 15, each defeating a round-14
# mechanism with punctuation, a parenthetical, one extra word, or a
# cap/stem boundary the fix itself introduced.

import pytest as _pytest


@_pytest.mark.parametrize(
    "message",
    [
        # CR15-1: the negation governing the offer's modal sits in
        # a sibling clause of the same sentence (split off by
        # commas, dashes, or a semicolon) — polarity is
        # sentence-scoped, not clause-scoped.
        "David yes. David will, in the end, not serve.",
        "Maya yes. Maya can, if she wants to, not serve.",
        "David yes. David will — in the end — not serve.",
        "David yes. David will serve; not in those roles.",
        "David yes. David will serve, not in those roles though.",
        # CR15-2: the exclusion flip is sentence-scoped too — an
        # insertion between the negation and the exclusion
        # predicate must not leave name clauses inheriting a
        # False sentence polarity.
        "Maya no, David no. Maya and David should not be, in my view, ruled out.",
        "David no. David should not, under any circumstances, be left out.",
        "Maya no, David no. Maya and David should not be overlooked.",
        # CR15-3: subjectless retraction sentences — phrasal /
        # unable / retired stems at sentence start, and time
        # adverbials pushing listed stems past the old token cap.
        "Backed out. Maya yes, David yes.",
        "Bowed out. Maya yes, David yes.",
        "Gave up. Maya yes, David yes.",
        "Stands down. Maya yes, David yes.",
        "Walked away last week. Maya yes, David yes.",
        "Unable to serve. Maya yes, David yes.",
        "Resigned a few weeks ago. Maya yes, David yes.",
        "Will not serve any longer. Maya yes, David yes.",
        "Declined two weeks ago today. Maya yes, David yes.",
        "Stepped aside a while ago. Maya yes, David yes.",
        "Retired last year. Maya yes, David yes.",
        # CR15-4: a deliberation marker inside REPORTED speech is
        # the reporter's, not the user's — the attribution frame
        # still scopes over the adjacent verdict sentence.
        "Maya yes, David no. As Priya said I should choose.",
        # CR15-6: pronoun-particle dismissal with seven
        # intervening tokens (the old cap was six).
        "Maya yes. Take her full legal name and official professional title off.",
        # CR15-7: a pronoun clause spliced into the OTHER offer's
        # sentence — the pronoun's referent is undecidable, so
        # nothing in the message persists.
        "Maya yes. David should serve, and she should not.",
        "David no. Maya should serve, and he will serve.",
        # CR15-8: a quote before terminal punctuation other than
        # a period still marks reported fragments.
        "Maya yes, David no’!",
        "Maya yes, David no'!",
        "Maya yes, David no’;",
    ],
)
def test_round16_persisting_wrong_verdicts_defer(message):
    assert _resolve_offer_verdicts([PRIMARY, BACKUP], message) == {}


def test_round16_address_conditional_instruction_defers():
    # CR15-9: a conditional use-instruction is not an
    # unconditional accept — the bare-instruction rule needs a
    # conditional gate (the person path already defers these).
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], "Use my address (if you need one).") == {}
    assert _resolve_offer_verdicts([PRIMARY, address], "Use my address if needed.") == {}


def test_round16_address_three_adjective_passive_tail_decides_decline():
    # CR15-5: three adjectives defeat the {0,2} tail frame, then
    # bare-"Yes" borrowing flipped an explicit passive decline
    # into an accept.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = (
        "Yes, it does not handle packages my old primary home "
        "address should not be used."
    )
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": False
    }


@_pytest.mark.parametrize(
    "message,expected",
    [
        # MN15-1: direct address verdicts with >=3 adjectives
        # were lost entirely (deferred) by the {0,2} slots.
        ("Please do not use my old primary home address.", False),
        ("Please do not use my old primary family home address.", False),
        ("My old primary home address should not be used.", False),
        ("Use my old primary home address.", True),
        (
            "Yes, it does not handle packages do not use my old "
            "primary home address.",
            False,
        ),
    ],
)
def test_round16_address_wide_adjective_slots_decide(message, expected):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": expected
    }


# --- Round-17 review fixes (CR16-1..5, MN16-1) --------------------------------
# The round-16 unsevered passive read was an UNGATED decider: it
# persisted conditional, reported-speech, and contrastive
# passives. The passive read now carries the same discipline as
# every other decider — sentence-level hedge + conditional
# gates, a single-clause scope, and polarity read from the
# matched frame only, never the clause remainder.

import pytest as _pytest17


@_pytest17.mark.parametrize(
    "message",
    [
        # CR16-1: conditional passives are not unconditional
        # accepts.
        "My address should be used if you need it.",
        "My address should be used only if you need it.",
        "My address should be used unless the other one works.",
        "My address should be used when you need it.",
        "Yes, my address should be used if it helps.",
        # CR16-2: reported speech is not the user's instruction.
        "He said the address should be used.",
        "He said my address should be used.",
        "She said my address should not be used.",
        "She told me the address should not be used.",
        "They said the address should be used.",
        "It says the address should not be used.",
        "The letter says my address should not be used.",
        # CR16-3 (scope-qualified half): a purpose-scoped
        # factual passive must not override the explicit
        # instruction in the same sentence — combined reads
        # defer.
        "Use my address, the address should not be used for packages.",
        # CR16-4: a third person's address is not the offer —
        # third-person possessives never unblock the scoped
        # read.
        "His address should be used.",
        "His old home address should be used.",
        "Her address should be used.",
        "Her old home address should be used.",
        "Their old home address should be used.",
        # CR16-5: conditional paraphrases of the bare
        # use-instruction — the gate is a form class, not four
        # tokens.
        "Use my address in case you need one.",
        "Use my address in case you need it.",
        "Please use my address in case of emergency.",
        "Use my address as needed.",
        "Use my address as long as you need it.",
        "Use my address should you need one.",
        "Use my address provided you need one.",
    ],
)
def test_round17_address_ungated_reads_defer(message):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


def test_round17_address_contrastive_passive_affirms():
    # CR16-3 (contrastive half): polarity comes from the
    # matched passive frame only — a trailing contrastive
    # "not" later in the clause does not flip an affirmation.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "My address should be used not for mail but for identification."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": True
    }


def test_round17_address_passive_with_adverbial_negation():
    # MN16-1: "not ever be used" — an adverb between the
    # negation and "be used" still reads as the decline.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "My address should not ever be used."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": False
    }


# --- Round-18 review fixes (CR17-1..3) ----------------------------------------
# The bare-affirmative rule must require an actual use-
# instruction in the clause (a statement that the address WAS
# used is a fact, not consent); bare-"Yes" borrowing must not
# short-circuit a later framed clause carrying its own verdict
# or negation; and a second negation inside the passive
# negation slot defers instead of reading as a single decline.

import pytest as _pytest18


@_pytest18.mark.parametrize(
    "message",
    [
        # CR17-1: factual "address … used" clauses are facts,
        # not consent — the round-6 C4 class re-entering through
        # the used-frame and the bare-affirmative rule.
        "My address was used last year.",
        "My address is used for packages.",
        "The address on file last year was used.",
        "My address in the county records was used.",
    ],
)
def test_round18_address_fact_clauses_do_not_decide(message):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


def test_round18_address_fact_clause_does_not_block_person_fragment():
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "Maya yes. My address was used last year."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "primary:maya lee": True
    }


def test_round18_bare_yes_borrow_never_overrides_later_decline():
    # CR17-2: the borrow from the severed fact clause's emptied
    # head ("my") returned before the explicit "do not use"
    # clause was ever read.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = (
        "Yes, my address is not used for packages, "
        "do not use my address."
    )
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": False
    }
    # Affirmative-fact control (already correct, must stay).
    message = (
        "Yes, my address is used for packages, do not use my address."
    )
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": False
    }


@_pytest18.mark.parametrize(
    "message",
    [
        # CR17-3: double negation inside the slot is an
        # affirmation or at minimum undecidable — never a
        # persisted decline.
        "My address should never not be used.",
        "My address should not not be used.",
        "My address should not never be used.",
    ],
)
def test_round18_address_double_negation_slot_defers(message):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


# --- Round-19 review fixes (CR18-1..3) ----------------------------------------
# The bare-affirmative rule's instruction frame must be
# imperative-ANCHORED (a declarative subject in front of "use
# my address" is a usage fact, not an instruction); a borrowed
# verdict is PROVISIONAL — any direct verdict in a later
# sentence of the message overrides it; and a severed passive
# tail that deferred on double negation must not be borrowed
# over (the emptied head no longer shows why it deferred).

import pytest as _pytest19


@_pytest19.mark.parametrize(
    "message",
    [
        # CR18-1: declarative / modal / reported forms of the
        # use-frame are usage facts or third-party statements,
        # not the user's imperative.
        "They use my address for packages.",
        "They use the address for packages.",
        "The neighbors use my address for packages.",
        "They can use my address for packages.",
        "I use my address for packages.",
        "The instructions say to use my address.",
    ],
)
def test_round19_address_declarative_use_frame_defers(message):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


@_pytest19.mark.parametrize(
    "sentence_two",
    [
        "Please do not use my old primary home address.",
        "My address should not be used.",
        "No, don't use my address for any of them.",
        "Please do not use my address.",
    ],
)
def test_round19_borrowed_yes_never_preempts_a_later_decline_sentence(
    sentence_two,
):
    # CR18-2: the borrowed True from the severed fact sentence
    # is provisional; an independently-deciding decline in a
    # later sentence wins.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"Yes, my address is not used for packages. {sentence_two}"
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": False
    }


def test_round19_borrowed_yes_still_decides_without_a_later_direct_read():
    # The provisional borrow still lands when nothing else in
    # the message decides the offer directly.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "Yes, my address is not used for packages."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": True
    }


@_pytest19.mark.parametrize(
    "tail",
    [
        # CR18-3: severed passive tails that defer on double
        # negation must not be resurrected by the bare-Yes
        # borrow over the emptied head.
        "my address should never not be used.",
        "my address should not never be used.",
        "my address should not be used not for mail but for identification.",
    ],
)
def test_round19_severed_double_negation_tail_is_not_borrowed_over(tail):
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"Yes, it does not handle packages {tail}"
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


# --- Round-20 review fixes (CR19-1..2) ----------------------------------------
# A provisional borrow is the weakest read in the message: a
# decline-SHAPED sentence (modal use-instruction, instruction
# passive, or want-decline) vetoes it even when no decider
# parses that sentence directly. And the bare rule's anchored
# frame must end at the address as OBJECT — "address book" is
# a compound noun, not the offer.

import pytest as _pytest20

_BORROW_SENTENCE = "Yes, my address is not used for packages."


@_pytest20.mark.parametrize(
    "decline",
    [
        "You should not use my address.",
        "You shouldn't use my address.",
        "You must not use my address.",
        "You can't use my address.",
        "You may not use my address.",
        "You should never use my address.",
        "I do not want my address used.",
        "I don't want you to use my address.",
        "My address is not to be used.",
    ],
)
def test_round20_borrow_never_survives_a_decline_shaped_sentence(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts(
            [PRIMARY, address], f"{_BORROW_SENTENCE} {decline}"
        )
        == {}
    )
    # Order-independent: the decline may precede the borrow.
    assert (
        _resolve_offer_verdicts(
            [PRIMARY, address], f"{decline} {_BORROW_SENTENCE}"
        )
        == {}
    )


@_pytest20.mark.parametrize(
    "message",
    [
        # CR19-2: "address" as a compound-noun modifier is not
        # the offer's object.
        "Use my address book.",
        "Use my address book to find her number.",
        "Use my address label.",
    ],
)
def test_round20_address_compound_noun_object_defers(message):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


@_pytest20.mark.parametrize(
    "message",
    [
        # Controls: genuine imperatives with a continuation
        # after the object still decide via the bare rule.
        "Use my address for packages.",
        "Please use that address of mine.",
        "Use my address on the form.",
    ],
)
def test_round20_address_imperative_controls_still_decide(message):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": True
    }


# --- Round-21 review fixes (CR20-1) --------------------------------------------
# The borrow veto is no longer three decline SHAPES: a
# provisional borrow dies if ANY other clause or sentence
# carries a negated/prohibited address instruction in any
# dress — imperative, copula, causative, prohibition verb, or
# pronoun-referring ("use it", "it … be used"). The borrow's
# own fact clause ("is not used for packages" — a property
# fact, no instruction marker) never vetoes itself.

import pytest as _pytest21

_BORROW21 = "Yes, my address is not used for packages."


@_pytest21.mark.parametrize(
    "decline",
    [
        "Never use my address for any of them.",
        "Do not use my address for any of them.",
        "You are not to use my address.",
        "You are not allowed to use my address.",
        "It is not to be used.",
        "Never use it.",
        "Do not use it.",
        "You should not use it.",
        "Nobody should use my address.",
        "Do not let anyone use my address.",
        "Do not have my address used.",
        "I forbid the use of my address.",
    ],
)
def test_round21_borrow_dies_on_any_negated_address_instruction(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW21} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW21}")
        == {}
    )


def test_round21_same_sentence_pronoun_decline_blocks_the_borrow():
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "Yes, my address is not used for packages, and it is not to be used."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


def test_round21_unrelated_negated_passive_does_not_veto():
    # A negated passive about a DIFFERENT object (no address,
    # no pronoun reference) is not an address instruction.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"{_BORROW21} The form is not to be used."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": True
    }


# --- Round-22 review fixes (CR21-1) --------------------------------------------
# The borrow veto is no longer a factor enumeration: a
# provisional borrow survives only if every OTHER clause in
# the message is BENIGN for the address — a property fact
# ("is used for packages", "does not handle mail", "is in
# Austin") or a plainly affirmative instruction ("You should
# use my address."). Any other undecidable clause that
# references the address (or "it" in a use context) vetoes
# it, whatever verb or negation dress the decline wears.

import pytest as _pytest22

_BORROW22 = "Yes, my address is not used for packages."


@_pytest22.mark.parametrize(
    "decline",
    [
        "Stop using my address.",
        "Please stop using my address for them.",
        "Stop using it.",
        "No using my address.",
        "Do not put my address on the forms.",
        "I do not want it used.",
        "I don't want it used.",
        "I refuse the use of my address.",
        "I refuse to let you use my address.",
        "Keep my address off the forms.",
        "Keep it off the forms.",
        "My address is not for use.",
        "My address is off limits.",
        "Not my address.",
    ],
)
def test_round22_borrow_dies_on_any_undecidable_decline(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW22} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW22}")
        == {}
    )


def test_round22_same_sentence_gerund_decline_blocks_the_borrow():
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "Yes, my address is not used for packages, stop using my address."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


@_pytest22.mark.parametrize(
    "benign",
    [
        # Property facts (the two syntactic fact classes) do
        # not veto.
        "The address does not handle mail.",
        "My address is not used for identification.",
    ],
)
def test_round22_benign_clauses_do_not_veto_the_borrow(benign):
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"{_BORROW22} {benign}"
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {
        "address:123 main street, austin, texas": True
    }


# --- Round-23 review fixes (CR22-1..3, MN22-3) ---------------------------------
# Benign is now ONLY the two syntactic property-fact classes
# (fact passive, property-verb fact). The round-22 copula
# class was predicate-blind ("is a no", "is out") and its
# plainly-affirmative class was cessation-lexeme-bound
# ("Forget using…") — both dropped: an undecidable sentence
# about the address that is not a property fact vetoes the
# borrow, affirmative dress included (the cost is a re-ask,
# the safe direction). The pronoun reference gate covers
# exclusion/removal verbs, and a first-person denial ("I
# never said…") is the user's own speech, not a hedge skip.

import pytest as _pytest23

_BORROW23 = "Yes, my address is not used for packages."


@_pytest23.mark.parametrize(
    "decline",
    [
        # CR22-1: verdict-bearing copula predicates are not
        # property facts.
        "My address is a no.",
        "My address is a hard no.",
        "My address is a definite no.",
        "My address is a no-go.",
        "My address is not an option.",
        "My address is out.",
        "That address is out.",
        "My address is off-limits.",
        # CR22-2: cessation verbs outside the old list.
        "Forget using my address.",
        "Scratch using my address.",
        "Cancel using my address.",
        "Quit using my address.",
        # CR22-3: pronoun declines with exclusion/removal
        # verbs.
        "Leave it out of the documents.",
        "Keep it out of the documents.",
        "Do not include it.",
        "Exclude it from the forms.",
        "Omit it from the forms.",
        "Remove it from the forms.",
        "Leave it out.",
    ],
)
def test_round23_borrow_dies_on_predicate_and_pronoun_declines(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW23} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW23}")
        == {}
    )


@_pytest23.mark.parametrize(
    "undecidable",
    [
        # Deliberate round-23 narrowing: undecidable
        # affirmative/copula sentences no longer keep a borrow
        # alive either — the borrow coexists only with
        # property facts and direct reads.
        "You should use my address.",
        "Just use my address.",
        "Keep using my address.",
        "My address is in Austin.",
    ],
)
def test_round23_borrow_coexists_only_with_property_facts(undecidable):
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"{_BORROW23} {undecidable}"
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


def test_round23_first_person_denial_is_not_a_hedge_skip():
    # MN22-3: "I never said to use my address." is the user's
    # own denial, not reported speech — the post-pass hedge
    # skip must not protect the borrow from it.
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"{_BORROW23} I never said to use my address."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


# --- Round-24 review fixes (CR23-1..3) -----------------------------------------
# The borrow guard's reference gate can now SEE the offer:
# a clause references the address offer if it names the
# anchor's distinctive tokens (street number/name, city,
# state), uses a demonstrative with a use/removal verb
# ("that", "the other one"), or "it" with a stemmed
# use/exclusion/removal verb (delete/erase/pull/strike/skip
# included). And an F1 fact passive with an explicit
# exclusion tail in a sibling clause ("…never for
# documents") is a scoped restriction, not a benign fact.

import pytest as _pytest24

_BORROW24 = "Yes, my address is not used for packages."


@_pytest24.mark.parametrize(
    "decline",
    [
        # CR23-1: anchor value / street / city / demonstrative.
        "Do not use 123 Main Street.",
        "Never use 123 Main Street for them.",
        "Keep Main Street off the forms.",
        "Leave that out of the documents.",
        "Do not use that one.",
        "Use the other one instead.",
        "Exclude the Austin one.",
        # CR23-2: removal verbs beyond the old list.
        "Delete it from the forms.",
        "Erase it from the forms.",
        "Pull it from the forms.",
        "Strike it from the forms.",
        "Skip it.",
        # CR23-3: F1 fact passive with an explicit exclusion
        # tail.
        "My address is used for identification only, never for documents.",
        "My address is used for identification only, not on any documents.",
    ],
)
def test_round24_borrow_dies_on_anchor_and_tail_declines(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW24} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW24}")
        == {}
    )


def test_round24_same_sentence_anchor_decline_blocks_the_borrow():
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = "Yes, my address is not used for packages, delete it from the forms."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


# --- Round-25 review fixes (CR24-1..4, MN24-2) ---------------------------------
# The reference gate covers the proximal demonstratives
# ("this", "this one"), placement/motion stems (place, go,
# enter, list, write, ban, barred, belongs), and definite
# place descriptions with a reference verb ("the place",
# "my home", "my residence"). The F1 restriction scan reads
# the whole sentence tail after the fact-passive match —
# same-clause, reordered, elliptical, and excluding-tails
# included. First-person "asked" joins the denial exception.

import pytest as _pytest25

_BORROW25 = "Yes, my address is not used for packages."


@_pytest25.mark.parametrize(
    "decline",
    [
        # CR24-1: proximal demonstratives.
        "Do not use this.",
        "Never use this.",
        "Keep this off the forms.",
        "Leave this out of the documents.",
        "Delete this from the forms.",
        "Do not use this one.",
        # CR24-2: F1 restriction tails in every packaging.
        "My address is used for identification only never for documents.",
        "My address is used for identification only, never documents.",
        "My address is used for identification only, but not documents.",
        "My address is used for identification only, for documents never.",
        "My address is used for identification only, excluding documents.",
        # CR24-3: placement / motion / record stems.
        "Do not place it on the forms.",
        "Do not place that on the forms.",
        "It must not go on the forms.",
        "That must not go on the forms.",
        "That goes nowhere near the forms.",
        "Do not enter it on the forms.",
        "Do not list it on the forms.",
        "Do not write it on the forms.",
        "That belongs nowhere near the forms.",
        "Ban it from the forms.",
        "It is barred from the forms.",
        # CR24-4: definite place descriptions.
        "Do not use the place on the forms.",
        "Keep the place off the forms.",
        "Do not use the place.",
        "Do not use my home.",
        "Never use my residence on the forms.",
        "Keep my home off the forms.",
        "Exclude my home.",
    ],
)
def test_round25_borrow_dies_on_demonstrative_and_paraphrase_declines(
    decline,
):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW25} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW25}")
        == {}
    )


def test_round25_first_person_asked_denial_vetoes():
    # MN24-2: "I never asked you to use my address." is the
    # user's own denial, like "said"/"told".
    address = _offer("address", "123 Main Street, Austin, Texas")
    message = f"{_BORROW25} I never asked you to use my address."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}
    message = f"{_BORROW25} I never asked you to use it."
    assert _resolve_offer_verdicts([PRIMARY, address], message) == {}


# --- Round-26 review fixes (CR25-1..2) -----------------------------------------
# The F1 tail scan covers the full exclusion lexeme family
# (never / not / no / exclud* / except / off), and the place
# descriptions cover the primary US dwelling nouns.

import pytest as _pytest26

_BORROW26 = "Yes, my address is not used for packages."


@_pytest26.mark.parametrize(
    "decline",
    [
        # CR25-1: elliptical "no" tails and participle
        # exclusions.
        "My address is used for identification only, no documents.",
        "My address is used for identification only, no documents please.",
        "My address is used for identification only, no document use.",
        "My address is used for identification, documents excluded.",
        "My address is used for identification only, documents excluded please.",
        "My address is used for identification only, documents are off-limits.",
        "My address is used for identification purposes alone, documents are off.",
        # CR25-2: primary dwelling nouns.
        "Do not use the condo.",
        "Do not use my condo.",
        "Keep the condo off the forms.",
        "Do not use the duplex.",
        "Do not use my townhouse.",
        "Never use my townhouse on the forms.",
        "Exclude the townhouse.",
        "Do not use the loft.",
        "Do not use my cottage.",
    ],
)
def test_round26_borrow_dies_on_tail_and_dwelling_declines(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW26} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW26}")
        == {}
    )


# --- Round-27 review fixes (CR26-1) ----------------------------------------------
# Place descriptions allow a modifier run between the
# determiner and the dwelling noun ("mobile home", "guest
# house", "studio apartment"), and the noun list covers the
# observed single-word dwellings (studio, farmhouse,
# trailer, penthouse, brownstone, ranch, suite).

import pytest as _pytest27

_BORROW27 = "Yes, my address is not used for packages."


@_pytest27.mark.parametrize(
    "decline",
    [
        "Do not use the mobile home.",
        "Do not use my mobile home.",
        "Never use the mobile home on the forms.",
        "Keep the mobile home off the forms.",
        "Exclude the mobile home.",
        "Do not use the guest house.",
        "Keep the guest house off the forms.",
        "Do not use the single-family home.",
        "Do not use the manufactured home.",
        "Do not use the tiny home.",
        "Do not use the tiny house.",
        "Do not use the studio apartment.",
        "Do not use my studio apartment.",
        "Do not use the garden apartment.",
        "Do not use the basement apartment.",
        "Do not use the detached garage apartment.",
        "Do not use the carriage house.",
        "Do not use the pool house.",
        "Do not use the studio.",
        "Do not use the farmhouse.",
        "Do not use the trailer.",
        "Do not use the penthouse.",
        "Exclude the penthouse.",
        "Do not use the brownstone.",
        "Do not use the ranch.",
        "Do not use the in-law suite.",
    ],
)
def test_round27_borrow_dies_on_modified_dwelling_descriptions(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW27} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW27}")
        == {}
    )


# --- Round-28 review fixes (CR27-1) ----------------------------------------------
# Modifier tokens admit digits (bedroom counts and ordinals
# are the canonical dwelling descriptors), the determiner
# may join the noun by a hyphen ("mobile-home"), and the
# noun list covers guesthouse and plural forms.

import pytest as _pytest28

_BORROW28 = "Yes, my address is not used for packages."


@_pytest28.mark.parametrize(
    "decline",
    [
        "Do not use the 2-bedroom apartment.",
        "Do not use my 2-bedroom apartment.",
        "Exclude the 2-bedroom apartment.",
        "Keep the 2-bedroom apartment off the forms.",
        "Do not use the 3-bedroom house.",
        "Never use the 3-bedroom house on the forms.",
        "Do not use the 1-bedroom apartment.",
        "Do not use the 4-bedroom house.",
        "Do not use the 2 bedroom apartment.",
        "Do not use the 2-bed apartment.",
        "Do not use the 1-bathroom apartment.",
        "Do not use the 2-story house.",
        "Do not use the 1st floor apartment.",
        "Do not use the 2BR apartment.",
        "Do not use the mobile-home.",
        "Do not use the town-home.",
        "Do not use the guesthouse.",
        "Do not use the mobile homes.",
        "Do not use the apartments.",
    ],
)
def test_round28_borrow_dies_on_digit_led_dwelling_descriptions(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW28} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW28}")
        == {}
    )


# --- Round-29 review fixes (CR28-1) ----------------------------------------------
# The v7 presentation numbers the offers and invites the
# user to "answer each one separately", so an ordinal
# reference ("the 2nd one", "number two", "#2") is a
# presentation-canonical way to decline one of them. The
# reference is deliberately coarse, like every veto
# reference: any ordinal decline defers the borrowed
# address verdict (re-ask cost only); direct verdicts are
# untouched.

import pytest as _pytest29

_BORROW29 = "Yes, my address is not used for packages."


@_pytest29.mark.parametrize(
    "decline",
    [
        "Do not use the 2nd one.",
        "Do not use the second one.",
        "Never use the 1st one.",
        "Exclude the second one.",
        "Do not use number two.",
        "Do not use #2.",
        "Do not use the last one.",
        "Skip the 2nd one.",
        "The second one is out.",
        "Do not use the properties.",
    ],
)
def test_round29_borrow_dies_on_ordinal_offer_references(decline):
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{_BORROW29} {decline}")
        == {}
    )
    assert (
        _resolve_offer_verdicts([PRIMARY, address], f"{decline} {_BORROW29}")
        == {}
    )


def test_round29_ordinal_decline_in_three_offer_turn():
    backup = _offer("backup", "David Lee")
    address = _offer("address", "123 Main Street, Austin, Texas")
    assert (
        _resolve_offer_verdicts(
            [PRIMARY, backup, address],
            f"{_BORROW29} Do not use the 3rd one.",
        )
        == {}
    )
