"""Documents completeness pre-check (Interview v2, Part 1 Task 1.4).

Draft generation and production release must refuse while APPLICABLE
required intake answers are missing — using the same partition as
progress (pruned questions never block; pending ones do). The error is a
409 whose message lists the missing question labels.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.domains.documents import service
from app.domains.intake import repository as intake_repo
from app.domains.legal_content import pack_loader


def _matter(version: str) -> dict:
    return {
        "id": str(uuid4()),
        "jurisdiction_snapshot": {
            "pack_id": "us-va",
            "jurisdiction_code": "US-VA",
            "semantic_version": version,
        },
    }


def _answers(monkeypatch, rows: dict) -> None:
    monkeypatch.setattr(
        intake_repo,
        "list_answers",
        lambda conn, mid: [
            {"question_key": k, "answer": v} for k, v in rows.items()
        ],
    )


def test_incomplete_v2_matter_raises_with_labels(monkeypatch):
    _answers(monkeypatch, {"has_minor_children": False})
    with pytest.raises(service.IntakeIncompleteError) as excinfo:
        service._require_intake_complete(object(), _matter("2.0.0"))
    err = excinfo.value
    assert err.status == 409
    assert err.code == "intake_incomplete"
    # Labels (not raw keys) for the still-missing applicable questions.
    assert "What best describes your household?" in err.detail
    # Pruned guardian questions must NOT be listed (no minor children).
    assert "care for your minor children" not in err.detail


def test_pruned_questions_do_not_block(monkeypatch):
    # Answer everything applicable for the no-minors/no-trust profile.
    pack = pack_loader.get_pack("US-VA", "2.0.0")
    answers = {
        "has_minor_children": False,
        "wants_revocable_trust": False,
        "has_specific_bequests": False,
        "has_retirement_accounts": False,
        "has_life_insurance": False,
        "wants_poa_document": True,
        "wants_healthcare_directive": True,
    }
    from app.domains.intake import question_sets as qs

    _per, _pct, missing, _pruned = qs.compute_progress(pack.question_sets, answers)
    assert missing, "profile should still have applicable questions to fill"
    for key in missing:
        answers[key] = "x" if not key.startswith("has_") else True
    _answers(monkeypatch, answers)
    # Must not raise: everything applicable is answered, pruned is ignored.
    service._require_intake_complete(object(), _matter("2.0.0"))


def test_v1_complete_matter_passes_and_incomplete_raises(monkeypatch):
    pack = pack_loader.get_pack("US-VA", "0.2.0")
    from app.domains.intake import question_sets as qs

    _per, _pct, missing, _pr = qs.compute_progress(pack.question_sets, {})
    full = {key: "x" for key in missing}
    _answers(monkeypatch, full)
    service._require_intake_complete(object(), _matter("0.2.0"))
    _answers(monkeypatch, {})
    with pytest.raises(service.IntakeIncompleteError):
        service._require_intake_complete(object(), _matter("0.2.0"))
