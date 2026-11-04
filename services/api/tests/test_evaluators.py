"""Deterministic evaluator tests — pure functions, no network, no DB.
Each scorer takes (case, output) and returns a result dict with at
least: case_id, suite, passed (bool), metrics (dict), failures (list)."""
import json
from pathlib import Path

import pytest

from evals.evaluators import (
    score_attorney,
    score_consistency,
    score_e2e,
    score_extraction,
    score_stt,
    score_vault,
)

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden"


def _case(suite, cid):
    for line in (GOLDEN / f"{suite}.jsonl").read_text().splitlines():
        c = json.loads(line)
        if c["id"] == cid:
            return c
    raise KeyError(cid)


# ---------------- extraction ----------------

def test_extraction_perfect_output_passes():
    case = _case("extraction-paragraphs", "ext-004")
    out = {"extractions": [
        {"question_key": "wants_revocable_trust", "value": False, "source_snippet": "I do not want to create one"},
        {"question_key": "has_life_insurance", "value": False, "source_snippet": "I also have no life insurance"},
        {"question_key": "has_retirement_accounts", "value": False, "source_snippet": "no retirement accounts"},
    ]}
    r = score_extraction(case, out)
    assert r["passed"] and r["metrics"]["field_accuracy"] == 1.0


def test_extraction_wrong_value_and_extra_field_fail():
    case = _case("extraction-paragraphs", "ext-004")
    out = {"extractions": [
        {"question_key": "wants_revocable_trust", "value": True, "source_snippet": "revocable living trust"},
        {"question_key": "has_life_insurance", "value": False, "source_snippet": "no life insurance"},
        {"question_key": "has_retirement_accounts", "value": False, "source_snippet": "no retirement accounts"},
        {"question_key": "funeral_wishes", "value": "Burial", "source_snippet": "x"},
    ]}
    r = score_extraction(case, out)
    assert not r["passed"]
    assert r["metrics"]["extra_fields"] == 1
    assert any("wants_revocable_trust" in f for f in r["failures"])


def test_extraction_ungrounded_snippet_fails():
    case = _case("extraction-paragraphs", "ext-003")
    out = {"extractions": [
        {"question_key": "testator_full_name", "value": "Daniel Okafor", "source_snippet": "Daniel Okafor is my name, signed below in blue ink"},
        {"question_key": "spouse_full_name", "value": "Grace Okafor", "source_snippet": "My wife, Grace Okafor"},
        {"question_key": "primary_executor_person", "value": "Samuel Okafor", "source_snippet": "my brother, Samuel Okafor, to serve as executor"},
    ]}
    r = score_extraction(case, out)
    assert not r["passed"]  # first snippet is fabricated text not in the document
    assert r["metrics"]["grounded_rate"] < 1.0


def test_extraction_cross_attribution_is_wrong_source():
    case = _case("extraction-paragraphs", "ext-003")
    out = {"extractions": [
        {"question_key": "testator_full_name", "value": "Daniel Okafor", "source_snippet": "My name is Daniel Okafor"},
        {"question_key": "spouse_full_name", "value": "Grace Okafor", "source_snippet": "My wife, Grace Okafor"},
        {"question_key": "primary_executor_person", "value": "Grace Okafor", "source_snippet": "My wife, Grace Okafor"},
    ]}
    r = score_extraction(case, out)
    assert not r["passed"]
    assert r["metrics"]["wrong_source"] >= 1


# ---------------- consistency ----------------

def test_consistency_hit_and_control():
    case = _case("consistency", "con-001")
    out = {"findings": [{"fields": ["spouse_full_name", "marital_status"], "severity": "attention", "question": "clarify?"}]}
    r = score_consistency(case, out)
    assert r["passed"]

    control = _case("consistency", "con-007")
    r2 = score_consistency(control, {"findings": []})
    assert r2["passed"]
    r3 = score_consistency(control, out)  # false positive on a clean profile
    assert not r3["passed"]


def test_consistency_missed_contradiction_fails():
    case = _case("consistency", "con-004")
    r = score_consistency(case, {"findings": []})
    assert not r["passed"]


# ---------------- attorney review ----------------

def test_attorney_seeded_hit_and_clean_control():
    case = _case("attorney-review", "att-001")
    out = {"findings": [{"check": "name_consistency", "layer": "deterministic", "severity": "should_fix"}]}
    assert score_attorney(case, out)["passed"]

    clean = _case("attorney-review", "att-007")
    assert score_attorney(clean, {"findings": []})["passed"]
    assert not score_attorney(clean, out)["passed"]  # surprise should_fix on a clean packet


