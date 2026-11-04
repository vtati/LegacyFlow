#!/usr/bin/env python3
"""LegacyFlow eval runner (eval pipeline Phase 3).

Executes golden-dataset cases against the SAME code paths production
uses, scores them with the deterministic evaluators, and writes a run
directory containing results.json, scorecard.json and scorecard.html.

Legs:
- extraction-paragraphs / consistency: in-process, calling the
  production modules' own OpenAI legs (_extract_via_openai /
  _review_via_openai) with the production prompts — the same functions
  the API calls, minus persistence.
- vault-analyst: in-process via analyze_with_llm.
- stt: synthesizes each script with OpenAI TTS (cached under
  evals/audio/) and transcribes with the production Whisper provider.
- conversations-e2e: against the live API with the recording test
  account, replaying each persona's paragraphs by trigger match —
  the October A/B harness pattern. The throwaway matter is deleted
  afterwards (FK-safe order) unless --keep-matters is passed.
- attorney-review: NOT wired here — it needs a frozen packet plus
  snapshot mutation (the Oct 27 verify_seeded.py mechanics). The
  evaluator is unit-tested; the seeded runner lands with the baseline
  phase. Asking for this suite exits with an explanatory error.

Environment: OPENAI_API_KEY must be present for the model legs (the
runner never reads keys from anywhere else). E2E uses
EVAL_API_BASE (default the production API) and the recording account.

Usage:
    python -m evals.run_evals --suite extraction-paragraphs \
        --suite consistency --out evals/runs/2026-11-04-baseline
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).resolve().parent / "golden"
AUDIO_DIR = Path(__file__).resolve().parent / "audio"

sys.path.insert(0, str(API_ROOT))
os.environ.setdefault("AI_LLM_PROVIDER", "openai")

from evals.evaluators import SCORERS  # noqa: E402
from evals.scorecard import render_html, summarize  # noqa: E402

DEFAULT_MODEL = "gpt-5.5"
VAULT_MODEL = "gpt-4o-mini"


def load_cases(suite: str) -> list[dict]:
    path = GOLDEN / f"{suite}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _pack_labels() -> dict[str, str]:
    import yaml

    labels: dict[str, str] = {}
    qdir = API_ROOT / "content-packs" / "us-va" / "v2" / "question-sets"
    for f in sorted(qdir.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text())
        for section in (doc or {}).get("sections") or []:
            for q in section.get("questions") or []:
                if q.get("key") and q.get("label"):
                    labels[q["key"]] = q["label"]
    return labels


# ---------------------------------------------------------------------------
# in-process legs


def leg_extraction(case: dict, model: str) -> dict:
    from app.domains.interview.extraction import _extract_via_openai

    labels = _pack_labels()
    schema = [{"key": k, "label": labels.get(k, k.replace("_", " ").title())}
              for k in case["input"]["question_keys"]]
    items, _usage = _extract_via_openai(
        model=model, document_text=case["input"]["document_text"],
        field_schema=schema)
    return {"extractions": items}


def leg_consistency(case: dict, model: str) -> dict:
    from app.domains.interview.consistency import _review_via_openai

    facts = [{"key": f["key"], "value": f["value"],
              "provenance": f.get("source", "interview")}
             for f in case["input"]["facts"]]
    items, _usage = _review_via_openai(model=model, facts=facts)
    findings = [{"fields": it.get("fields") or [], "severity": it.get("severity"),
                 "question": it.get("question")} for it in items]
    return {"findings": findings}


def leg_vault(case: dict, model: str) -> dict:
    os.environ.setdefault("OPENAI_CHAT_MODEL", model)
    from app.domains.files.analysis import analyze_with_llm

    return analyze_with_llm(case["input"]["extracted_text"],
                            case["input"]["filename"])


def leg_stt(case: dict, model: str) -> str:
    AUDIO_DIR.mkdir(exist_ok=True)
    clip = AUDIO_DIR / f"{case['id']}.mp3"
    if not clip.exists():
        key = os.environ["OPENAI_API_KEY"]
        with _httpx_client() as client:
            resp = client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "tts-1", "voice": "alloy",
                      "input": case["input"]["script"]})
        resp.raise_for_status()
        clip.write_bytes(resp.content)
    from app.domains.ai.provider import get_stt_provider

    return get_stt_provider().transcribe(
        clip.read_bytes(), filename=clip.name, language="en")


# ---------------------------------------------------------------------------
# e2e leg (live API) — a faithful port of the October A/B harness engine
# (interview_ab.py run_persona), adapted to emit the scorer's contract.
# The harness mechanics matter: proposals carry proposed_value + source,
# only value-matching proposals are confirmed (mismatches rejected),
# bundle offers are accepted with a ride-along paragraph, and paragraph
# selection is progress-driven with stall detection. Wrong-source is
# accounted on the proposal stream in the harness's own terms and
# passed to the scorer as wrong_source_events.

_PERSON_KEYS = {
    "primary_guardian_person", "backup_guardian_person",
    "primary_executor_person", "backup_executor_person",
    "poa_agent_person", "successor_poa_agent_person",
    "healthcare_agent_person", "successor_healthcare_agent_person",
    "successor_trustee_person",
}
_ADDRESS_KEYS = {
    "primary_executor_address", "poa_agent_address", "primary_guardian_address",
}
_ASSET_ALIASES = {
    "bank": "bank", "bank accounts": "bank",
    "brokerage": "brokerage", "brokerage / investment accounts": "brokerage",
    "investment": "brokerage", "real_property": "real_property",
    "real estate": "real_property", "retirement": "retirement",
    "insurance": "insurance", "life insurance": "insurance",
    "vehicle": "vehicle", "business": "business", "529": "529",
    "digital": "digital",
}
_STOP = {"the", "and", "our", "your", "with", "that", "this", "have",
         "has", "are", "for", "she", "her", "his", "who", "all"}
_BUNDLE_QUESTION_RE = re.compile(
    r"(quick question:|also serve as|also use your address|would you like .* to also)",
    re.IGNORECASE)
_KEY_ASK_PATTERNS = {
    "primary_executor_person": r"serve as the executor|executor of your will|your executor should|who.*executor",
    "backup_executor_person": r"backup executor|executor if your first",
    "poa_agent_person": r"handle your financial affairs|financial power of attorney agent|who.*financial agent",
    "successor_poa_agent_person": r"successor.*financial|financial.*successor",
    "healthcare_agent_person": r"make healthcare decisions|healthcare agent(?! if)|medical decisions for you",
    "successor_healthcare_agent_person": r"successor healthcare|healthcare agent if",
    "successor_trustee_person": r"successor trustee|take over managing the trust",
    "primary_guardian_person": r"care for your (minor )?children|guardian for (our|your) children|who should.*guardian",
    "backup_guardian_person": r"backup guardian",
    "primary_executor_address": r"city and state where your executor|executor.*\baddress\b",
    "poa_agent_address": r"city and state does your financial agent|financial agent.*\baddress\b",
    "primary_guardian_address": r"guardian.*\baddress\b|address of your.*guardian",
    "healthcare_agent_phone": r"phone number.*healthcare|healthcare agent.*phone",
    "testator_full_name": r"your full legal name|what is your (full )?name",
    "testator_street_address": r"your street address",
    "testator_city": r"what city do you live in|which city do you live in|city do you (live|reside) in",
    "marital_status": r"what best describes your household|are you (currently )?(married|single|divorced|widowed)|your marital status",
    "primary_residence_state": r"state is your primary residence|state your primary residence is in",
    "has_child_with_special_needs": r"special needs",
    "has_prior_marriage_or_children_outside_current_relationship": r"married before|prior marriage|previous marriage|children from (a|outside)|children outside",
    "owns_real_estate_outside_primary_jurisdiction": r"real estate (in|outside) (another|a different)|own (any )?(real estate|property) (in|outside)|property outside",
    "owns_or_controls_business_interest": r"business interest|own (a|any) business",
    "expects_family_conflict": r"family conflict|contest(ed)? (your|the) (will|estate)",
    "wants_poa_document": r"power of attorney document|want a durable financial power of attorney|do you want.*power of attorney",
    "wants_healthcare_directive": r"healthcare directive \(living will\) document|advance healthcare directive|want an advance",
    "has_minor_children": r"do you have minor children|have minor children or dependents",
    "minor_children_details": r"list each (minor )?child|each child by name|names and ages of",
    "guardian_reason": r"why is this person a good fit|a good fit",
    "asset_categories_present": r"which of these do you have",
    "has_debts": r"do you have significant debts|have (any )?significant debts|any outstanding debts",
    "has_retirement_accounts": r"do you have retirement accounts",
    "has_life_insurance": r"do you have a life insurance policy",
    "retirement_beneficiaries_current": r"beneficiary on your retirement accounts",
    "life_insurance_beneficiaries_current": r"beneficiary on your life insurance",
    "has_specific_bequests": r"specific items or amounts you want to leave",
    "residuary_beneficiaries": r"receive everything else|the rest of your estate|who should receive everything",
    "wants_revocable_trust": r"do you want a revocable|want a revocable living trust|revocable living trust to hold",
    "trust_initial_property": r"assets should go into the trust|go into the trust",
    "trust_distribution_age": r"at what age should|age should your children receive",
    "trust_marital_share": r"share of the trust should go to your spouse|share.*go to your spouse outright",
    "poa_additional_powers": r"specific financial powers you want to grant or withhold",
    "living_will_directives": r"wishes about life-prolonging treatment|life-prolonging treatment",
    "healthcare_agent_limitations": r"limits on what your healthcare agent may decide",
    "organ_donation_wishes": r"donate organs|organ donation|donate.*tissue",
}
_ASK_EXCLUSIONS = {
    "healthcare_agent_person": ["phone", "limits", "limitations", "successor"],
    "poa_agent_person": ["powers", "document", "successor"],
    "primary_guardian_person": ["address", "city and state", "backup", "if your first"],
    "primary_executor_person": ["address", "city and state", "backup", "if your first"],
}


def _vnorm(v: Any) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(v).lower()).strip()


def _value_matches(key: str, proposed: Any, expected: Any) -> bool:
    """The harness's semantic matcher (person/address/asset aware)."""
    if isinstance(expected, bool):
        if isinstance(proposed, bool):
            return proposed is expected
        return _vnorm(proposed) in ("true", "yes") if expected else _vnorm(proposed) in ("false", "no")
    if isinstance(expected, list):
        got = proposed if isinstance(proposed, list) else [proposed]
        codes = {_ASSET_ALIASES.get(_vnorm(i), _vnorm(i)) for i in got}
        return set(expected) <= codes or bool(set(expected) & codes)
    if key in _PERSON_KEYS:
        pnorm = _vnorm(proposed)
        return all(part in pnorm for part in _vnorm(expected).split() if len(part) > 2)
    if key in _ADDRESS_KEYS:
        pnorm = _vnorm(proposed)
        tokens = [t for t in _vnorm(expected).split() if len(t) > 3 and t not in _STOP]
        return sum(1 for t in tokens if t in pnorm) >= max(1, len(tokens) - 1)
    pnorm, enorm = _vnorm(proposed), _vnorm(expected)
    if not pnorm:
        return False
    if enorm in pnorm or pnorm in enorm:
        return True
    if key == "minor_children_details":
        names = re.findall(r"[A-Z][a-z]+ [A-Z][a-z]+", str(expected))
        return sum(1 for n in names if _vnorm(n) in pnorm) >= 2
    etoks = {t for t in enorm.split() if (len(t) > 4 or t.isdigit()) and t not in _STOP}
    return len(etoks & set(pnorm.split())) >= 2


