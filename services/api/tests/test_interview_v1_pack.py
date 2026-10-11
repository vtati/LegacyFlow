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


class TestTemplateContextMapping:
    def test_executor_answers_map_to_tokens(self) -> None:
        # New answer keys must flow into the will template placeholders.
        from app.domains.documents.service import build_template_context

        # This is a unit-level check on the mapping logic; the full DB-backed
        # test lives in test_documents.py. Here we verify the key mapping
        # exists by checking the token list the context builder handles.
        import inspect

        from app.domains.documents import service as doc_service

        source = inspect.getsource(doc_service.build_template_context)
        assert "executor_name" in source
        assert "residuary_beneficiaries" in source
        assert "specific_bequests" in source


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
