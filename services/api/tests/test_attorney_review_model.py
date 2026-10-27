"""Attorney Reviewer — Layer 2 model-judgment validation (plan 2026-10-27).

The model may only flag, never conclude: findings are validated
server-side — the excerpt must quote the frozen document verbatim, the
check must be in the allowed set, and legal-conclusion phrasing is
discarded. A stub provider stands in for OpenAI.

All fixture data is synthetic.
"""

from __future__ import annotations

import json

import pytest

from app.domains.attorney_review import model_review

WILL_BODY = (
    "LAST WILL AND TESTAMENT OF Arjun Sharma\n"
    "I give my house at 123 Main Street to my brother, Amit Sharma.\n"
    "I give the residue of my estate to my spouse, Neha Kapoor."
)


def _snapshots() -> list[dict]:
    return [{
        "package_id": "pkg-will",
        "package_version": 2,
        "template_type": "will",
        "template_body": "",
        "body_text": WILL_BODY,
        "context": {"testator_name": "Arjun Sharma", "spouse_name": "Neha Kapoor"},
    }]


def _source() -> dict:
    return {"persons": [], "nominations": [], "answers": {}, "expected_set": ["will"]}


class _StubProvider:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def complete_json(self, *, system_prompt, user_content, temperature, timeout):
        content = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return content, {"prompt_tokens": 11, "completion_tokens": 7}


def _install(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    monkeypatch.setattr(
        model_review, "get_json_provider", lambda model=None: _StubProvider(payload)
    )


def _finding(**overrides) -> dict:
    base = {
        "check": "free_text_contradiction",
        "severity": "should_fix",
        "document": "will",
        "field": "specific_bequests",
        "excerpt": "I give my house at 123 Main Street to my brother, Amit Sharma.",
        "explanation": (
            "The will gives the house to Amit Sharma, but the residue "
            "answer leaves everything to Neha Kapoor with no carve-out."
        ),
        "suggested_fix": "Decide whether the house is a specific gift or part of the residue.",
    }
    base.update(overrides)
    return base


def test_valid_model_finding_passes_validation(monkeypatch) -> None:
    _install(monkeypatch, {"findings": [_finding()]})
    findings, usage = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.layer == "model"
    assert f.check == "free_text_contradiction"
    assert f.severity == "should_fix"
    assert f.document == "will"
    assert usage == {"prompt_tokens": 11, "completion_tokens": 7}


def test_unquotable_excerpt_is_discarded(monkeypatch) -> None:
    _install(monkeypatch, {"findings": [_finding(excerpt="This text is not in the will.")]})
    findings, _ = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert findings == []


def test_legal_conclusion_phrasing_is_discarded(monkeypatch) -> None:
    bad = _finding(explanation="This makes the will invalid and unenforceable.")
    _install(monkeypatch, {"findings": [bad]})
    findings, _ = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert findings == []


def test_unknown_check_is_discarded(monkeypatch) -> None:
    _install(monkeypatch, {"findings": [_finding(check="tax_optimization")]})
    findings, _ = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert findings == []


def test_missing_context_is_always_a_question_for_the_attorney(monkeypatch) -> None:
    item = _finding(
        check="missing_context",
        severity="should_fix",
        excerpt="I give the residue of my estate to my spouse, Neha Kapoor.",
        explanation=(
            "The documents mention no prior marriage — is there anything "
            "the attorney should know about a prior marriage?"
        ),
        suggested_fix="Ask the attorney whether this needs recording.",
    )
    _install(monkeypatch, {"findings": [item]})
    findings, _ = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert len(findings) == 1
    assert findings[0].severity == "question_for_attorney"


def test_malformed_model_output_yields_no_findings(monkeypatch) -> None:
    _install(monkeypatch, "not json at all")
    findings, _ = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert findings == []


def test_unknown_document_is_discarded(monkeypatch) -> None:
    _install(monkeypatch, {"findings": [_finding(document="codicil")]})
    findings, _ = model_review.review_via_model(
        snapshots=_snapshots(), source=_source(), model="gpt-5.5"
    )
    assert findings == []
