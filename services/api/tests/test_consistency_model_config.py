"""F2 (2026-10-26): the Consistency Reviewer's model is its own config.

Benchmark evidence (12 seeded cases, production prompt + server filter,
one pass each; full record in the goal's hidden_files
consistency-benchmark-2026-10-26/results.json):
gpt-4o-mini caught 6/6 contradictions but false-flagged 1/6 consistent
cases; gpt-5.5 caught 6/6 with 0/6 false positives. The reviewer
therefore resolves its model from OPENAI_CONSISTENCY_MODEL, defaulting
to gpt-5.5 — not from the shared chat model.
"""

from __future__ import annotations

import inspect


def test_consistency_model_setting_defaults_to_benchmark_winner() -> None:
    from app.core.config import Settings

    fields = Settings.model_fields
    assert "OPENAI_CONSISTENCY_MODEL" in fields
    assert fields["OPENAI_CONSISTENCY_MODEL"].default == "gpt-5.5"


def test_consistency_review_uses_the_dedicated_setting() -> None:
    from app.domains.interview import consistency

    src = inspect.getsource(consistency)
    assert "OPENAI_CONSISTENCY_MODEL" in src
    # The drift this fixes: consistency silently inherited the cheap
    # shared chat model (OPENAI_CHAT_MODEL).
    assert "OPENAI_CHAT_MODEL" not in src
