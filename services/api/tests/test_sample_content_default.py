"""F6 (2026-10-26): sample (attorney-pending) content is excluded by default.

AI_ALLOW_SAMPLE_CONTENT used to default to true via a raw os.environ
read in ai/service.py, so attorney-pending sample knowledge could
ground interview answers and Ask responses unless a deployment
explicitly opted out. The setting now lives in core config and
defaults to False; enabling sample content is an explicit choice.

Grounding check behind the flip (2026-10-26): every us-va pack
version (0.1.0/0.2.0/2.0.0 — the versions the Demo and E2E firms'
matters use) ships exactly two knowledge docs: will-validity-basics
(status: approved) and guardian-basics (status: sample). The flip
therefore leaves both firms with the approved doc as grounding —
guardian questions escalate instead of grounding on sample content.
"""

from __future__ import annotations

import pytest


def test_setting_defaults_to_false() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["AI_ALLOW_SAMPLE_CONTENT"].default is False


def test_helper_reads_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from app.domains.ai import service

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delenv("AI_ALLOW_SAMPLE_CONTENT", raising=False)
    get_settings.cache_clear()
    try:
        assert service._allow_sample_content() is False
        monkeypatch.setenv("AI_ALLOW_SAMPLE_CONTENT", "true")
        get_settings.cache_clear()
        assert service._allow_sample_content() is True
    finally:
        get_settings.cache_clear()
