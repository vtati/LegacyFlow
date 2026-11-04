"""Deterministic evaluators for the LegacyFlow golden dataset.

One scorer per suite. Every scorer is a pure function of (case, output)
returning a result dict::

    {"case_id", "suite", "passed", "metrics": {...}, "failures": [str]}

Scoring philosophy (from the October benchmarks):
- Value comparison is SEMANTIC, never raw string equality — normalized
  containment counts as a match for longer strings (a fuller verbatim
  value is not an error).
- Correctness is judged on SOURCE ATTRIBUTION where the suite supports
  it: the right value copied from the wrong place is a wrong-source
  error, the failure class that hurt most in production.
- Hard gates and soft metrics are kept separate: ``passed`` reflects
  the hard gates only; turn budgets and the like ride along as metrics.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# normalization helpers


def _norm(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _value_match(gold: Any, actual: Any) -> bool:
    """Semantic value equality: normalized equality, or containment
    either way for strings long enough that containment is meaningful."""
    if isinstance(gold, bool) or isinstance(actual, bool):
        return _norm(gold) == _norm(actual)
    if isinstance(gold, (list, tuple)) or isinstance(actual, (list, tuple)):
        g = sorted(_norm(x) for x in (gold or []))
        a = sorted(_norm(x) for x in (actual or []))
        return g == a
    g, a = _norm(gold), _norm(actual)
    if g == a:
        return True
    if len(g) >= 8 and (g in a or a in g):
        return True
    return False


def _result(case: dict, metrics: dict, failures: list[str]) -> dict:
    return {
        "case_id": case["id"],
        "suite": case["suite"],
        "passed": not failures,
        "metrics": metrics,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# extraction


def score_extraction(case: dict, output: dict) -> dict:
    gold = {e["question_key"]: e for e in case["gold"]["extractions"]}
    doc_norm = _norm(case["input"]["document_text"])
    got: dict[str, dict] = {}
    extra = 0
    for item in output.get("extractions") or []:
        key = item.get("question_key")
        if key not in case["input"]["question_keys"]:
            extra += 1
        elif key in gold:
            got[key] = item
    failures: list[str] = []
    correct = 0
    grounded = 0
    wrong_source = 0
    snippet_checks = 0
    for key, g in gold.items():
        item = got.get(key)
        if item is None:
            failures.append(f"{key}: missing extraction")
            continue
        if _value_match(g["value"], item.get("value")):
            correct += 1
        else:
            failures.append(f"{key}: value {item.get('value')!r} != gold {g['value']!r}")
            # The right value attached to the WRONG field is the
            # cross-attribution form of wrong-source.
            for other_key, other_gold in gold.items():
                if other_key != key and _value_match(other_gold["value"], item.get("value")):
                    wrong_source += 1
                    failures.append(f"{key}: value belongs to {other_key} (wrong source)")
        snippet = _norm(item.get("source_snippet") or "")
        if snippet:
            snippet_checks += 1
            if snippet in doc_norm:
                grounded += 1
            else:
                failures.append(f"{key}: source snippet not grounded in the document")
    if extra:
        failures.append(f"{extra} extraction(s) outside the requested schema")
    n = len(gold) or 1
    metrics = {
        "field_accuracy": round(correct / n, 4),
        "grounded_rate": round(grounded / snippet_checks, 4) if snippet_checks else 1.0,
        "wrong_source": wrong_source,
        "extra_fields": extra,
    }
    return _result(case, metrics, failures)


# ---------------------------------------------------------------------------
# consistency


def score_consistency(case: dict, output: dict) -> dict:
    findings = output.get("findings") or []
    expect = bool(case["gold"]["expect_contradiction"])
    detected = len(findings) > 0
    failures: list[str] = []
    if detected != expect:
        failures.append(
            f"contradiction expected={expect} but detected={detected}")
    matched = False
    severity_ok = False
    if expect and findings:
        gold_sets = [set(f["fields"]) for f in case["gold"]["findings"]]
        gold_sev = {tuple(sorted(f["fields"])): f["severity"]
                    for f in case["gold"]["findings"]}
        for f in findings:
            fs = set(f.get("fields") or [])
            if fs in gold_sets:
                matched = True
                if f.get("severity") == gold_sev.get(tuple(sorted(fs))):
                    severity_ok = True
        if not matched:
            failures.append("no finding matches a gold field set")
        elif not severity_ok:
            failures.append("finding severity differs from gold")
    metrics = {
        "contradiction_detected": detected,
        "field_set_match": matched if expect else (not detected),
        "severity_match": severity_ok if expect else True,
        "finding_count": len(findings),
    }
    return _result(case, metrics, failures)


# ---------------------------------------------------------------------------
# attorney review


def score_attorney(case: dict, output: dict) -> dict:
    findings = output.get("findings") or []
    failures: list[str] = []
    found = {(f.get("check"), f.get("layer")) for f in findings}
    for g in case["gold"]["findings"]:
        if (g["check"], g["layer"]) not in found:
            failures.append(f"missing finding: {g['check']} ({g['layer']})")
        else:
            match = next(f for f in findings
                         if f.get("check") == g["check"] and f.get("layer") == g["layer"])
            if match.get("severity") != g["severity"]:
                failures.append(
                    f"{g['check']}: severity {match.get('severity')!r} != gold {g['severity']!r}")
    gold_checks = {g["check"] for g in case["gold"]["findings"]}
    surprise = [f for f in findings
                if f.get("severity") == "should_fix" and f.get("check") not in gold_checks]
    if surprise:
        failures.append(
            f"unexpected should_fix finding(s): {[f.get('check') for f in surprise]}")
    metrics = {
        "expected_found": len(case["gold"]["findings"]) - sum(
            1 for f in failures if f.startswith("missing finding")),
        "expected_total": len(case["gold"]["findings"]),
        "surprise_should_fix": len(surprise),
        "finding_count": len(findings),
    }
    return _result(case, metrics, failures)


# ---------------------------------------------------------------------------
# vault analyst


def score_vault(case: dict, output: dict) -> dict:
    gold = case["gold"]
    failures: list[str] = []
    type_ok = output.get("document_type") == gold["document_type"]
    if not type_ok:
        failures.append(
            f"document_type {output.get('document_type')!r} != gold {gold['document_type']!r}")
    labels = {f.get("label") for f in output.get("key_facts") or []}
    missing = [l for l in gold.get("required_key_fact_labels") or [] if l not in labels]
    if missing:
        failures.append(f"missing key-fact labels: {missing}")
    tags = set(output.get("suggested_tags") or [])
    missing_tags = [t for t in gold.get("required_tags") or [] if t not in tags]
    if missing_tags:
        failures.append(f"missing required tags: {missing_tags}")
    forbidden = [t for t in gold.get("forbidden_tags") or [] if t in tags]
    if forbidden:
        failures.append(f"forbidden tags present: {forbidden}")
    metrics = {
        "type_match": type_ok,
        "labels_found": len(labels & set(gold.get("required_key_fact_labels") or [])),
        "labels_required": len(gold.get("required_key_fact_labels") or []),
    }
    return _result(case, metrics, failures)


# ---------------------------------------------------------------------------
# speech to text


def _wer(expected: str, actual: str) -> float:
    e = _norm(expected).split()
    a = _norm(actual).split()
    if not e:
        return 0.0 if not a else 1.0
    # word-level Levenshtein
    prev = list(range(len(a) + 1))
    for i, ew in enumerate(e, 1):
        cur = [i] + [0] * len(a)
        for j, aw in enumerate(a, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ew != aw))
        prev = cur
    return prev[len(a)] / len(e)


def score_stt(case: dict, transcript: str) -> dict:
    gold = case["gold"]
    wer = _wer(gold["expected_text"], transcript)
    failures: list[str] = []
    if wer > gold["max_wer"]:
        failures.append(f"WER {wer:.3f} exceeds max {gold['max_wer']}")
    transcript_norm = _norm(transcript)
    missing_terms = [t for t in gold["critical_terms"]
                     if _norm(t) not in transcript_norm]
    if missing_terms:
        failures.append(f"critical terms missing: {missing_terms}")
    metrics = {
        "wer": round(wer, 4),
        "critical_terms_found": len(gold["critical_terms"]) - len(missing_terms),
        "critical_terms_total": len(gold["critical_terms"]),
    }
    return _result(case, metrics, failures)


# ---------------------------------------------------------------------------
# end-to-end conversations


def score_e2e(case: dict, output: dict) -> dict:
    gold = case["gold"]
    facts = gold["final_profile"]
    profile = output.get("final_profile") or {}
    failures: list[str] = []
    correct = 0
    wrong_source = 0
    applicable = 0
    # When the runner accounted wrong-source on the proposal stream
    # (harness terms), that count is authoritative; the per-field
    # attribution check is the fallback for runners that only ship a
    # final profile.
    stream_events = output.get("wrong_source_events")
    for key, gold_value in facts.items():
        entry = profile.get(key)
        if entry is None:
            continue  # not applicable / not reached: excluded from the denominator
        applicable += 1
        if entry.get("value_matches_gold") or _value_match(gold_value, entry.get("value")):
            correct += 1
        else:
            failures.append(f"{key}: value {entry.get('value')!r} != gold {gold_value!r}")
        if stream_events is None:
            sources = entry.get("source_paragraph_keys") or []
            if sources and key not in sources:
                wrong_source += 1
                failures.append(f"{key}: value sourced from a paragraph that never stated it (wrong source)")
    if stream_events is not None:
        wrong_source = int(stream_events)
        if wrong_source:
            failures.append(f"{wrong_source} wrong-source proposal event(s) on the proposal stream")
    denom = gold.get("applicable_fields") or applicable or 1
    accuracy = correct / denom if denom else 0.0
    if accuracy < 0.95:
        failures.append(f"profile accuracy {accuracy:.2f} below the 0.95 bar")
    esc_gold = sorted(gold.get("expected_escalations") or [])
    esc_got = sorted(output.get("escalations") or [])
    if esc_gold != esc_got:
        failures.append(f"escalations {esc_got} != gold {esc_gold}")
    if output.get("document_count") != gold.get("expected_document_count"):
        failures.append(
            f"document_count {output.get('document_count')} != gold {gold.get('expected_document_count')}")
    if (output.get("repeated_questions") or 0) != (gold.get("repeated_questions") or 0):
        failures.append(f"repeated_questions {output.get('repeated_questions')} != gold {gold.get('repeated_questions')}")
    metrics = {
        "field_accuracy": round(accuracy, 4),
        "wrong_source": wrong_source,
        "user_turns": output.get("user_turns"),
        "turn_budget": gold.get("turn_budget"),
        "documents": output.get("document_count"),
    }
    return _result(case, metrics, failures)


SCORERS = {
    "extraction-paragraphs": score_extraction,
    "consistency": score_consistency,
    "attorney-review": score_attorney,
    "vault-analyst": score_vault,
    "stt": score_stt,
    "conversations-e2e": score_e2e,
}
