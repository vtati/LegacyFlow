"""Batch B (deep-review 2026-10-29) regression tests.

PERF-015: the interview extraction/dictation handlers were ``async def``
while running blocking DB + LLM work, stalling the event loop for the
full model call. They must be plain ``def`` handlers so FastAPI runs
them on the threadpool.
"""

from __future__ import annotations

import inspect


def test_dictate_and_extract_handlers_are_sync() -> None:
    from app.domains.interview import router

    assert not inspect.iscoroutinefunction(router.dictate), (
        "dictate must be a plain def handler (threadpool offload)"
    )
    assert not inspect.iscoroutinefunction(router.extract_document), (
        "extract_document must be a plain def handler (threadpool offload)"
    )


# ---------------------------------------------------------------------------
# ERR-009: undecryptable turns must escalate to an ERROR log with the
# skipped count (a systemic key-rotation failure must not silently
# degrade every session), and the turn response flags incomplete history.
# ---------------------------------------------------------------------------


def test_history_messages_logs_error_and_counts_skips(monkeypatch, caplog) -> None:
    import logging
    from uuid import uuid4

    from app.domains.interview import service

    session_id = uuid4()
    rows = [
        {"id": uuid4(), "ciphertext": "ok-1", "role": "user"},
        {"id": uuid4(), "ciphertext": "corrupt", "role": "assistant"},
        {"id": uuid4(), "ciphertext": "ok-2", "role": "assistant"},
    ]
    monkeypatch.setattr(
        service.repo, "list_recent_bodies", lambda conn, sid, limit: rows
    )

    def _decrypt(text: str) -> str:
        if text == "corrupt":
            raise ValueError("bad ciphertext")
        return f"plain:{text}"

    monkeypatch.setattr(service.crypto, "decrypt_text", _decrypt)

    with caplog.at_level(logging.WARNING, logger=service.logger.name):
        messages, skipped = service._history_messages_with_status(None, session_id)

    assert skipped == 1
    assert [m.content for m in messages] == ["plain:ok-1", "plain:ok-2"]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a skipped turn must be logged at ERROR, not just warning"
    assert str(session_id) in errors[0].getMessage()
    assert "1" in errors[0].getMessage()


def test_analyze_upload_phased_calls_llm_outside_transaction(monkeypatch) -> None:
    """PERF-016 (vault analysis): the phased analyze path must run the
    LLM call with no DB transaction open — proven by a tracking
    transaction factory standing in for app.core.db.transaction while
    prepare/persist run through it."""
    import contextlib
    from uuid import uuid4

    from app.core import db as core_db
    from app.domains.files import service

    class _Tracker:
        def __init__(self) -> None:
            self.open = 0
            self.opened_total = 0

        @contextlib.contextmanager
        def transaction(self):
            self.open += 1
            self.opened_total += 1
            try:
                yield object()
            finally:
                self.open -= 1

    tracker = _Tracker()
    monkeypatch.setattr(core_db, "transaction", tracker.transaction)

    prepared = service._PreparedAnalysis(
        actor_id=uuid4(),
        upload_id=uuid4(),
        firm_id=uuid4(),
        matter_id=None,
        extracted_text="GENERAL WARRANTY DEED ...",
        original_filename="deed.pdf",
        correlation_id=None,
    )
    monkeypatch.setattr(
        service, "_prepare_analysis", lambda conn, **kw: prepared
    )
    seen: dict[str, int] = {}

    def _fake_llm(text: str, filename: str) -> dict:
        seen["open_at_call"] = tracker.open
        return {
            "document_type": "deed",
            "summary": "s",
            "key_facts": [],
            "suggested_tags": [],
            "confidence": 0.9,
            "source": "rules",
        }

    monkeypatch.setattr(service.file_analysis, "analyze_with_llm", _fake_llm)
    sentinel = object()
    monkeypatch.setattr(
        service, "_persist_analysis", lambda conn, prep, result: sentinel
    )

    out = service.analyze_upload_phased(
        actor_sub="sub", upload_id=prepared.upload_id
    )
    assert out is sentinel
    assert seen["open_at_call"] == 0, "LLM called with a transaction open"
    assert tracker.opened_total == 2, "expected prepare + persist transactions only"


def test_post_turn_phased_calls_provider_outside_transaction(monkeypatch) -> None:
    """PERF-016 (interview turn, the hot path): post_turn_phased must
    run prepare in one short transaction, call the provider with NO
    transaction open, and persist in a second short transaction.
    Proven with a tracking transaction factory over stubbed phases;
    the full interview suite exercises the same path end-to-end."""
    import contextlib
    from types import SimpleNamespace
    from uuid import uuid4

    from app.core import db as core_db
    from app.domains.interview import service

    class _Tracker:
        def __init__(self) -> None:
            self.open = 0
            self.opened_total = 0

        @contextlib.contextmanager
        def transaction(self):
            self.open += 1
            self.opened_total += 1
            try:
                yield object()
            finally:
                self.open -= 1

    tracker = _Tracker()
    monkeypatch.setattr(core_db, "transaction", tracker.transaction)

    seen: dict[str, int] = {}

    class _Provider:
        name = "stub"

        def chat(self, *, system_prompt, messages, context):
            seen["open_at_call"] = tracker.open
            return SimpleNamespace(reply="ok", proposals=[], citations=[], usage={})

    prepared = SimpleNamespace(
        provider=_Provider(),
        system_prompt="sys",
        context=object(),
        history=[],
        message="hello",
    )
    monkeypatch.setattr(service, "_prepare_turn", lambda conn, **kw: prepared)
    sentinel = object()
    monkeypatch.setattr(
        service, "_complete_turn", lambda conn, prep, response, latency_ms: sentinel
    )

    out = service.post_turn_phased(
        actor_sub="sub",
        matter_id=uuid4(),
        session_id=uuid4(),
        payload=SimpleNamespace(message="hello", idempotency_key=None),
    )
    assert out is sentinel
    assert seen["open_at_call"] == 0, "provider called with a transaction open"
    assert tracker.opened_total == 2, "expected prepare + persist transactions only"


