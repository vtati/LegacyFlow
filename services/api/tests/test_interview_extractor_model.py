"""Stronger-extractor wiring (Interview v2): the v2 path runs on
OPENAI_INTERVIEW_MODEL_V2 (default gpt-5.5, benchmark winner 2026-10-25);
the flag-off v1 path keeps OPENAI_INTERVIEW_MODEL (gpt-4o) untouched.

gpt-5.x API differences handled in the provider: chat completions with
function tools rejects reasoning_effort != 'none' (verified live 400),
temperature is not accepted, and the token cap is
max_completion_tokens, not max_tokens.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from app.core.config import Settings, get_settings
from app.domains.ai import provider as ai_provider
from app.domains.ai.provider import (
    ChatMessage,
    InterviewContext,
    OpenAIChatProvider,
)


def _settings(**overrides) -> Settings:
    kwargs = {}
    for field in Settings.model_fields:
        if Settings.model_fields[field].is_required():
            kwargs[field] = overrides.pop(field, "test-value")
    kwargs.update(overrides)
    return Settings(**kwargs)


def _env_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for field, info in Settings.model_fields.items():
        if info.is_required():
            monkeypatch.setenv(field, "test-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_LLM_PROVIDER", "openai")
    get_settings.cache_clear()


def _context() -> InterviewContext:
    return InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="2.0.0",
        missing_required_keys=[],
        total_required=41,
        answered_required=41,
        documents=[],
    )


def _tool_response() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "Thanks — recorded.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "propose_field_values",
                                "arguments": json.dumps(
                                    {
                                        "proposals": [
                                            {
                                                "question_key": "marital_status",
                                                "value": "married",
                                                "confidence": 0.95,
                                                "source_snippet": "I'm married",
                                            }
                                        ]
                                    }
                                ),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                  "total_tokens": 120},
    }


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.sent: dict = {}

    def post(self, url, headers=None, json=None):  # noqa: A002 - httpx shape
        self.sent = {"url": url, "headers": headers, "json": json}
        return _FakeResponse(self._payload)


def _chat_with_payload(model: str) -> dict:
    provider = OpenAIChatProvider(api_key="sk-test", model=model)
    client = _FakeClient(_tool_response())
    with mock.patch.object(ai_provider, "get_http_client", return_value=client):
        resp = provider.chat(
            system_prompt="sp",
            messages=[ChatMessage(role="user", content="I'm married")],
            context=_context(),
        )
    assert len(resp.proposals) == 1
    assert resp.proposals[0].question_key == "marital_status"
    return client.sent["json"]


# --- config ---------------------------------------------------------------

def test_v2_extractor_model_default_and_legacy_defaults_unchanged():
    s = _settings()
    assert s.OPENAI_INTERVIEW_MODEL_V2 == "gpt-5.5"
    assert s.OPENAI_INTERVIEW_MODEL == "gpt-4o"
    assert s.OPENAI_CHAT_MODEL == "gpt-4o-mini"


# --- factory / registry ----------------------------------------------------

def test_factory_default_and_override(monkeypatch: pytest.MonkeyPatch):
    _env_settings(monkeypatch)
    try:
        assert ai_provider._openai_chat_factory()._model == "gpt-4o"
        assert ai_provider._openai_chat_factory(model="gpt-5.5")._model == "gpt-5.5"
        assert ai_provider.get_llm_provider()._model == "gpt-4o"
        assert ai_provider.get_llm_provider(model="gpt-5.5")._model == "gpt-5.5"
    finally:
        get_settings.cache_clear()


# --- payload shape ----------------------------------------------------------

def test_payload_legacy_model_keeps_legacy_shape():
    payload = _chat_with_payload("gpt-4o")
    assert payload["model"] == "gpt-4o"
    assert set(payload) == {"model", "messages", "tools", "tool_choice",
                            "temperature"}
    assert payload["temperature"] == 0.2
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "propose_field_values"


def test_payload_next_gen_model_uses_gpt5_shape():
    payload = _chat_with_payload("gpt-5.5")
    assert payload["model"] == "gpt-5.5"
    assert set(payload) == {"model", "messages", "tools", "tool_choice",
                            "reasoning_effort", "max_completion_tokens"}
    assert payload["reasoning_effort"] == "none"
    assert payload["max_completion_tokens"] == 6000
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "propose_field_values"


@pytest.mark.parametrize(
    "model,send_temperature,token_param,reasoning_none",
    [
        # Explicit table entries: the legacy contract stays byte-identical
        # (temperature sent, no completion cap, no reasoning_effort).
        ("gpt-4o", True, None, False),
        ("gpt-4o-mini", True, None, False),
        ("gpt-4.1", True, None, False),
        ("gpt-4.1-mini", True, None, False),
        ("gpt-4-turbo", True, None, False),
        # Dated snapshots inherit their base model's table entry —
        # pinning OPENAI_*_MODEL to a snapshot must not change shape
        # (review I-2).
        ("gpt-4o-2024-11-20", True, None, False),
        ("gpt-4o-mini-2024-07-18", True, None, False),
        ("gpt-5.5-2026-01-15", False, "max_completion_tokens", True),
        # The verified gpt-5 contract (2026-10-25): no temperature,
        # max_completion_tokens, reasoning pinned to 'none' with tools.
        ("gpt-5.5", False, "max_completion_tokens", True),
        ("GPT-5.5", False, "max_completion_tokens", True),
        (" gpt-5.5 ", False, "max_completion_tokens", True),
        # Genuinely unknown models resolve to the SAFE default (the
        # modern contract): omitting temperature can never 400.
        # Name prefixes no longer decide anything (F5).
        ("gpt-5.4-mini", False, "max_completion_tokens", True),
        ("gpt-6-sol", False, "max_completion_tokens", True),
        ("some-future-model", False, "max_completion_tokens", True),
    ],
)
def test_model_capability_table(
    model: str, send_temperature: bool, token_param: str | None, reasoning_none: bool
):
    from app.core.config import model_capabilities

    caps = model_capabilities(model)
    assert caps.send_temperature is send_temperature
    assert caps.completion_token_param == token_param
    assert caps.reasoning_effort_none_with_tools is reasoning_none


# --- service seam ------------------------------------------------------------

def test_service_selects_v2_model_only_on_v2_turns():
    import inspect

    from app.domains.interview import service

    src = inspect.getsource(service)
    assert "OPENAI_INTERVIEW_MODEL_V2" in src
    # The model override is conditional on the turn's v2 flag state.
    assert "if v2_on else None" in src
    # Provenance (review I-2): the AI trace and every proposal row record
    # the effective model — exactly two record sites use it, and neither
    # may hardcode the legacy interview model anymore.
    assert src.count("model_id=effective_model") == 2
    assert "model_id=settings.OPENAI_INTERVIEW_MODEL " not in src
