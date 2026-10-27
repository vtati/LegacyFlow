"""Model split (Interview v2, Part 4): the interview conductor and the
document extractor run on the stronger interview model; cheap
classification (consistency) stays on the chat model."""
from __future__ import annotations


from app.core.config import Settings, get_settings


def _settings(**overrides) -> Settings:
    # Settings pulls required fields from the environment (.env in dev);
    # instantiate with explicit dummy values for the required ones if the
    # test environment lacks them.
    kwargs = {}
    for field in Settings.model_fields:
        if Settings.model_fields[field].is_required():
            kwargs[field] = overrides.pop(field, "test-value")
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_interview_model_default_is_gpt_4o_mini_stays_for_chat():
    s = _settings()
    assert s.OPENAI_INTERVIEW_MODEL == "gpt-4o"
    assert s.OPENAI_CHAT_MODEL == "gpt-4o-mini"


def test_chat_provider_factory_uses_interview_model(monkeypatch):
    from app.domains.ai import provider as ai_provider

    # The settings singleton rebuilds from the environment after a cache
    # clear: supply every required field plus the API key.
    for field, info in Settings.model_fields.items():
        if info.is_required():
            monkeypatch.setenv(field, "test-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    try:
        provider = ai_provider._openai_chat_factory()
        assert provider._model == "gpt-4o"
    finally:
        get_settings.cache_clear()


def test_extraction_and_consistency_model_choices():
    # Source-level model choices after the 2026-10-26 fix pass (F1/F2):
    # extraction follows the MATTER's interview path (v2 matters use
    # OPENAI_INTERVIEW_MODEL_V2 via _extraction_model — previously it
    # hardcoded the v1 model, the drift §10 flagged), and consistency
    # uses its own OPENAI_CONSISTENCY_MODEL (benchmarked to gpt-5.5)
    # instead of silently inheriting the cheap shared chat model.
    import inspect

    from app.domains.interview import consistency, extraction

    ext_src = inspect.getsource(extraction)
    assert "_extraction_model" in ext_src
    assert "OPENAI_INTERVIEW_MODEL_V2" in ext_src
    assert "OPENAI_INTERVIEW_MODEL" in ext_src
    con_src = inspect.getsource(consistency)
    assert "OPENAI_CONSISTENCY_MODEL" in con_src
    assert "OPENAI_CHAT_MODEL" not in con_src
    assert "OPENAI_INTERVIEW_MODEL" not in con_src
