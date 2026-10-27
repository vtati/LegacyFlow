"""us-va v2 pack tests (Interview v2, Part 1 Task 1.3).

The v2 pack is v1 + applicability annotations + cluster fields + three
new questions (two beneficiary gates in Part 1; testator_full_name comes
in Part 3). v1 is never edited.
"""
from __future__ import annotations

from app.domains.intake import question_sets as qs
from app.domains.intake.question_sets import _tokenize_applies, compute_progress
from app.domains.interview.service import _ADDRESS_ROLE_KEYS
from app.domains.legal_content.pack_loader import get_pack

CLUSTERS = {"identity", "children", "money", "wishes", "people", "agents", "trust", "final"}


def _v2():
    return get_pack("US-VA", "2.0.0")


def _required_total(per_set) -> int:
    return sum(p.required_total for p in per_set.values())


def test_loader_resolves_v2_as_highest_and_v1_by_version():
    assert get_pack("US-VA").manifest.semantic_version == "2.0.0"
    v1 = get_pack("US-VA", "0.2.0")
    assert v1.manifest.semantic_version == "0.2.0"
    # v1 untouched: specific_bequests still optional, no cluster fields.
    for _, _, q in qs.iter_questions(v1.question_sets):
        assert "cluster" not in q
        if q["key"] == "specific_bequests":
            assert q.get("required") is False


def test_v2_manifest_capabilities_mirror_v1():
    v1 = get_pack("US-VA", "0.2.0")
    v2 = _v2()
    assert v2.manifest.capabilities == v1.manifest.capabilities
    assert v2.manifest.capabilities.get("document_assembly") is True


def test_every_question_has_a_cluster():
    for _, _, q in qs.iter_questions(_v2().question_sets):
        assert q.get("cluster") in CLUSTERS, q["key"]


def test_every_applies_if_parses_and_references_existing_keys():
    pack = _v2()
    keys = qs.all_question_keys(pack.question_sets)
    exprs: list[str] = []
    for question_set in pack.question_sets.values():
        for section in question_set.get("sections", []):
            if section.get("applies_if"):
                exprs.append(str(section["applies_if"]))
            for q in section.get("questions", []):
                if q.get("applies_if"):
                    exprs.append(str(q["applies_if"]))
    assert exprs, "v2 pack must carry applies_if annotations"
    for expr in exprs:
        # Must not raise (parseable + evaluable).
        assert qs.evaluate_applies_if(expr, {}) in (True, False, None)
        for kind, text in _tokenize_applies(expr):
            if kind == "word" and text not in ("and", "or", "not"):
                assert text in keys, f"{expr!r} references unknown key {text!r}"


def test_v2_nomination_bindings_are_db_legal():
    allowed = {"guardian", "trustee", "executor", "financial_agent", "healthcare_agent"}
    bad = []
    for _, _, q in qs.iter_questions(_v2().question_sets):
        maps_to = q.get("maps_to") or ""
        if isinstance(maps_to, str) and maps_to.startswith("nominations."):
            parts = maps_to.split(".")
            if len(parts) == 3 and parts[1] not in allowed:
                bad.append((q.get("key"), maps_to))
    assert bad == []


def test_full_yes_profile_applicable_required_count():
    pack = _v2()
    answers = {
        "has_minor_children": True,
        "wants_revocable_trust": True,
        "has_specific_bequests": True,
        "has_retirement_accounts": True,
        "has_life_insurance": True,
        "wants_poa_document": True,
        "wants_healthcare_directive": True,
    }
    per_set, _pct, _missing, pruned = compute_progress(pack.question_sets, answers)
    # 38 v1 required + 2 beneficiary gates + specific_bequests (now
    # required, gate true) = 41 applicable, nothing pruned. Part 3 added
    # testator_full_name (required, identity) -> 42.
    assert _required_total(per_set) == 42
    assert pruned == []


def test_pruned_profile_denominator_and_pruned_keys():
    pack = _v2()
    answers = {
        "has_minor_children": False,
        "wants_revocable_trust": False,
        "has_specific_bequests": False,
        "has_retirement_accounts": False,
        "has_life_insurance": False,
        # wants_poa_document / wants_healthcare_directive unanswered:
        # their sections stay PENDING (counted), never pruned.
    }
    per_set, _pct, _missing, pruned = compute_progress(pack.question_sets, answers)
    # 30 pre-Part-3 + testator_full_name (always applicable) = 31.
    assert _required_total(per_set) == 31
    assert sorted(pruned) == sorted(
        [
            "primary_guardian_person",
            "guardian_reason",
            "backup_guardian_person",
            "primary_guardian_address",
            "successor_trustee_person",
            "trust_initial_property",
            "trust_distribution_age",
            "trust_marital_share",
            "specific_bequests",
            "retirement_beneficiaries_current",
            "life_insurance_beneficiaries_current",
        ]
    )


def test_poa_agent_address_question_agrees_with_address_guard():
    """Pack/guard agreement (R3 nit): ``poa_agent_address`` is in the
    interview's ``_ADDRESS_ROLE_KEYS``, whose guard requires a street-
    shaped full address — so every shipped pack that asks the question
    must actually request a street / full address, never only a
    city/state. Pins the two sides together so they cannot drift."""
    assert "poa_agent_address" in _ADDRESS_ROLE_KEYS
    for version in ("2.0.0", "0.2.0"):
        pack = get_pack("US-VA", version)
        question = next(
            q
            for _, _, q in qs.iter_questions(pack.question_sets)
            if q["key"] == "poa_agent_address"
        )
        label = str(question.get("label") or "").lower()
        help_text = str(question.get("help") or "").lower()
        # Separate assertions (not a loose ``or``): a revert of either
        # the label or the help wording must fail on its own.
        assert "street address" in label, (version, label)
        assert "full address" in label, (version, label)
        assert "identifies your agent by their full address" in help_text, (
            version,
            help_text,
        )


def test_declining_documents_prunes_their_sections():
    pack = _v2()
    answers = {
        "has_minor_children": True,
        "wants_revocable_trust": True,
        "has_specific_bequests": True,
        "has_retirement_accounts": True,
        "has_life_insurance": True,
        "wants_poa_document": False,
        "wants_healthcare_directive": False,
    }
    _per_set, _pct, _missing, pruned = compute_progress(pack.question_sets, answers)
    for key in (
        "poa_agent_person",
        "poa_agent_address",
        "successor_poa_agent_person",
        "poa_additional_powers",
        "healthcare_agent_person",
        "healthcare_agent_phone",
        "successor_healthcare_agent_person",
        "living_will_directives",
        "healthcare_agent_limitations",
        "organ_donation_wishes",
    ):
        assert key in pruned, key
