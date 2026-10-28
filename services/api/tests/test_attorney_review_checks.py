"""Attorney Reviewer — Layer 1 deterministic checks (plan 2026-10-27).

Pure-function tests over frozen snapshot dicts: no DB, no provider.
The clean Sharma-shaped set must produce ZERO deterministic findings
(the §9.8 false-positive budget); each seeded defect class must produce
exactly its finding.

All fixture data is synthetic.
"""

from __future__ import annotations

from datetime import date

from app.domains.attorney_review.checks import Finding, run_deterministic_checks

TODAY = date(2026, 10, 27)

TESTATOR_ID = "11111111-1111-1111-1111-111111111111"
SPOUSE_ID = "22222222-2222-2222-2222-222222222222"
BROTHER_ID = "33333333-3333-3333-3333-333333333333"
CHILD_ID = "44444444-4444-4444-4444-444444444444"

WILL_EXECUTION_BLOCK = (
    "Signed this day by the testator.\n"
    "Signature of Testator: ____________________\n"
    "Witness signature: ____________________\n"
    "Witness signature: ____________________\n"
    "SELF-PROVING AFFIDAVIT\n"
    "Commonwealth of Virginia, County of Fairfax. Subscribed and sworn "
    "before me under Va. Code § 64.2-403.\n"
    "Notary Public signature: ____________________"
)


def _persons() -> list[dict]:
    return [
        {"id": TESTATOR_ID, "full_name": "Arjun Sharma", "kind": "adult",
         "date_of_birth": "1985-04-12"},
        {"id": SPOUSE_ID, "full_name": "Neha Kapoor", "kind": "adult",
         "date_of_birth": "1987-09-03"},
        {"id": BROTHER_ID, "full_name": "Amit Sharma", "kind": "adult",
         "date_of_birth": "1989-01-25"},
        {"id": CHILD_ID, "full_name": "Kabir Sharma", "kind": "minor",
         "date_of_birth": "2018-05-01"},
    ]


def _context() -> dict[str, str]:
    return {
        "testator_name": "Arjun Sharma",
        "spouse_name": "Neha Kapoor",
        "executor_name": "Amit Sharma",
        "alternate_executor_name": "Neha Kapoor",
        "agent_name": "Neha Kapoor",
        "successor_agent_name": "Amit Sharma",
        "healthcare_agent_name": "Neha Kapoor",
        "successor_healthcare_agent_name": "Amit Sharma",
        "guardian_name": "Amit Sharma",
        "alternate_guardian_name": "Neha Kapoor",
        "children_list": "Kabir Sharma",
        "residuary_beneficiaries": "my spouse, Neha Kapoor",
        "initial_property": "My home at 123 Main Street, Richmond, Virginia",
        "today": "October 27, 2026",
    }


def _snapshot(template_type: str, body: str, *, context=None, template_body="") -> dict:
    return {
        "package_id": f"pkg-{template_type}",
        "package_version": 2,
        "template_type": template_type,
        "template_body": template_body,
        "body_text": body,
        "context": _context() if context is None else context,
    }


def _snapshots() -> list[dict]:
    will = (
        "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
        "I am married to Neha Kapoor. I appoint Amit Sharma as Executor. "
        "If Amit Sharma fails to act, I appoint Neha Kapoor.\n"
        "I give the residue of my estate to my spouse, Neha Kapoor.\n"
        + WILL_EXECUTION_BLOCK
    )
    poa = (
        "DURABLE POWER OF ATTORNEY\n"
        "I, Arjun Sharma, appoint Neha Kapoor as my agent. "
        "If Neha Kapoor cannot act, I appoint Amit Sharma as successor agent."
    )
    directive = (
        "ADVANCE MEDICAL DIRECTIVE\n"
        "I, Arjun Sharma, name Neha Kapoor as my healthcare agent."
    )
    guardianship = (
        "GUARDIANSHIP DESIGNATION\n"
        "We, Arjun Sharma and Neha Kapoor, name Amit Sharma as guardian "
        "of our minor child, Kabir Sharma."
    )
    trust = (
        "REVOCABLE TRUST AGREEMENT\n"
        "I, Arjun Sharma, as grantor, transfer my home at 123 Main Street, "
        "Richmond, Virginia to the trustee."
    )
    return [
        _snapshot("will", will),
        _snapshot("poa", poa),
        _snapshot("advance_directive", directive),
        _snapshot("guardianship", guardianship),
        _snapshot("trust", trust),
    ]


