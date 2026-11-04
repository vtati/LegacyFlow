"""Scorecard generation for eval runs.

Turns a list of evaluator result dicts (see evaluators.py) into:
- a machine-readable summary (per-suite pass rates, headline metrics,
  every hard-gate failure named), and
- a single self-contained HTML page suitable for showing live —
  no external assets, no JavaScript.

Suite → agent display names reflect the product's five agents plus
dictation.
"""
from __future__ import annotations

import html
from typing import Any

AGENT_NAMES = {
    "conversations-e2e": "Interviewer",
    "extraction-paragraphs": "Document Extractor",
    "consistency": "Consistency Reviewer",
    "attorney-review": "Attorney Reviewer",
    "vault-analyst": "Vault Analyst",
    "stt": "Dictation (speech-to-text)",
}

SUITE_ORDER = [
    "conversations-e2e",
    "extraction-paragraphs",
    "consistency",
    "attorney-review",
    "vault-analyst",
    "stt",
]


def summarize(results: list[dict], run_name: str) -> dict:
    suites: dict[str, dict[str, Any]] = {}
    for r in results:
        s = suites.setdefault(r["suite"], {
            "agent": AGENT_NAMES.get(r["suite"], r["suite"]),
            "cases": 0, "passed": 0, "failures": [],
        })
        s["cases"] += 1
        if r["passed"]:
            s["passed"] += 1
        for f in r["failures"]:
            s["failures"].append({"case_id": r["case_id"], "failure": f})
    for s in suites.values():
        s["pass_rate"] = round(s["passed"] / s["cases"], 4) if s["cases"] else 0.0
    total = sum(s["cases"] for s in suites.values())
    passed = sum(s["passed"] for s in suites.values())
    return {
        "run_name": run_name,
        "total_cases": total,
        "total_passed": passed,
        "overall_pass_rate": round(passed / total, 4) if total else 0.0,
        "suites": suites,
    }


def render_html(summary: dict, generated_at: str) -> str:
    esc = html.escape
    cards = []
    for suite in SUITE_ORDER:
        s = summary["suites"].get(suite)
        if not s:
            continue
        pct = int(round(s["pass_rate"] * 100))
        state = "pass" if s["pass_rate"] == 1.0 else ("warn" if s["pass_rate"] >= 0.8 else "fail")
        cards.append(
            f'<section class="card {state}"><h2>{esc(s["agent"])}</h2>'
            f'<div class="big">{pct}%</div>'
            f'<div class="sub">{s["passed"]} of {s["cases"]} cases passed</div></section>'
        )
    rows = []
    for suite in SUITE_ORDER:
        s = summary["suites"].get(suite)
        if not s:
            continue
        for f in s["failures"]:
            rows.append(
                f'<tr><td>{esc(s["agent"])}</td><td>{esc(f["case_id"])}</td>'
                f'<td>{esc(f["failure"])}</td></tr>'
            )
    failures_block = (
        '<h2 class="fh">Hard-gate failures</h2><table><tr><th>Agent</th><th>Case</th><th>Failure</th></tr>'
        + "".join(rows) + "</table>"
        if rows else '<p class="clean">No hard-gate failures in this run.</p>'
    )
    overall = int(round(summary["overall_pass_rate"] * 100))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LegacyFlow AI Eval Scorecard — {esc(summary['run_name'])}</title>
<style>
 body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; margin: 2rem auto; max-width: 960px; padding: 0 1rem; color: #1a2332; background: #f7f5f0; }}
 h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }}
 .meta {{ color: #5a6a7a; margin-bottom: 1.5rem; }}
 .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }}
 .card {{ background: #fff; border-radius: 12px; padding: 1.2rem; border-top: 6px solid #2e7d32; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
 .card.warn {{ border-top-color: #e6a100; }} .card.fail {{ border-top-color: #c62828; }}
 .card h2 {{ font-size: 1rem; margin: 0 0 .4rem; }} .big {{ font-size: 2.4rem; font-weight: 700; }}
 .sub {{ color: #5a6a7a; }}
 table {{ border-collapse: collapse; width: 100%; background: #fff; }}
 th, td {{ text-align: left; padding: .5rem .7rem; border-bottom: 1px solid #eee; font-size: .92rem; }}
 .fh {{ margin-top: 2rem; }} .clean {{ background: #e8f5e9; padding: 1rem; border-radius: 8px; }}
 .overall {{ font-size: 1.1rem; margin-bottom: 1.2rem; }}
</style></head><body>
<h1>LegacyFlow — AI Evaluation Scorecard</h1>
<div class="meta">Run: {esc(summary['run_name'])} · Generated: {esc(generated_at)}</div>
<div class="overall">Overall: <strong>{overall}%</strong> — {summary['total_passed']} of {summary['total_cases']} golden cases passed across every agent.</div>
<div class="grid">{''.join(cards)}</div>
{failures_block}
<p class="meta">Golden dataset v1 · deterministic evaluators (source-attribution aware) · full run detail in scorecard.json</p>
</body></html>"""
