"""Tests for v1 pack: executor, bequest, and address questions.

The interviewer must collect everything the will template needs:
- Executor and backup executor nomination
- Residuary beneficiaries
- Specific bequests
- Addresses for testator, guardian, executor
"""
from __future__ import annotations


class TestV1PackQuestionSets:
    def test_v1_pack_exists(self) -> None:
        from app.domains.legal_content.pack_loader import get_pack

        pack = get_pack("US-VA", version="0.2.0")
        assert pack is not None

    def test_executor_questions_present(self) -> None:
        from app.domains.legal_content.pack_loader import get_pack
        from app.domains.intake.question_sets import all_question_keys

        pack = get_pack("US-VA", version="0.2.0")
        keys = all_question_keys(pack.question_sets)
        assert "primary_executor_person" in keys
        assert "backup_executor_person" in keys

    def test_bequest_questions_present(self) -> None:
        from app.domains.legal_content.pack_loader import get_pack
        from app.domains.intake.question_sets import all_question_keys

        pack = get_pack("US-VA", version="0.2.0")
        keys = all_question_keys(pack.question_sets)
        assert "residuary_beneficiaries" in keys
        assert "specific_bequests" in keys

    def test_address_questions_present(self) -> None:
        from app.domains.legal_content.pack_loader import get_pack
        from app.domains.intake.question_sets import all_question_keys

        pack = get_pack("US-VA", version="0.2.0")
        keys = all_question_keys(pack.question_sets)
        assert "testator_street_address" in keys
        assert "testator_city" in keys
        assert "primary_guardian_address" in keys
        assert "primary_executor_address" in keys


def _build_context(monkeypatch, answers: dict):
    """Exercise the real context builder with representative facts.

    Replaces the original source-text scrape (which asserted each
    token string appeared in ``build_template_context``'s source):
    the tokens are now assembled via the ``_ANSWER_SLOT_TABLE``
    mapping inside that builder, so the strings no longer literally
    appear in its source even though every token IS produced at
    runtime. The honest assertion is on the produced context itself
    — base render and household reads are stubbed, the slot-table
    path under test runs for real. (The full DB-backed render test
    lives in test_documents_person_render.py.)
    """
    from contextlib import contextmanager
    from uuid import uuid4

    from app.domains.documents import service as doc_service
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
        def __init__(self, row):
            self._row = row

        @contextmanager
        def cursor(self):
            yield _Cursor(self._row)

    matter_id = uuid4()
    household_id = uuid4()
    persons = [
        {"id": str(uuid4()), "kind": "adult", "full_name": name}
        for name in ["Raj Sharma", "Priya Nair", "Neha Kapoor"]
    ]
    conn = _Conn({
        "id": str(matter_id), "household_id": str(household_id),
        "title": "Synthetic Matter",
    })
    monkeypatch.setattr(
        doc_service, "_render_context", lambda c, m, answers=None: {})
    monkeypatch.setattr(
        household_repo, "list_persons_for_household",
        lambda c, hid: persons)
    monkeypatch.setattr(
        household_repo, "list_members_for_household",
        lambda c, hid: [{
            "relationship_role": "primary_client",
            "person_id": persons[0]["id"],
        }])
    monkeypatch.setattr(
        household_repo, "list_relationships_for_household",
        lambda c, hid: [])
    monkeypatch.setattr(
        intake_repo, "list_answers",
        lambda c, mid: [
            {"question_key": k, "answer": v} for k, v in answers.items()
        ])
    return doc_service.build_template_context(conn, matter_id)


class TestTemplateContextMapping:
    def test_executor_answers_map_to_tokens(self, monkeypatch) -> None:
        # New answer keys must flow into the will template placeholders:
        # build the context from the facts and assert the produced
        # token keys/values, not the builder's source text.
        ctx = _build_context(monkeypatch, {
            "testator_full_name": "Raj Sharma",
            "primary_executor_person": "Priya Nair",
            "residuary_beneficiaries": "Neha Kapoor",
            "specific_bequests": "Watch to Priya Nair",
        })
        assert ctx.get("executor_name") == "Priya Nair"
        assert ctx.get("residuary_beneficiaries") == "Neha Kapoor"
        assert ctx.get("specific_bequests") == "Watch to Priya Nair"


class TestAdaptiveFollowUp:
    def test_executor_backup_prompted_after_primary(self) -> None:
        # When the user names an executor, the interviewer should ask for
        # a backup executor next (adaptive interrogation per section).
        from app.domains.interview.service import _next_adaptive_question

        answered = {"primary_executor_person": "Priya Sharma"}
        pending: set[str] = set()
        result = _next_adaptive_question(
            answered_keys=set(answered.keys()),
            pending_keys=pending,
            section="executors",
        )
        assert result == "backup_executor_person"

    def test_bequest_details_prompted_after_bequest_yes(self) -> None:
        from app.domains.interview.service import _next_adaptive_question

        answered = {"has_specific_bequests": True}
        pending: set[str] = set()
        result = _next_adaptive_question(
            answered_keys=set(answered.keys()),
            pending_keys=pending,
            section="bequests",
        )
        assert result == "specific_bequests"