def _source(**overrides) -> dict:
    source = {
        "persons": _persons(),
        "nominations": [
            {"nomination_type": "executor", "person_id": BROTHER_ID,
             "priority": 1, "status": "confirmed"},
            {"nomination_type": "executor", "person_id": SPOUSE_ID,
             "priority": 2, "status": "confirmed"},
            {"nomination_type": "financial_agent", "person_id": SPOUSE_ID,
             "priority": 1, "status": "confirmed"},
            {"nomination_type": "healthcare_agent", "person_id": SPOUSE_ID,
             "priority": 1, "status": "confirmed"},
            {"nomination_type": "guardian", "person_id": BROTHER_ID,
             "priority": 1, "status": "confirmed"},
        ],
        "answers": {
            "has_minor_children": True,
            "wants_revocable_trust": True,
            "testator_full_name": "Arjun Sharma",
            "spouse_full_name": "Neha Kapoor",
        },
        "expected_set": ["will", "poa", "advance_directive", "guardianship", "trust"],
    }
    source.update(overrides)
    return source


def _profiles() -> dict:
    return {
        "will": {
            "id": "VA-WILL-V0",
            "requirements": {
                "testator_signature_required": True,
                "witnesses": {"required_count": 2},
                "notary": {"legally_required": False,
                           "recommended_self_proving_affidavit": True},
            },
            "source": {"citation": "Va. Code § 64.2-403"},
        }
    }


def _run(snapshots=None, source=None, profiles=None) -> list[Finding]:
    return run_deterministic_checks(
        snapshots=_snapshots() if snapshots is None else snapshots,
        source=_source() if source is None else source,
        execution_profiles=_profiles() if profiles is None else profiles,
        today=TODAY,
    )


def _by_check(findings: list[Finding], check: str) -> list[Finding]:
    return [f for f in findings if f.check == check]


# ---------------------------------------------------------------------------
# Clean control — the false-positive gate
# ---------------------------------------------------------------------------


def test_clean_sharma_set_produces_zero_deterministic_findings() -> None:
    assert _run() == []


# ---------------------------------------------------------------------------
# name_consistency
# ---------------------------------------------------------------------------


def test_name_misspelled_in_one_document_is_caught() -> None:
    snaps = _snapshots()
    snaps[1] = _snapshot(
        "poa",
        "DURABLE POWER OF ATTORNEY\n"
        "I, Arjun Sharma, appoint Neha Kapur as my agent.",
    )
    findings = _by_check(_run(snaps), "name_consistency")
    assert len(findings) == 1
    f = findings[0]
    assert f.layer == "deterministic"
    assert f.severity == "should_fix"
    assert f.document == "poa"
    assert "Neha Kapur" in (f.excerpt or "")
    assert "Neha Kapoor" in (f.suggested_fix or "")


def test_identical_names_everywhere_are_not_flagged() -> None:
    findings = _by_check(_run(), "name_consistency")
    assert findings == []


# ---------------------------------------------------------------------------
# role_consistency
# ---------------------------------------------------------------------------


def test_successor_equal_to_primary_is_caught() -> None:
    ctx = _context()
    ctx["alternate_executor_name"] = "Amit Sharma"  # same as executor_name
    snaps = [_snapshot(s["template_type"], s["body_text"], context=ctx)
             for s in _snapshots()]
    source = _source()
    # Align the priority-2 nomination with the planted context so only the
    # primary == successor rule fires (not the nomination cross-check).
    source["nominations"] = [
        {**n, "person_id": BROTHER_ID}
        if n["nomination_type"] == "executor" and n["priority"] == 2 else n
        for n in source["nominations"]
    ]
    findings = _by_check(_run(snaps, source=source), "role_consistency")
    assert len(findings) == 1
    assert findings[0].field == "alternate_executor_name"


