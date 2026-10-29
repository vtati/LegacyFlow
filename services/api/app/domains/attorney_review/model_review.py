"""Attorney Reviewer — Layer 2 model judgment (plan 2026-10-27, §9.3/§9.4).

One fenced model call per run over the FROZEN context + rendered texts,
through the shared JSON gateway (never a hand-rolled call site). The
model may only flag: every finding must quote the exact span it relies
on, legal-conclusion phrasing is discarded server-side, and judgment
findings are advisory by construction.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.domains.ai.provider import get_json_provider

from .checks import Finding

logger = logging.getLogger(__name__)

#: Usage-dict marker set when the model's output could not be parsed
#: into a findings object (ERR-020). The service strips it before the
#: usage reaches the trace and marks the run degraded — previously an
#: unparseable response silently produced zero findings under a real
#: model_id, indistinguishable from a clean review.
UNPARSEABLE_MARKER = "model_output_unparseable"

PROMPT_VERSION = "attorney_review-v1"
TRACE_PROMPT_TEMPLATE = "attorney_review"

ALLOWED_CHECKS = frozenset({
    "free_text_contradiction",
    "implausible_value",
    "missing_context",
    "ambiguity",
})
#: Checks that are questions by construction (§9.3) — always advisory questions.
_QUESTION_CHECKS = frozenset({"missing_context", "ambiguity"})
_ALLOWED_SEVERITIES = frozenset({"should_fix", "question_for_attorney"})
#: Legal-conclusion phrasing is forbidden in findings (§9.5 register:
#: "these two names differ", never "this is invalid").
_FORBIDDEN_PHRASES = (
    "invalid",
    "unenforceable",
    "illegal",
    "is void",
    "void ab initio",
    "you must",
    "legal advice",
    "not legally",
    "not valid",
    "no legal effect",
)
_MAX_FINDINGS = 25
_MAX_BODY_CHARS = 12000

_SYSTEM_PROMPT = """\
You are a careful pre-review assistant for an estate-planning product.
You receive the FROZEN facts of a family's plan (the exact values their
draft documents were rendered from) plus the rendered draft texts
themselves. An attorney will review these drafts next; your job is to
flag what a careful first read would catch, so the family can fix it
first.

Flag ONLY these kinds of issues:
- free_text_contradiction: narrative text (bequests, wishes, notes) that
  conflicts with the structured facts or with another document.
- implausible_value: values that cannot all be true at once — a bequest
  larger than the stated estate, dates or ages in free text that
  disagree with the structured facts.
- missing_context: a situation a reasonable attorney would ask about —
  a child mentioned in text who is not in the household records, a
  prior marriage referenced but not recorded, property in another
  state. Phrase these as the question the attorney would ask.
- ambiguity: a reference in free text that could attach to two
  different people, or a term the family invented that is undefined.

HARD RULES:
- Never state a legal conclusion. Do not say a document is invalid,
  unenforceable, illegal, or void, and never give legal advice. State
  what differs from what, in plain language.
- Every finding must include "excerpt": the EXACT verbatim span from
  the named document that the finding relies on. If you cannot quote
  it, do not report it.
- "document" must be one of the document types listed below.
- Only flag genuine tensions grounded in the given material. Do not
  invent facts. An empty findings list is a valid, good answer.
- Keep explanation under 500 characters and suggested_fix under 300.