def _asked_keys_for(reply: str) -> set[str]:
    if _BUNDLE_QUESTION_RE.search(reply):
        return set()
    keys: set[str] = set()
    for sentence in re.split(r"(?<=[.!])\s+", reply):
        if "?" not in sentence:
            continue
        low = sentence.lower()
        for key, pattern in _KEY_ASK_PATTERNS.items():
            if any(x in low for x in _ASK_EXCLUSIONS.get(key, [])):
                continue
            if re.search(pattern, sentence, re.IGNORECASE):
                keys.add(key)
    return keys


def _first_household_id(payload: Any) -> str | None:
    """Households endpoint returns a bare list (verified live); tolerate
    the wrapped shapes too."""
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("households") or payload.get("items") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0].get("id")
    return None


def leg_e2e(case: dict, api_base: str, keep_matter: bool) -> dict:
    import uuid as _uuid

    facts = case["gold"]["final_profile"]
    paragraphs = case["input"]["paragraphs"]
    token = _api_token(api_base)
    _c, households = _http("GET", f"{api_base}/v1/households", token, None)
    household_id = os.environ.get(
        "EVAL_HOUSEHOLD_ID") or _first_household_id(households)
    _c, matter = _http("POST", f"{api_base}/v1/matters", token, {
        "jurisdiction_code": "US-VA",
        "title": f"[EVAL] {case['id']} {case['input']['persona']}",
        "plan_type": "bundle", "household_id": household_id})
    mid = matter["id"]

    def answers_now() -> dict:
        _c, data = _http("GET", f"{api_base}/v1/matters/{mid}/intake/answers",
                         token, None)
        rows = data.get("answers") if isinstance(data, dict) else data
        out: dict[str, Any] = {}
        if isinstance(rows, dict):
            out = dict(rows)
        elif isinstance(rows, list):
            for r in rows:
                v = r.get("answer")
                if isinstance(v, dict) and "value" in v:
                    v = v["value"]
                out[r["question_key"]] = v
        return out

    def progress_now() -> tuple[int, int]:
        _c, data = _http("GET", f"{api_base}/v1/matters/{mid}/intake/progress",
                         token, None)
        ov = (data or {}).get("overall") or {}
        return int(ov.get("required_answered") or 0), int(ov.get("required_total") or 0)

    try:
        _c, session = _http(
            "POST", f"{api_base}/v1/matters/{mid}/interview/sessions",
            token, {"question_set_id": "household"})
        sid = session["id"]
        provided: set[str] = set()
        sent_paras: set[int] = set()
        asked_before: set[str] = set()
        repeated_events = 0
        wrong_source_events = 0
        attribution: dict[str, list[str]] = {}
        prev_answers: dict[str, Any] = {}
        user_turns = 0
        last_progress = -1
        stall = 0
        completed = False

        message = paragraphs[0]["text"]
        sent_paras = {0}
        provided |= set(paragraphs[0]["keys"])
        current_keys = list(paragraphs[0]["keys"])

        for _turn in range(1, 41):
            user_turns += 1
            code, out = _http(
                "POST",
                f"{api_base}/v1/matters/{mid}/interview/sessions/{sid}/turns",
                token, {"message": message,
                        "idempotency_key": str(_uuid.uuid4())})
            if code != 200:
                raise RuntimeError(f"turn failed: {code} {str(out)[:200]}")
            reply = str(out.get("reply") or "")
            answers_at_start = answers_now()

            # wrong-source accounting on the proposal stream (harness terms)
            for prop in out.get("proposals") or []:
                key = prop.get("question_key")
                if key not in facts:
                    continue
                val = prop.get("proposed_value")
                if prop.get("source") == "bundle":
                    if not _value_matches(key, val, facts[key]):
                        wrong_source_events += 1
                    continue
                if key not in provided:
                    wrong_source_events += 1
                elif not _value_matches(key, val, facts[key]):
                    wrong_source_events += 1

            # confirm value-matching open proposals; reject the rest
            _c, open_props = _http(
                "GET",
                f"{api_base}/v1/matters/{mid}/interview/sessions/{sid}/proposals",
                token, None)
            for prop in open_props if isinstance(open_props, list) else []:
                key = prop.get("question_key")
                if key in facts and _value_matches(
                        key, prop.get("proposed_value"), facts[key]):
                    _http("POST",
                          f"{api_base}/v1/matters/{mid}/interview/proposals/{prop['id']}/confirm",
                          token, {})
                elif key in facts:
                    _http("POST",
                          f"{api_base}/v1/matters/{mid}/interview/proposals/{prop['id']}/reject",
                          token, {})

            # attribution: keys that newly appeared ride on this turn's paragraph
            now_answers = answers_now()
            for key in now_answers:
                if key not in prev_answers and key not in attribution:
                    attribution[key] = list(current_keys)
            prev_answers = now_answers

            # repeat detection
            for key in _asked_keys_for(reply):
                if key in answers_at_start and key in asked_before:
                    repeated_events += 1
            asked_before |= _asked_keys_for(reply)

            answered_req, total_req = progress_now()
            if total_req and answered_req >= total_req:
                completed = True
                break
            if answered_req == last_progress:
                stall += 1
            else:
                stall = 0
                last_progress = answered_req

            # choose the next user message
            if _BUNDLE_QUESTION_RE.search(reply):
                for key, val in facts.items():
                    if key in _PERSON_KEYS and isinstance(val, str):
                        parts = [pt for pt in _vnorm(val).split() if len(pt) > 2]
                        if all(pt in _vnorm(reply) for pt in parts):
                            for sib in _PERSON_KEYS:
                                if facts.get(sib) == val:
                                    provided.add(sib)
                if "address" in reply.lower():
                    provided |= _ADDRESS_KEYS
                message = "Yes, please — that works for all of those."
                current_keys = []
                for i, p in enumerate(paragraphs):
                    if i not in sent_paras:
                        sent_paras.add(i)
                        provided |= set(p["keys"])
                        current_keys = list(p["keys"])
                        message += "\n\nAlso: " + p["text"]
                        break
                continue
            nxt = None
            for i, p in enumerate(paragraphs):
                if i not in sent_paras and re.search(p["trigger"], reply, re.IGNORECASE):
                    nxt = i
                    break
            if nxt is None:
                for i, p in enumerate(paragraphs):
                    if i not in sent_paras:
                        nxt = i
                        break
            if nxt is None:
                missing = [k for k in facts if k not in now_answers]
                for i, p in enumerate(paragraphs):
                    if any(k in missing for k in p["keys"]):
                        nxt = i
                        break
            if nxt is None or stall >= 5:
                break
            sent_paras.add(nxt)
            provided |= set(paragraphs[nxt]["keys"])
            current_keys = list(paragraphs[nxt]["keys"])
            message = paragraphs[nxt]["text"]

        final_answers = answers_now()
        profile = {}
        for key, gold in facts.items():
            if key in final_answers:
                raw = final_answers[key]
                profile[key] = {
                    "value": raw,
                    "source_paragraph_keys": attribution.get(key, []),
                    "value_matches_gold": _value_matches(key, raw, gold),
                }
        _c, esc = _http("GET", f"{api_base}/v1/matters/{mid}/escalations",
                        token, None)
        esc_rules = sorted({e.get("rule_code") or e.get("code") or ""
                            for e in (esc if isinstance(esc, list) else [])} - {""})
        return {
            "final_profile": profile,
            "escalations": esc_rules,
            "document_count": _generate_documents(api_base, token, mid),
            "user_turns": user_turns,
            "repeated_questions": repeated_events,
            "wrong_source_events": wrong_source_events,
            "completed": completed,
            "_matter_id": mid,
        }
    finally:
        if not keep_matter:
            _delete_matter(mid)


