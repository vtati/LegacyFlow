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
