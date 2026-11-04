"""LangSmith tracing layer (eval pipeline Phase 1, 2026-11-04).

Pins the design rules for app.core.tracing:
- Off unless LANGSMITH_TRACING is set AND an API key is present; when off
  the traced block is a pure no-op and no client is ever built. (These
  tests detect construction through a recording builder, never through
  a thrown assertion — finish() swallows exceptions by design, so a
  throwing builder could not fail them.)
- Fail-open: a LangSmith failure can never break, mask, or alter a model
  call. Errors inside the traced block are recorded (class name only)
  AND re-raised. Client construction failures are cached, not retried
  on every call.
- The client is built with background batching requested explicitly, so
  run posting stays off the request path.
- When on, one run is posted per call carrying the agent and model in
  its tags and metadata — never client PII (the provider layer has
  none), and never audio bytes for dictation.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core import tracing


class _FakeLangSmithClient:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []

    def create_run(self, **kwargs):
        self.created.append(kwargs)

    def update_run(self, run_id, **kwargs):
        self.updated.append({"run_id": run_id, **kwargs})


class _ExplodingClient:
    def create_run(self, **kwargs):
        raise RuntimeError("langsmith is down")

    def update_run(self, run_id, **kwargs):
        raise RuntimeError("langsmith is down")


class _UpdateExplodingClient(_FakeLangSmithClient):
    def update_run(self, run_id, **kwargs):
        raise RuntimeError("update failed")


def _settings(*, enabled: bool, key: str = "test-key"):
    return SimpleNamespace(
        LANGSMITH_TRACING=enabled,
        LANGSMITH_API_KEY=key,
        LANGSMITH_PROJECT="legacyflow-tracing",
        LANGSMITH_ENDPOINT="https://api.smith.langchain.com",
        ENVIRONMENT="test",
    )


@pytest.fixture(autouse=True)
def _reset_client_cache():
    tracing._client = None
    tracing._client_failed_at = None
    yield
    tracing._client = None
    tracing._client_failed_at = None


def _recording_builder(calls: list, result=None):
    def _build(settings):
        calls.append(settings)
        return result if result is not None else _FakeLangSmithClient()

    return _build


def test_disabled_is_pure_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list = []
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=False))
    monkeypatch.setattr(tracing, "_build_client", _recording_builder(builds))
    with tracing.traced_llm_call(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": []},
    ) as rec:
        rec.set_outputs(content="{}")
        rec.set_usage(prompt_tokens=3)
    assert builds == []


def test_enabled_without_key_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list = []
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True, key=""))
    monkeypatch.setattr(tracing, "_build_client", _recording_builder(builds))
    with tracing.traced_llm_call(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": []},
    ):
        pass
    assert builds == []


def test_unreadable_settings_disable_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list = []

    def _boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(tracing, "_settings", _boom)
    monkeypatch.setattr(tracing, "_build_client", _recording_builder(builds))
    with tracing.traced_llm_call(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": []},
    ) as rec:
        rec.set_outputs(content="{}")
    # No exception escaped AND no client was built: tracing was off,
    # not merely rescued.
    assert builds == []


def test_enabled_posts_run_with_tags_outputs_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLangSmithClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)

    with tracing.traced_llm_call(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": [{"role": "user", "content": "hi"}]},
    ) as rec:
        rec.set_outputs(content='{"findings": []}')
        rec.set_usage(prompt_tokens=11, completion_tokens=7, total_tokens=18)

    assert len(fake.created) == 1
    run = fake.created[0]
    assert run["name"] == "json-completion"
    assert run["run_type"] == "llm"
    assert run["project_name"] == "legacyflow-tracing"
    assert "agent:consistency-review" in run["tags"]
    assert "model:gpt-5.5" in run["tags"]
    assert run["extra"]["metadata"]["agent"] == "consistency-review"
    assert run["extra"]["metadata"]["environment"] == "test"

    assert len(fake.updated) == 1
    update = fake.updated[0]
    assert update["outputs"]["content"] == '{"findings": []}'
    assert update["outputs"]["usage"]["total_tokens"] == 18
    assert update["error"] is None
    assert update["end_time"] is not None


def test_error_inside_block_is_recorded_and_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLangSmithClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)

    with pytest.raises(ValueError, match="boom"):
        with tracing.traced_llm_call(
            name="json-completion", agent="vault-analysis", model="gpt-4o-mini",
            inputs={"messages": []},
        ):
            raise ValueError("boom — sensitive detail that must not ship")

    assert len(fake.updated) == 1
    # Class name only: the message text never reaches LangSmith.
    assert fake.updated[0]["error"] == "ValueError"


def test_langsmith_failure_never_breaks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: _ExplodingClient())

    with tracing.traced_llm_call(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": []},
    ) as rec:
        rec.set_outputs(content="{}")
    # no exception escaped — the model call proceeds exactly as before


def test_update_run_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _UpdateExplodingClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)

    with tracing.traced_llm_call(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": []},
    ) as rec:
        rec.set_outputs(content="{}")
    assert len(fake.created) == 1  # create landed; the update failure vanished


def test_client_construction_failure_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list = []

    def _always_fails(settings):
        builds.append(settings)
        raise RuntimeError("sdk broken")

    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", _always_fails)

    for _ in range(3):
        with tracing.traced_llm_call(
            name="json-completion", agent="consistency-review", model="gpt-5.5",
            inputs={"messages": []},
        ):
            pass
    # One construction attempt across three calls, not one per call.
    assert len(builds) == 1


def test_finish_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLangSmithClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)

    trace = tracing.LLMTrace(
        name="json-completion", agent="consistency-review", model="gpt-5.5",
        inputs={"messages": []},
    )
    trace.finish()
    trace.finish()
    assert len(fake.created) == 1
    assert len(fake.updated) == 1


def test_build_client_requests_background_batching(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _CaptureClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import langsmith

    monkeypatch.setattr(langsmith, "Client", _CaptureClient)
    tracing._build_client(_settings(enabled=True))
    assert captured["auto_batch_tracing"] is True
    assert captured["api_url"] == "https://api.smith.langchain.com"


def test_json_provider_posts_a_trace_with_its_agent_label(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.ai import provider as ai_provider
    from tests.test_ai_json_gateway import _install_client

    fake = _FakeLangSmithClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)
    _install_client(monkeypatch, '{"verdict": "ok"}')

    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="gpt-5.5")
    content, usage = provider.complete_json(
        system_prompt="sys", user_content="user", temperature=0.0,
        agent="consistency-review",
    )

    assert content == '{"verdict": "ok"}'
    assert len(fake.created) == 1
    assert "agent:consistency-review" in fake.created[0]["tags"]
    assert fake.updated[0]["outputs"]["usage"] == usage


def test_json_provider_unaffected_when_tracing_explodes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.ai import provider as ai_provider
    from tests.test_ai_json_gateway import _install_client

    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: _ExplodingClient())
    _install_client(monkeypatch, '{"verdict": "ok"}')

    provider = ai_provider.OpenAIJsonProvider(api_key="sk-test", model="gpt-5.5")
    content, _usage = provider.complete_json(
        system_prompt="sys", user_content="user", temperature=0.0,
        agent="consistency-review",
    )
    assert content == '{"verdict": "ok"}'


def test_chat_provider_posts_an_interviewer_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.ai import provider as ai_provider
    from tests.test_ai_json_gateway import _FakeClient, _FakeResponse

    fake = _FakeLangSmithClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)

    payload = {
        "choices": [
            {
                "message": {
                    "content": "Thanks — noted.",
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
                                                "value": "single",
                                                "confidence": 0.92,
                                                "source_snippet": "I am single",
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
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    client = _FakeClient(_FakeResponse(payload))
    monkeypatch.setattr(ai_provider, "get_http_client", lambda: client)

    provider = ai_provider.OpenAIChatProvider(api_key="sk-test", model="gpt-5.5")
    context = ai_provider.InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="0.1.0",
        missing_required_keys=[],
        total_required=9,
        answered_required=9,
        documents=[],
    )
    response = provider.chat(
        system_prompt="sys",
        messages=[ai_provider.ChatMessage(role="user", content="I am single")],
        context=context,
    )

    assert [p.question_key for p in response.proposals] == ["marital_status"]
    assert len(fake.created) == 1
    assert fake.created[0]["name"] == "interviewer-turn"
    assert "agent:interviewer" in fake.created[0]["tags"]
    outputs = fake.updated[0]["outputs"]
    assert outputs["proposals"][0]["question_key"] == "marital_status"
    assert outputs["usage"]["total_tokens"] == 18


def test_whisper_trace_never_contains_audio_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.ai import provider as ai_provider
    from tests.test_ai_json_gateway import _FakeClient, _FakeResponse

    fake = _FakeLangSmithClient()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tracing, "_build_client", lambda settings: fake)

    client = _FakeClient(_FakeResponse({"text": "My name is Priya Sharma."}))
    monkeypatch.setattr(ai_provider, "get_http_client", lambda: client)

    provider = ai_provider.WhisperSTTProvider(api_key="sk-test", model="whisper-1")
    text = provider.transcribe(b"FAKE-AUDIO-BYTES", filename="clip.webm")

    assert text == "My name is Priya Sharma."
    assert len(fake.created) == 1
    inputs = fake.created[0]["inputs"]
    assert inputs["audio_bytes"] == len(b"FAKE-AUDIO-BYTES")
    assert inputs["filename"] == "clip.webm"
    # No bytes object anywhere in the traced inputs.
    assert not any(isinstance(v, (bytes, bytearray)) for v in inputs.values())
    assert fake.updated[0]["outputs"]["text"] == "My name is Priya Sharma."
