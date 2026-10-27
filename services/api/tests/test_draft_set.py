"""Draft-set resolution (family draft generation plan, Task 1.5 / D2).

Pure unit tests for ``resolve_draft_set``: the generated document set is
a deterministic function of the matter's stored answers, mirroring the
v2 pack's own ``applies_if`` gates. Codicil / trust_amendment are never
in a fresh plan's set (they amend executed documents).
"""

from __future__ import annotations

from app.domains.documents.draft_set import resolve_draft_set, resolve_draft_signals

SHARMA = {
    "has_minor_children": True,
    "wants_revocable_trust": True,
    "wants_poa_document": True,
    "wants_healthcare_directive": True,
}


def test_sharma_profile_yields_the_five_types_in_canonical_order() -> None:
    assert resolve_draft_set(SHARMA) == [
        "will",
        "poa",
        "advance_directive",
        "guardianship",
        "trust",
    ]


def test_fully_pruned_profile_yields_will_only() -> None:
    answers = {
        "has_minor_children": False,
        "wants_revocable_trust": False,
        "wants_poa_document": False,
        "wants_healthcare_directive": False,
    }
    assert resolve_draft_set(answers) == ["will"]


def test_minors_only_adds_guardianship() -> None:
    answers = {"has_minor_children": True, "wants_revocable_trust": False}
    assert resolve_draft_set(answers) == ["will", "poa", "advance_directive", "guardianship"]


def test_trust_only_adds_trust() -> None:
    answers = {"has_minor_children": False, "wants_revocable_trust": True}
    assert resolve_draft_set(answers) == ["will", "poa", "advance_directive", "trust"]


def test_unanswered_gates_default_poa_and_directive_in_trust_and_guardianship_out() -> None:
    # `!= false` semantics: an unanswered opt-out question never excludes;
    # `== true` semantics: an unanswered opt-in question never includes.
    assert resolve_draft_set({}) == ["will", "poa", "advance_directive"]


def test_declined_poa_and_directive_are_excluded() -> None:
    answers = {"wants_poa_document": False, "wants_healthcare_directive": False}
    assert resolve_draft_set(answers) == ["will"]


def test_signals_explain_every_type() -> None:
    signals = {entry["template_type"]: entry for entry in resolve_draft_signals(SHARMA)}
    assert signals["will"] == {"template_type": "will", "included": True, "signal": "always"}
    assert signals["trust"]["included"] is True
    assert signals["trust"]["signal"] == "wants_revocable_trust == true"

    pruned = {
        entry["template_type"]: entry
        for entry in resolve_draft_signals({"wants_revocable_trust": False})
    }
    assert pruned["trust"]["included"] is False
    assert pruned["trust"]["signal"] == "wants_revocable_trust != true"
    assert pruned["guardianship"]["included"] is False
    assert pruned["poa"]["included"] is True
    assert pruned["poa"]["signal"] == "wants_poa_document != false"