Respond with a single JSON object only, exactly:
{"findings": [{"check": str, "severity": "should_fix" | "question_for_attorney",
"document": str, "field": str | null, "excerpt": str,
"explanation": str, "suggested_fix": str}, ...]}
"""


def _user_content(snapshots: list[dict[str, Any]], source: dict[str, Any]) -> str:
    context: dict[str, Any] = {}
    for snap in snapshots:
        if snap.get("template_type") == "will":
            context = dict(snap.get("context") or {})
            break
    if not context and snapshots:
        context = dict(snapshots[0].get("context") or {})
    parts = [
        "FROZEN FACTS (the values the drafts were rendered from):",
        json.dumps(
            {
                "context": context,
                "persons": source.get("persons") or [],
                "answers": source.get("answers") or {},
            },
            default=str,
        )[:_MAX_BODY_CHARS],
        "",
        "DRAFT DOCUMENTS:",
    ]
    for snap in snapshots:
        body = str(snap.get("body_text") or "")[:_MAX_BODY_CHARS]
        parts.append(f"--- document: {snap.get('template_type')} ---")
        parts.append(body)
    return "\n".join(parts)


def _has_forbidden_phrasing(*texts: str) -> bool:
    joined = " ".join(t.lower() for t in texts if t)
    return any(phrase in joined for phrase in _FORBIDDEN_PHRASES)


def _validate(
    raw: Any, snapshots: list[dict[str, Any]]
) -> list[Finding]:
    bodies = {
        str(s.get("template_type")): str(s.get("body_text") or "")
        for s in snapshots
    }
    findings: list[Finding] = []
    if not isinstance(raw, list):
        return findings
    for item in raw[:_MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        check = str(item.get("check") or "")
        if check not in ALLOWED_CHECKS:
            continue
        document = str(item.get("document") or "")
        if document not in bodies:
            continue
        excerpt = str(item.get("excerpt") or "").strip()
        if not excerpt or excerpt not in bodies[document]:
            continue  # unquotable findings are discarded (§9.5)
        explanation = str(item.get("explanation") or "").strip()
        suggested_fix = str(item.get("suggested_fix") or "").strip()
        if not explanation or _has_forbidden_phrasing(explanation, suggested_fix):
            continue
        # §9.5: a judgment finding's suggested fix must be phrased as a
        # question for the attorney, never as an instruction. A
        # directive fix is dropped (the finding itself still stands).
        if suggested_fix and not suggested_fix.endswith("?"):
            suggested_fix = ""
        severity = str(item.get("severity") or "")
        if check in _QUESTION_CHECKS or severity not in _ALLOWED_SEVERITIES:
            severity = "question_for_attorney" if check in _QUESTION_CHECKS else severity
            if severity not in _ALLOWED_SEVERITIES:
                severity = "question_for_attorney"
        package_id = next(
            (s.get("package_id") for s in snapshots
             if str(s.get("template_type")) == document),
            None,
        )
        findings.append(Finding(
            layer="model",
            check=check,
            severity=severity,
            document=document,
            field=str(item.get("field")) if item.get("field") else None,
            excerpt=excerpt[:300],
            explanation=explanation[:500],
            suggested_fix=suggested_fix[:300] or None,
            package_id=package_id,
        ))
    return findings


def review_via_model(
    *,
    snapshots: list[dict[str, Any]],
    source: dict[str, Any],
    model: str,
) -> tuple[list[Finding], dict[str, int]]:
    """Call the JSON gateway once; return (validated findings, usage)."""
    started = time.monotonic()
    content, usage = get_json_provider(model=model).complete_json(
        system_prompt=_SYSTEM_PROMPT,
        user_content=_user_content(snapshots, source),
        temperature=0.0,
        timeout=90.0,
    )
    logger.info(
        "attorney_review provider call latency_ms=%d",
        int((time.monotonic() - started) * 1000),
    )
    try:
        parsed = json.loads(content)
    except (ValueError, AttributeError):
        parsed = {}
    raw = parsed.get("findings") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        # ERR-020: unparseable output is a degraded model pass, not a
        # clean zero-findings one — warn-log it and mark the usage so
        # the caller records the run as degraded.
        logger.warning(
            "attorney_review model output unparseable (no findings object); "
            "marking the run degraded"
        )
        return _validate(raw, snapshots), {**usage, UNPARSEABLE_MARKER: True}
    return _validate(raw, snapshots), usage