def _generate_documents(api_base: str, token: str, mid: str) -> int:
    _c, templates = _http("GET", f"{api_base}/v1/document-templates", token, None)
    tpls = (templates if isinstance(templates, list)
            else templates.get("templates") or [])
    count = 0
    for t in tpls:
        if not t.get("id"):
            continue
        c1, pkg = _http("POST", f"{api_base}/v1/matters/{mid}/document-packages",
                        token, {"template_id": t["id"]})
        if c1 in (200, 201) and pkg.get("id"):
            c2, _v = _http("POST", f"{api_base}/v1/packages/{pkg['id']}/generate-demo",
                           token, {})
            if c2 in (200, 201):
                count += 1
    return count


def _delete_matter(matter_id: str) -> None:
    """FK-safe delete via Management SQL (same order as the runbooks)."""
    import subprocess

    stmts = [
        "DELETE FROM document_versions WHERE package_id IN (SELECT id FROM document_packages WHERE matter_id='{m}')",
        "DELETE FROM document_packages WHERE matter_id='{m}'",
        "DELETE FROM vault_shares WHERE matter_id='{m}'",
        "DELETE FROM interview_turn_bodies WHERE turn_id IN (SELECT id FROM interview_turns WHERE session_id IN (SELECT id FROM interview_sessions WHERE matter_id='{m}'))",
        "DELETE FROM interview_turns WHERE session_id IN (SELECT id FROM interview_sessions WHERE matter_id='{m}')",
        "DELETE FROM field_proposals WHERE matter_id='{m}'",
        "DELETE FROM interview_parked_items WHERE matter_id='{m}'",
        "DELETE FROM interview_sessions WHERE matter_id='{m}'",
        "DELETE FROM intake_answers WHERE matter_id='{m}'",
        "DELETE FROM nominations WHERE matter_id='{m}'",
        "DELETE FROM audit_events WHERE matter_id='{m}'",
        "DELETE FROM matters WHERE id='{m}'",
    ]
    sql = ";\n".join(s.format(m=matter_id) for s in stmts) + ";"
    path = Path("/tmp/eval_matter_cleanup.sql")
    path.write_text(sql)
    subprocess.run(
        ["python3", os.path.expanduser(
            "~/workspace/custom-connectors/custom.supabase-management/bin/run_sql.py"),
         "nlrxlpjkrmvubvsfepjs", str(path)],
        capture_output=True, timeout=120)