def test_successor_without_primary_is_caught() -> None:
    ctx = _context()
    del ctx["agent_name"]  # successor_agent_name remains
    snaps = [_snapshot(s["template_type"], s["body_text"], context=ctx)
             for s in _snapshots()]
    findings = _by_check(_run(snaps), "role_consistency")
    assert any(f.field == "successor_agent_name" for f in findings)


def test_nomination_that_disagrees_with_rendered_slot_is_caught() -> None:
    source = _source()
    source["nominations"] = [
        {"nomination_type": "executor", "person_id": SPOUSE_ID,
         "priority": 1, "status": "confirmed"},
    ]
    findings = _by_check(_run(source=source), "role_consistency")
    assert len(findings) == 1
    assert "Amit Sharma" in findings[0].explanation
    assert "Neha Kapoor" in findings[0].explanation


# ---------------------------------------------------------------------------
# date_age_arithmetic
# ---------------------------------------------------------------------------


def test_minor_whose_dob_makes_them_an_adult_is_caught() -> None:
    persons = _persons()
    persons[3] = {**persons[3], "date_of_birth": "2000-05-01"}  # age 26
    findings = _by_check(_run(source=_source(persons=persons)), "date_age_arithmetic")
    assert len(findings) == 1
    assert "Kabir Sharma" in findings[0].explanation


def test_future_dob_is_caught() -> None:
    persons = _persons()
    persons[3] = {**persons[3], "date_of_birth": "2031-01-01"}
    findings = _by_check(_run(source=_source(persons=persons)), "date_age_arithmetic")
    assert len(findings) == 1


def test_minor_gate_answer_without_any_minor_is_caught() -> None:
    persons = [p for p in _persons() if p["kind"] != "minor"]
    ctx = _context()
    ctx["children_list"] = "___"
    snaps = [_snapshot(s["template_type"], s["body_text"], context=ctx)
             for s in _snapshots()]
    findings = _by_check(_run(snaps, source=_source(persons=persons)),
                          "date_age_arithmetic")
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# pack_completeness
# ---------------------------------------------------------------------------


def test_missing_pack_document_is_caught() -> None:
    snaps = [s for s in _snapshots() if s["template_type"] != "trust"]
    findings = _by_check(_run(snaps), "pack_completeness")
    assert len(findings) == 1
    assert "trust" in findings[0].explanation
    assert findings[0].package_id is None


def test_extraneous_document_is_caught() -> None:
    snaps = _snapshots() + [_snapshot("codicil", "CODICIL\nI, Arjun Sharma, ...")]
    findings = _by_check(_run(snaps), "pack_completeness")
    assert len(findings) == 1
    assert "codicil" in findings[0].explanation


# ---------------------------------------------------------------------------
# unfilled_placeholders
# ---------------------------------------------------------------------------


def test_family_required_blank_is_caught_attorney_fill_is_not() -> None:
    ctx = {k: v for k, v in _context().items() if k != "executor_address"}
    will = _snapshot(
        "will",
        "LAST WILL AND TESTAMENT OF Arjun Sharma\n" + WILL_EXECUTION_BLOCK,
        context=ctx,
        template_body=(
            "I appoint {{executor_name}}, of {{executor_address}}, as Executor. "
            "Witness 1: {{witness_1_name}} Date: {{signing_date}}"
        ),
    )
    snaps = [will] + _snapshots()[1:]
    source = _source(
        required_question_keys=["primary_executor_address"],
        slot_answer_keys={"executor_address": ["primary_executor_address"]},
    )
    findings = _by_check(_run(snaps, source), "unfilled_placeholders")
    assert len(findings) == 1
    assert findings[0].field == "executor_address"
    assert findings[0].document == "will"


# ---------------------------------------------------------------------------
# va_execution_requirements
# ---------------------------------------------------------------------------


