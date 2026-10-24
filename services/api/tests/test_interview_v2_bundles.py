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