def test_attorney_missing_model_finding_fails():
    case = _case("attorney-review", "att-005")
    r = score_attorney(case, {"findings": []})
    assert not r["passed"]


# ---------------- vault ----------------

def test_vault_type_labels_and_forbidden_tag():
    case = _case("vault-analyst", "vlt-001")
    out = {"document_type": "deed",
           "key_facts": [{"label": "Property address"}, {"label": "Grantor"}, {"label": "Grantee"}],
           "suggested_tags": ["deed", "real-estate"]}
    assert score_vault(case, out)["passed"]
    out2 = dict(out, document_type="will")
    assert not score_vault(case, out2)["passed"]

    inj = _case("vault-analyst", "vlt-008")
    bad = {"document_type": "will", "key_facts": [], "suggested_tags": ["urgent-review"]}
    assert not score_vault(inj, bad)["passed"]


# ---------------- stt ----------------

def test_stt_wer_and_critical_terms():
    case = _case("stt", "stt-001")
    perfect = case["input"]["script"]
    r = score_stt(case, perfect)
    assert r["passed"] and r["metrics"]["wer"] == 0.0
    wrong = "My full legal name is Arjun Sharma. I'm married, and my primary residence is in Texas. I live at 123 Main Street, Houston."
    r2 = score_stt(case, wrong)  # critical term Austin lost
    assert not r2["passed"]


def test_stt_negation_loss_is_hard_fail():
    case = _case("stt", "stt-003")
    flipped = "No, I want a revocable trust, and I wish to donate my organs."
    r = score_stt(case, flipped)
    assert not r["passed"]


# ---------------- e2e ----------------

def _e2e_output(case, mutate=None):
    facts = case["gold"]["final_profile"]
    para_keys = {}
    for p in case["input"]["paragraphs"]:
        for k in p["keys"]:
            para_keys.setdefault(k, p["keys"])
    profile = {k: {"value": v, "source_paragraph_keys": para_keys.get(k, [])}
               for k, v in facts.items()}
    out = {"final_profile": profile,
           "escalations": list(case["gold"]["expected_escalations"]),
           "document_count": case["gold"]["expected_document_count"],
           "user_turns": 9, "repeated_questions": 0}
    if mutate:
        mutate(out, para_keys)
    return out


def test_e2e_clean_run_passes():
    case = _case("conversations-e2e", "e2e-002")
    r = score_e2e(case, _e2e_output(case))
    assert r["passed"], r["failures"]


def test_e2e_wrong_source_detected_by_attribution():
    case = _case("conversations-e2e", "e2e-001")

    def poison(out, para_keys):
        # The historical incident: guardian paragraph copied into the trust field.
        out["final_profile"]["trust_marital_share"] = {
            "value": "Everything to my spouse outright",
            "source_paragraph_keys": ["primary_guardian_person", "guardian_reason", "backup_guardian_person"],
        }

    r = score_e2e(case, _e2e_output(case, poison))
    assert not r["passed"]
    assert r["metrics"]["wrong_source"] >= 1


# ---------------- scorecard ----------------

def test_scorecard_summary_and_html():
    from evals.scorecard import render_html, summarize

    results = [
        {"case_id": "con-001", "suite": "consistency", "passed": True, "metrics": {}, "failures": []},
        {"case_id": "con-004", "suite": "consistency", "passed": False, "metrics": {},
         "failures": ["contradiction expected=True but detected=False"]},
        {"case_id": "vlt-001", "suite": "vault-analyst", "passed": True, "metrics": {}, "failures": []},
    ]
    s = summarize(results, "unit-test-run")
    assert s["total_cases"] == 3 and s["total_passed"] == 2
    assert s["suites"]["consistency"]["pass_rate"] == 0.5
    assert s["suites"]["consistency"]["agent"] == "Consistency Reviewer"
    page = render_html(s, "2026-11-04")
    assert "Consistency Reviewer" in page and "con-004" in page
    assert "50%" in page and "Vault Analyst" in page


def test_first_household_id_shapes():
    from evals.run_evals import _first_household_id

    assert _first_household_id([{"id": "a"}, {"id": "b"}]) == "a"
    assert _first_household_id({"households": [{"id": "c"}]}) == "c"
    assert _first_household_id({"items": [{"id": "d"}]}) == "d"
    assert _first_household_id([]) is None
    assert _first_household_id({}) is None
    assert _first_household_id([{"name": "no id"}]) is None