def test_missing_witness_block_is_caught() -> None:
    will_body = (
        "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
        "Signature of Testator: ____________________\n"
        "SELF-PROVING AFFIDAVIT under Va. Code § 64.2-403.\n"
        "Notary Public signature: ____________________"
    )
    snaps = [_snapshot("will", will_body)] + _snapshots()[1:]
    findings = _by_check(_run(snaps), "va_execution_requirements")
    assert len(findings) == 1
    assert findings[0].severity == "should_fix"
    assert "witness" in findings[0].explanation.lower()


def test_missing_self_proving_affidavit_is_a_question_not_a_should_fix() -> None:
    will_body = (
        "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
        "Signature of Testator: ____________________\n"
        "Witness signature: ____________________\n"
        "Witness signature: ____________________\n"
        "Executed under Va. Code § 64.2-403."
    )
    snaps = [_snapshot("will", will_body)] + _snapshots()[1:]
    findings = _by_check(_run(snaps), "va_execution_requirements")
    assert len(findings) == 1
    assert findings[0].severity == "question_for_attorney"


def test_missing_statutory_citation_is_caught() -> None:
    will_body = (
        "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
        "Signature of Testator: ____________________\n"
        "Witness signature: ____________________\n"
        "Witness signature: ____________________\n"
        "SELF-PROVING AFFIDAVIT\nNotary Public signature: ____________"
    )
    snaps = [_snapshot("will", will_body)] + _snapshots()[1:]
    findings = _by_check(_run(snaps), "va_execution_requirements")
    assert any("64.2-403" in f.explanation for f in findings)


# ---------------------------------------------------------------------------
# reference_integrity
# ---------------------------------------------------------------------------


def test_residuary_beneficiary_outside_household_is_caught() -> None:
    ctx = _context()
    ctx["residuary_beneficiaries"] = "my friend, John Smith"
    snaps = [_snapshot(s["template_type"], s["body_text"], context=ctx)
             for s in _snapshots()]
    findings = _by_check(_run(snaps), "reference_integrity")
    assert len(findings) == 1
    assert "John Smith" in findings[0].explanation


def test_trust_without_initial_property_is_caught() -> None:
    ctx = _context()
    ctx["initial_property"] = "___"
    snaps = [_snapshot(s["template_type"], s["body_text"], context=ctx)
             for s in _snapshots()]
    findings = _by_check(_run(snaps), "reference_integrity")
    assert len(findings) == 1
    assert findings[0].document == "trust"


# ---------------------------------------------------------------------------
# Production false-positive regressions (2026-10-27 verification gate)
# Shapes taken from the live Sharma packet that the first production run
# flagged wrongly: similar sibling names, optional unanswered questions,
# the v2 template signature layout, the renderer's self-trustee default.
# ---------------------------------------------------------------------------

PRIYA_ID = "55555555-5555-5555-5555-555555555555"
DIYA_ID = "66666666-6666-6666-6666-666666666666"


def test_similar_family_names_are_not_misspellings() -> None:
    persons = _persons() + [
        {"id": PRIYA_ID, "full_name": "Priya Sharma", "kind": "adult",
         "date_of_birth": "1987-09-03"},
        {"id": DIYA_ID, "full_name": "Diya Sharma", "kind": "minor",
         "date_of_birth": "2017-05-01"},
    ]
    will = (
        "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
        "I give the residue to my wife, Priya Sharma. Our daughter is "
        "Diya Sharma.\n" + WILL_EXECUTION_BLOCK
    )
    snaps = [_snapshot("will", will)] + _snapshots()[1:]
    findings = _by_check(_run(snaps, _source(persons=persons)), "name_consistency")
    assert findings == []


def test_unfilled_optional_unanswered_slot_is_not_flagged() -> None:
    guardianship = _snapshot(
        "guardianship", "GUARDIANSHIP DESIGNATION\nArjun Sharma",
        template_body="Wishes: {{upbringing_wishes}}",
    )
    snaps = [s if s["template_type"] != "guardianship" else guardianship
             for s in _snapshots()]
    source = _source(
        required_question_keys=["testator_full_name"],
        slot_answer_keys={"upbringing_wishes": ["children_upbringing_wishes"]},
    )
    findings = _by_check(_run(snaps, source), "unfilled_placeholders")
    assert findings == []