# ---------------------------------------------------------------------------
# SEC-013 + PERF-019: auto-confirm must never write legally material
# keys on the model's say-so, and the confirmable remainder must be
# written in ONE save_answers call in ONE transaction.
# ---------------------------------------------------------------------------


def _auto_confirm_harness(monkeypatch):
    """Stub the router's collaborators; return (calls, turn_out_factory)."""
    import contextlib
    from uuid import uuid4

    from app.domains.interview import router
    from app.domains.legal_content.pack_loader import get_pack

    calls: dict[str, list] = {"decide": [], "save": [], "txns": []}

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *a, **k):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

    @contextlib.contextmanager
    def _txn():
        calls["txns"].append(1)
        yield _Conn()

    monkeypatch.setattr(router.db, "transaction", _txn)

    from app.domains.matters import service as matters_service

    pack = get_pack("US-VA", "2.0.0")
    monkeypatch.setattr(matters_service, "caller_id", lambda sub: uuid4())
    monkeypatch.setattr(
        matters_service, "load_matter_pack", lambda conn, mid: (pack, "US-VA", "2.0.0")
    )
    monkeypatch.setattr(
        router.interview_repo,
        "decide_proposal",
        lambda conn, pid, **kw: calls["decide"].append(pid) or {"id": str(pid)},
    )
    monkeypatch.setattr(
        router.intake_service,
        "save_answers",
        lambda conn, **kw: calls["save"].append(kw["answers"]),
    )
    monkeypatch.setattr(
        router.service,
        "_display_progress_for_matter",
        lambda conn, pack_, mid: (50.0, [], 10, 5, []),
    )
    return router, calls


def _turn_out_with(proposals):
    from uuid import uuid4

    from app.domains.interview.schemas import ProposalDraftOut, TurnOut

    return TurnOut(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        reply="ok",
        proposals=[
            ProposalDraftOut(
                id=uuid4(),
                question_key=key,
                proposed_value=value,
                source=source,
                confidence=conf,
                source_snippet=None,
                status="proposed",
            )
            for key, value, source, conf in proposals
        ],
        trace_hash="x",
        completeness_pct=0.0,
        missing_required_keys=[],
    )


def test_auto_confirm_never_confirms_material_keys(monkeypatch) -> None:
    from uuid import uuid4

    router, calls = _auto_confirm_harness(monkeypatch)
    turn_out = _turn_out_with(
        [
            # Model-reported 0.95 on legally material keys: spouse
            # (relationship sync) and a person-typed nomination key.
            ("spouse_full_name", "Jamie Example", "user_stated", 0.95),
            ("primary_executor_person", "Pat Example", "user_stated", 0.99),
            ("minor_children_details", "Kid Example (7)", "user_stated", 0.95),
        ]
    )
    confirmed = router._auto_confirm_proposals(turn_out, "sub", uuid4())
    assert confirmed == 0
    assert calls["decide"] == [], "material proposals must not even be decided"
    assert calls["save"] == [], "material values must not reach intake answers"
    assert all(p.status == "proposed" for p in turn_out.proposals)


def test_auto_confirm_batches_non_material_into_one_save(monkeypatch) -> None:
    from uuid import uuid4

    router, calls = _auto_confirm_harness(monkeypatch)
    turn_out = _turn_out_with(
        [
            ("primary_residence_state", "Virginia", "user_stated", 0.95),
            ("funeral_wishes", "Cremation", "user_stated", 0.92),
            ("organ_donation_wishes", "Yes", "user_stated", 0.5),  # too low
        ]
    )
    confirmed = router._auto_confirm_proposals(turn_out, "sub", uuid4())
    assert confirmed == 2
    assert len(calls["txns"]) == 1, "batch must run in a single transaction"
    assert len(calls["save"]) == 1, "batch must be a single save_answers call"
    assert calls["save"][0] == {
        "primary_residence_state": "Virginia",
        "funeral_wishes": "Cremation",
    }
    by_key = {p.question_key: p.status for p in turn_out.proposals}
    assert by_key["primary_residence_state"] == "confirmed"
    assert by_key["funeral_wishes"] == "confirmed"
    assert by_key["organ_donation_wishes"] == "proposed"
    # Progress refreshed from the same transaction (PERF-019).
    assert turn_out.completeness_pct == 50.0


def test_auto_confirm_bundle_verdict_still_confirms_material_key(monkeypatch) -> None:
    """Bundle proposals carry the user's own explicit YES verdict
    (server-generated, source='bundle') — the SEC-013 block targets
    model-asserted facts, not the user's verdict."""
    from uuid import uuid4

    router, calls = _auto_confirm_harness(monkeypatch)
    turn_out = _turn_out_with(
        [("primary_executor_person", "Pat Example", "bundle", 1.0)]
    )
    confirmed = router._auto_confirm_proposals(turn_out, "sub", uuid4())
    assert confirmed == 1
    assert calls["save"] == [{"primary_executor_person": "Pat Example"}]


def test_turn_out_history_incomplete_defaults_false() -> None:
    from uuid import uuid4

    from app.domains.interview.schemas import TurnOut

    out = TurnOut(
        id=uuid4(),
        session_id=uuid4(),
        role="assistant",
        reply="hi",
        trace_hash="x",
        completeness_pct=0.0,
        missing_required_keys=[],
    )
    assert out.history_incomplete is False
