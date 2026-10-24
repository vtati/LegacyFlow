"""build_template_context must not crash when the household has no minor
persons: the children_list fallback reads the minor_children_details answer.
Regression: the fallback referenced an undefined `answers` name, so document
generation 500'd for every template on exactly those matters (E2E #3)."""
from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from app.domains.documents import service
from app.domains.household import repository as household_repo
from app.domains.intake import repository as intake_repo


class _Cursor:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        pass

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, matter_row):
        self._row = matter_row

    @contextmanager
    def cursor(self):
        yield _Cursor(self._row)


def test_children_fallback_uses_interview_answer(monkeypatch):
    matter_id = uuid4()
    household_id = uuid4()
    conn = _Conn({"id": str(matter_id), "household_id": str(household_id)})
    monkeypatch.setattr(service, "_render_context", lambda c, m: {})
    # Household has adults only — no minor-kind persons (the E2E #3 state).
    monkeypatch.setattr(
        household_repo,
        "list_persons_for_household",
        lambda c, hid: [{"kind": "adult", "full_name": "Raj Sharma", "date_of_birth": None}],
    )
    monkeypatch.setattr(
        intake_repo,
        "list_answers",
        lambda c, mid: [
            {"question_key": "minor_children_details",
             "answer": "Aarav Sharma (12), Diya Sharma (9), Kabir Sharma (6)"}
        ],
    )
    ctx = service.build_template_context(conn, matter_id)
    assert ctx["children_list"] == "Aarav Sharma (12), Diya Sharma (9), Kabir Sharma (6)"
    assert ctx["testator_name"] == "Raj Sharma"


def test_children_fallback_blank_when_no_answer(monkeypatch):
    matter_id = uuid4()
    conn = _Conn({"id": str(matter_id), "household_id": str(uuid4())})
    monkeypatch.setattr(service, "_render_context", lambda c, m: {})
    monkeypatch.setattr(household_repo, "list_persons_for_household", lambda c, hid: [])
    monkeypatch.setattr(intake_repo, "list_answers", lambda c, mid: [])
    ctx = service.build_template_context(conn, matter_id)
    assert ctx["children_list"] == "___"
