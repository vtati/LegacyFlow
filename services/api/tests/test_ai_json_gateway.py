"""F1 (2026-10-26): one JSON-completion gateway for the three call sites
that used to hand-roll httpx requests (interview extraction, consistency
review, vault analysis).

Pins:
* payload shape follows the F5 capability table per model;
* extraction resolves its model from the matter's interview path —
  interview_v2 matters extract on OPENAI_INTERVIEW_MODEL_V2, v1 matters
  on OPENAI_INTERVIEW_MODEL (the drift fix; extraction previously
  hardcoded the v1 model);
* each domain wrapper parses through the gateway with its prompts,
  schemas, and failure behavior unchanged.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ProviderError
from app.domains.ai import provider as ai_provider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, fail: bool = False) -> None:
        self._payload = payload
        self._fail = fail

    def raise_for_status(self) -> None:
        if self._fail:
            import httpx

            raise httpx.HTTPStatusError(
                "500", request=httpx.Request("POST", "https://api.openai.com"), response=None  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


def _chat_payload(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def _install_client(monkeypatch: pytest.MonkeyPatch, content: str, *, fail: bool = False) -> _FakeClient:
    client = _FakeClient(_FakeResponse(_chat_payload(content), fail=fail))
    monkeypatch.setattr(ai_provider, "get_http_client", lambda: client)
    return client


def test_json_payload_legacy_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install_client(monkeypatch, '{"a": 1}')
    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="gpt-4o")
    content, usage = provider.complete_json(
        system_prompt="sys", user_content="user", temperature=0.0, timeout=90.0
    )
    assert json.loads(content) == {"a": 1}
    assert usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    payload = client.calls[0]["json"]
    assert payload["model"] == "gpt-4o"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.0
    assert "max_completion_tokens" not in payload
    assert "reasoning_effort" not in payload  # no tools on this path


def test_json_payload_next_gen_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install_client(monkeypatch, '{"a": 1}')
    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="gpt-5.5")
    provider.complete_json(system_prompt="sys", user_content="user", temperature=0.0, timeout=90.0)
    payload = client.calls[0]["json"]
    assert "temperature" not in payload
    assert payload["max_completion_tokens"] == 6000


def test_json_payload_unknown_model_uses_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install_client(monkeypatch, '{"a": 1}')
    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="future-model-9000")
    provider.complete_json(system_prompt="sys", user_content="user", temperature=0.0, timeout=90.0)
    payload = client.calls[0]["json"]
    assert "temperature" not in payload
    assert payload["max_completion_tokens"] == 6000


def test_json_http_error_becomes_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, "{}", fail=True)
    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="gpt-4o")
    with pytest.raises(ProviderError):
        provider.complete_json(system_prompt="s", user_content="u", temperature=0.0, timeout=90.0)


def test_json_registry_requires_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from app.core.errors import ConfigurationError

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("AI_LLM_PROVIDER", "deterministic_dev")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigurationError):
            ai_provider.get_json_provider(model="gpt-4o")
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Domain wrappers route through the gateway
# ---------------------------------------------------------------------------


class _FakeJsonProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> tuple[str, dict[str, int]]:
        self.calls.append(kwargs)
        return self.content, {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_extraction_wrapper_parses_gateway_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.interview import extraction

    fake = _FakeJsonProvider(
        json.dumps(
            {
                "extractions": [
                    {
                        "question_key": "marital_status",
                        "value": "married",
                        "confidence": 0.9,
                        "source_page": 1,
                        "source_snippet": "married",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(extraction, "get_json_provider", lambda model=None: fake)
    items, usage = extraction._extract_via_openai(
        model="gpt-5.5",
        document_text="Jane is married.",
        field_schema=[{"key": "marital_status", "label": "Marital status"}],
    )
    assert items[0]["question_key"] == "marital_status"
    assert usage["total_tokens"] == 5
    # The UNTRUSTED fencing survives the move to the gateway.
    assert "<UNTRUSTED_DOCUMENT>" in fake.calls[0]["user_content"]


def test_extraction_model_resolution_follows_the_matters_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.interview import extraction

    pack = SimpleNamespace(manifest=SimpleNamespace(pack_id="us-va"))
    settings = SimpleNamespace(
        OPENAI_INTERVIEW_MODEL="gpt-4o", OPENAI_INTERVIEW_MODEL_V2="gpt-5.5"
    )
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)

    monkeypatch.setattr(
        extraction.feature_flags, "is_enabled", lambda *a, **k: True
    )
    assert extraction._extraction_model(None, firm_id=None, pack=pack) == "gpt-5.5"
    monkeypatch.setattr(
        extraction.feature_flags, "is_enabled", lambda *a, **k: False
    )
    assert extraction._extraction_model(None, firm_id=None, pack=pack) == "gpt-4o"

    def _boom(*a: Any, **k: Any) -> bool:
        raise RuntimeError("flag store down")

    monkeypatch.setattr(extraction.feature_flags, "is_enabled", _boom)
    # Fail-safe mirrors the interview path: a flag-read error falls back
    # to the proven v1 model, never breaks extraction.
    assert extraction._extraction_model(None, firm_id=None, pack=pack) == "gpt-4o"


def test_consistency_wrapper_parses_gateway_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.interview import consistency

    fake = _FakeJsonProvider(
        json.dumps(
            {
                "findings": [
                    {
                        "question": "Status is single but a spouse was named — clarify?",
                        "fields": ["marital_status"],
                        "severity": "review",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(consistency, "get_json_provider", lambda model=None: fake)
    findings, usage = consistency._review_via_openai(
        model="gpt-5.5",
        facts=[{"key": "marital_status", "value": "single", "provenance": "interview"}],
    )
    assert findings[0]["question"].endswith("?")
    assert usage["total_tokens"] == 5
    assert "VERIFIED FACTS:" in fake.calls[0]["user_content"]


def test_analysis_wrapper_parses_gateway_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.files import analysis

    fake = _FakeJsonProvider(
        json.dumps(
            {
                "document_type": "deed",
                "summary": "A deed.",
                "key_facts": [{"label": "Grantor", "value": "Robert Smith"}],
                "suggested_tags": ["deed"],
                "confidence": 0.9,
            }
        )
    )
    monkeypatch.setattr(analysis, "get_json_provider", lambda model=None: fake)
    result = analysis._analyze_openai("gpt-4o-mini", "GENERAL WARRANTY DEED", "deed.pdf")
    assert result["document_type"] == "deed"
    assert result["key_facts"] == [{"label": "Grantor", "value": "Robert Smith"}]


def test_json_null_message_degrades_to_empty_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review I-1: a structurally odd 200 (message: null) must degrade to
    "{}" — the pre-gateway call sites caught AttributeError and treated
    it as an empty result; the gateway must not 500 extraction/consistency."""
    client = _FakeClient(
        _FakeResponse({"choices": [{"message": None}], "usage": {"prompt_tokens": 1}})
    )
    monkeypatch.setattr(ai_provider, "get_http_client", lambda: client)
    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="gpt-4o")
    content, usage = provider.complete_json(
        system_prompt="s", user_content="u", temperature=0.0, timeout=90.0
    )
    assert content == "{}"
    assert usage["prompt_tokens"] == 1
