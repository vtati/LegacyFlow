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
# e2e leg (live API, October A/B harness pattern)


def _httpx_client() -> Any:
    """httpx client for runner traffic. trust_env=False + an explicit
    proxy: sandbox NO_PROXY lists carry bracketed IPv6 entries that
    crash httpx's env parsing (InvalidURL ':1]'), and every runner
    target is an external host that should ride the egress proxy."""
    import httpx

    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"))
    return httpx.Client(trust_env=False, proxy=proxy, timeout=180.0)


def _http(method: str, url: str, token: str | None, body: dict | None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with _httpx_client() as client:
        resp = client.request(method, url, headers=headers, json=body)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {}


def _api_token(api_base: str) -> str:
    supa = os.environ.get("SUPABASE_URL", "https://nlrxlpjkrmvubvsfepjs.supabase.co")
    anon = os.environ["SUPABASE_ANON_KEY"]
    email = os.environ.get("EVAL_ACCOUNT_EMAIL", "test.recording@legacyflow.app")
    password = os.environ["EVAL_ACCOUNT_PASSWORD"]
    with _httpx_client() as client:
        resp = client.post(
            f"{supa}/auth/v1/token?grant_type=password",
            headers={"apikey": anon, "Content-Type": "application/json"},
            json={"email": email, "password": password})
    return resp.json()["access_token"]


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
    token = _api_token(api_base)
    _c, households = _http("GET", f"{api_base}/v1/households", token, None)
    household_id = os.environ.get(
        "EVAL_HOUSEHOLD_ID") or _first_household_id(households)
    _c, matter = _http("POST", f"{api_base}/v1/matters", token, {
        "jurisdiction_code": "US-VA",
        "title": f"[EVAL] {case['id']} {case['input']['persona']}",
        "plan_type": "bundle", "household_id": household_id})
    mid = matter["id"]
    try:
        _c, session = _http(
            "POST", f"{api_base}/v1/matters/{mid}/interview/sessions",
            token, {"question_set_id": "household"})
        sid = session["id"]
        paragraphs = case["input"]["paragraphs"]
        profile: dict[str, dict] = {}
        asked_texts: list[str] = []
        turns = 0
        pending = list(paragraphs)
        # Opening turn: the interviewer speaks first via an empty-ish nudge.
        reply = ""
        for _ in range(40):
            nxt = None
            for p in pending:
                if re.search(p["trigger"], reply, re.I):
                    nxt = p
                    break
            if nxt is None and pending and turns == 0:
                nxt = pending[0]
            if nxt is None:
                break
            pending.remove(nxt)
            code, turn = _http(
                "POST",
                f"{api_base}/v1/matters/{mid}/interview/sessions/{sid}/turns",
                token, {"message": nxt["text"]})
            if code != 200:
                raise RuntimeError(f"turn failed: {code} {str(turn)[:200]}")
            turns += 1
            reply = turn.get("reply") or ""
            if reply:
                asked_texts.append(reply)
            _c, props = _http(
                "GET",
                f"{api_base}/v1/matters/{mid}/interview/sessions/{sid}/proposals",
                token, None)
            prop_list = (props if isinstance(props, list)
                         else props.get("proposals") or [])
            for prop in prop_list:
                key = prop.get("question_key")
                _http("POST",
                      f"{api_base}/v1/matters/{mid}/interview/proposals/{prop['id']}/confirm",
                      token, {})
                if key:
                    profile[key] = {
                        "value": prop.get("value"),
                        "source_paragraph_keys": list(nxt["keys"]),
                    }
            if not pending:
                break
        repeated = _count_repeated(asked_texts)
        _c, esc = _http("GET", f"{api_base}/v1/matters/{mid}/escalations",
                        token, None)
        esc_rules = sorted({e.get("rule_code") or e.get("code") or ""
                            for e in (esc if isinstance(esc, list) else [])} - {""})
        doc_count = _generate_documents(api_base, token, mid)
        return {
            "final_profile": profile,
            "escalations": esc_rules,
            "document_count": doc_count,
            "user_turns": turns,
            "repeated_questions": repeated,
            "_matter_id": mid,
        }
    finally:
        if not keep_matter:
            _delete_matter(mid)


def _count_repeated(asked_texts: list[str]) -> int:
    seen: dict[str, int] = {}
    repeated = 0
    for t in asked_texts:
        norm = re.sub(r"\s+", " ", t.lower()).strip()[:120]
        if norm in seen:
            repeated += 1
        seen[norm] = 1
    return repeated


def _generate_documents(api_base: str, token: str, mid: str) -> int:
    _c, templates = _http("GET", f"{api_base}/v1/document-templates", token, None)
    tpls = (templates if isinstance(templates, list)
            else templates.get("templates") or [])
    count = 0
    for t in tpls:
        tid = t.get("id") or t.get("template_type")
        if not tid:
            continue
        c1, pkg = _http("POST", f"{api_base}/v1/matters/{mid}/document-packages",
                        token, {"template_id": tid} if t.get("id") else {"template_type": tid})
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