# ---------------------------------------------------------------------------


def run_suite(suite: str, model: str, api_base: str, keep_matters: bool,
              dry_run: bool) -> list[dict]:
    cases = load_cases(suite)
    scorer = SCORERS[suite]
    results = []
    for case in cases:
        if dry_run:
            output: Any = {}
        elif suite == "extraction-paragraphs":
            output = leg_extraction(case, model)
        elif suite == "consistency":
            output = leg_consistency(case, model)
        elif suite == "vault-analyst":
            output = leg_vault(case, VAULT_MODEL)
        elif suite == "stt":
            output = leg_stt(case, model)
        elif suite == "conversations-e2e":
            output = leg_e2e(case, api_base, keep_matters)
        elif suite == "attorney-review":
            raise SystemExit(
                "attorney-review is scored by the seeded-API runner "
                "(Oct 27 verify_seeded mechanics) in the baseline phase; "
                "its evaluator is unit-tested but the live leg is not "
                "wired into run_evals yet.")
        else:  # pragma: no cover
            raise SystemExit(f"unknown suite {suite}")
        scored = scorer(case, output) if not dry_run else {
            "case_id": case["id"], "suite": suite, "passed": False,
            "metrics": {}, "failures": ["dry-run: not executed"]}
        scored["output"] = output if not isinstance(output, str) else {"transcript": output}
        results.append(scored)
        print(f"  {case['id']}: {'PASS' if scored['passed'] else 'FAIL'} "
              f"{scored['failures'][:1] if scored['failures'] else ''}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", action="append", dest="suites",
                    choices=sorted(SCORERS.keys()))
    ap.add_argument("--out", default=None, help="run output directory")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--api-base",
                    default=os.environ.get("EVAL_API_BASE",
                                           "https://legacyflow-api.genalphalabs.com"))
    ap.add_argument("--keep-matters", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="load cases and wire scorers without model calls")
    args = ap.parse_args()

    suites = args.suites or ["extraction-paragraphs", "consistency", "vault-analyst"]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_name = args.run_name or f"eval-{stamp}"
    out_dir = Path(args.out or (Path(__file__).resolve().parent / "runs" / run_name))
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    for suite in suites:
        print(f"== {suite}")
        all_results.extend(run_suite(suite, args.model, args.api_base,
                                     args.keep_matters, args.dry_run))
    summary = summarize(all_results, run_name)
    (out_dir / "results.json").write_text(json.dumps(
        {"run_name": run_name, "generated_at": stamp, "results": all_results},
        indent=2))
    (out_dir / "scorecard.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "scorecard.html").write_text(render_html(summary, stamp))
    print(f"\n{summary['total_passed']}/{summary['total_cases']} passed "
          f"({int(summary['overall_pass_rate'] * 100)}%) — {out_dir}")
    return 0 if summary["overall_pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
