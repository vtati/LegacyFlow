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