def test_unfilled_answered_but_blank_slot_is_flagged() -> None:
    guardianship = _snapshot(
        "guardianship", "GUARDIANSHIP DESIGNATION\nArjun Sharma",
        template_body="Wishes: {{upbringing_wishes}}",
    )
    snaps = [s if s["template_type"] != "guardianship" else guardianship
             for s in _snapshots()]
    source = _source(
        answers={**_source()["answers"],
                 "children_upbringing_wishes": "Raise them in Richmond."},
        required_question_keys=["testator_full_name"],
        slot_answer_keys={"upbringing_wishes": ["children_upbringing_wishes"]},
    )
    findings = _by_check(_run(snaps, source), "unfilled_placeholders")
    assert len(findings) == 1
    assert findings[0].field == "upbringing_wishes"


REAL_V2_WILL_TAIL = (
    "IN WITNESS WHEREOF, I have signed this Will on October 27, 2026.\n\n"
    "________________________________________\n"
    "Arjun Sharma, Testator\n\n\n"
    "WITNESS ATTESTATION\n\n"
    "We, the undersigned witnesses, certify that the Testator signed this "
    "Will in our presence, in accordance with Va. Code \u00a7 64.2-403.\n\n"
    "Witness 1:\n\n________________________________________\nSignature\n\n"
    "________________________________________\nPrinted Name\n\n\n"
    "Witness 2:\n\n________________________________________\nSignature\n\n"
    "________________________________________\nPrinted Name\n\n\n"
    "SELF-PROVING AFFIDAVIT\n(Va. Code \u00a7 64.2-453)\n"
)


def test_execution_real_v2_layout_passes() -> None:
    will = "LAST WILL AND TESTAMENT OF Arjun Sharma\n" + REAL_V2_WILL_TAIL
    snaps = [_snapshot("will", will)] + _snapshots()[1:]
    findings = _by_check(_run(snaps), "va_execution_requirements")
    assert findings == []


def test_execution_stripped_witness_headers_are_caught() -> None:
    tail = REAL_V2_WILL_TAIL.replace("Witness 1:", "Observer 1:").replace(
        "Witness 2:", "Observer 2:")
    will = "LAST WILL AND TESTAMENT OF Arjun Sharma\n" + tail
    snaps = [_snapshot("will", will)] + _snapshots()[1:]
    findings = _by_check(_run(snaps), "va_execution_requirements")
    assert any(f.field == "witness_block" for f in findings)


def test_trustee_priority1_nomination_is_not_cross_checked() -> None:
    # The renderer sets trustee_name to the grantor by design (the
    # grantor is the initial trustee of the revocable trust), so a
    # priority-1 trustee nomination naming someone else is not a
    # documents-vs-records mismatch.
    ctx = _context()
    ctx["trustee_name"] = "Arjun Sharma"
    ctx["successor_trustee_name"] = "Neha Kapoor"
    snaps = [_snapshot(s["template_type"], s["body_text"], context=ctx)
             for s in _snapshots()]
    source = _source(nominations=_source()["nominations"] + [
        {"nomination_type": "trustee", "person_id": BROTHER_ID,
         "priority": 1, "status": "confirmed"},
        {"nomination_type": "trustee", "person_id": SPOUSE_ID,
         "priority": 2, "status": "confirmed"},
    ])
    findings = _by_check(_run(snaps, source), "role_consistency")
    assert findings == []


# ---------------------------------------------------------------------------
# Review pass 2 — M3 / M6 / M7
# ---------------------------------------------------------------------------


def test_twins_sharing_a_dob_produce_two_findings() -> None:
    from app.domains.attorney_review.checks import finding_hash

    persons = _persons() + [
        {"id": "55555555-5555-5555-5555-555555555555", "full_name": "Asha Twin",
         "kind": "minor", "date_of_birth": "2030-01-15"},
        {"id": "66666666-6666-6666-6666-666666666666", "full_name": "Bina Twin",
         "kind": "minor", "date_of_birth": "2030-01-15"},
    ]
    findings = _by_check(_run(source=_source(persons=persons)), "date_age_arithmetic")
    twin = [f for f in findings if f.excerpt == "2030-01-15"]
    assert len(twin) == 2
    assert finding_hash(twin[0]) != finding_hash(twin[1])
    assert {f.subject for f in twin} == {"Asha Twin", "Bina Twin"}


def test_spouse_slot_blank_despite_answer_is_flagged() -> None:
    context = _context()
    del context["spouse_name"]
    will = _snapshot(
        "will", "LAST WILL\nSpouse: (blank)",
        context=context, template_body="Spouse: {{spouse_name}}",
    )
    source = _source(slot_answer_keys={"spouse_name": ["spouse_full_name"]})
    flagged = _by_check(_run(snapshots=[will], source=source), "unfilled_placeholders")
    assert [f.field for f in flagged] == ["spouse_name"]


def test_placeholder_valued_slot_counts_as_unfilled() -> None:
    context = _context()
    context["children_list"] = "___"
    will = _snapshot(
        "will", "LAST WILL\nChildren: ___",
        context=context, template_body="Children: {{children_list}}",
    )
    source = _source(
        slot_answer_keys={"children_list": ["minor_children_details", "has_minor_children"]},
    )
    flagged = _by_check(_run(snapshots=[will], source=source), "unfilled_placeholders")
    assert [f.field for f in flagged] == ["children_list"]


def test_optional_unanswered_identity_slot_stays_quiet() -> None:
    context = _context()
    del context["spouse_name"]
    will = _snapshot(
        "will", "LAST WILL\nSpouse: (blank)",
        context=context, template_body="Spouse: {{spouse_name}}",
    )
    answers = {"has_minor_children": True, "testator_full_name": "Arjun Sharma"}
    source = _source(
        answers=answers, slot_answer_keys={"spouse_name": ["spouse_full_name"]},
    )
    flagged = _by_check(_run(snapshots=[will], source=source), "unfilled_placeholders")
    assert flagged == []


def test_reference_integrity_unknown_beneficiary_is_a_question() -> None:
    context = _context()
    context["residuary_beneficiaries"] = "my spouse, Neha Kapoor, and the American Red Cross"
    will = _snapshot("will", "LAST WILL", context=context)
    findings = _by_check(_run(snapshots=[will]), "reference_integrity")
    charity = [f for f in findings if "American Red Cross" in (f.excerpt or "")]
    assert len(charity) == 1
    assert charity[0].severity == "question_for_attorney"


def test_reference_integrity_near_miss_name_is_should_fix() -> None:
    persons = _persons() + [
        {"id": "77777777-7777-7777-7777-777777777777", "full_name": "Maya Lee",
         "kind": "adult", "date_of_birth": "1990-06-15"},
    ]
    context = _context()
    context["residuary_beneficiaries"] = "my sister, Maya Lea"
    will = _snapshot("will", "LAST WILL", context=context)
    findings = _by_check(
        _run(snapshots=[will], source=_source(persons=persons)), "reference_integrity")
    typo = [f for f in findings if "Maya Lea" in (f.excerpt or "")]
    assert len(typo) == 1
    assert typo[0].severity == "should_fix"


def test_childless_packet_children_list_blank_is_by_design() -> None:
    """Review N2: the renderer writes children_list="___" BY DESIGN
    for a childless family, and has_minor_children is a required
    question — membership-based gating flagged every clean childless
    packet. A False gate answer makes the blank inapplicable."""
    context = _context()
    context["children_list"] = "___"
    will = _snapshot(
        "will", "LAST WILL\nChildren: ___",
        context=context, template_body="Children: {{children_list}}",
    )
    answers = {
        "has_minor_children": False,
        "wants_revocable_trust": True,
        "testator_full_name": "Arjun Sharma",
        "spouse_full_name": "Neha Kapoor",
    }
    source = _source(
        answers=answers,
        slot_answer_keys={"children_list": ["minor_children_details", "has_minor_children"]},
    )
    flagged = _by_check(_run(snapshots=[will], source=source), "unfilled_placeholders")
    assert flagged == []
