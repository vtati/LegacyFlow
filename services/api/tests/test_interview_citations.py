"""Unit regression tests for interview-turn citations (the Sources expander).

On 2026-10-02 the staging walkthrough failed its citation step: asking
"What does Virginia law require for a will to be valid?" produced an answer
but no Sources expander, because retrieval found no approved pack content and
the assistant turn carried an empty citations array (the web UI renders
Sources only when ``citations.length > 0``).

API-level regression tests for the full turn pipeline live in
``test_interview.py::TestInterviewCitations``; this module covers the two
deterministic units:

- ``ai_service._retrieve`` finds the approved will-validity doc for the
  staging probe question (with and without sample content allowed).
- ``OpenAIChatProvider.chat`` injects the retrieved excerpts into the
  outgoing system message (so the prompt's grounding instruction refers to
  content the model can actually see) and still assembles one citation per
  retrieved document (mocked HTTP, no network).
"""

from __future__ import annotations

from unittest import mock

from app.domains.ai import provider as ai_provider
from app.domains.ai.provider import (
    ChatMessage,
    InterviewContext,
    RetrievedDocument,
)

WILL_VALIDITY_QUESTION = "What does Virginia law require for a will to be valid?"


# ---------------------------------------------------------------------------
# Unit: retrieval against the real vendored pack
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_will_validity_question_retrieves_approved_doc(self) -> None:
        from app.domains.ai.service import _retrieve
        from app.domains.legal_content.pack_loader import clear_pack_cache, get_pack

        clear_pack_cache()
        pack = get_pack("US-VA", "0.1.0")
        for allow_sample in (True, False):
            docs = _retrieve(WILL_VALIDITY_QUESTION, pack, allow_sample=allow_sample)
            assert docs, f"no pack content retrieved (allow_sample={allow_sample})"
            assert docs[0]["content_id"] == "will-validity-basics-v1"
            assert docs[0]["status"] == "approved"


# ---------------------------------------------------------------------------
# Unit: OpenAI adapter injects excerpts and assembles citations (mocked HTTP)
# ---------------------------------------------------------------------------


def _fake_chat_response(content: str = "Here is some general information.") -> mock.Mock:
    payload = {
        "id": "chatcmpl-test",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    fake = mock.Mock()
    fake.json.return_value = payload
    fake.raise_for_status.return_value = None
    return fake


def _context_with_doc() -> InterviewContext:
    return InterviewContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="0.1.0",
        documents=[
            RetrievedDocument(
                content_id="will-validity-basics-v1",
                title="Will Validity Basics",
                section="Wills",
                body_markdown="Virginia law requires a valid will to be in writing and signed.",
            )
        ],
    )


class TestOpenAIExcerpts:
    def test_excerpts_injected_into_system_message(self) -> None:
        provider = ai_provider.OpenAIChatProvider(api_key="test-key", model="gpt-test")
        with mock.patch("httpx.post", return_value=_fake_chat_response()) as post:
            resp = provider.chat(
                system_prompt="SYS",
                messages=[ChatMessage(role="user", content=WILL_VALIDITY_QUESTION)],
                context=_context_with_doc(),
            )
        sent = post.call_args.kwargs["json"]
        system_content = sent["messages"][0]["content"]
        assert system_content.startswith("SYS")
        assert "APPROVED EDUCATIONAL EXCERPTS" in system_content
        assert "Will Validity Basics" in system_content
        assert "Virginia law requires a valid will" in system_content
        assert resp.citations and resp.citations[0].source == "us-va"

    def test_no_documents_leaves_system_prompt_untouched(self) -> None:
        provider = ai_provider.OpenAIChatProvider(api_key="test-key", model="gpt-test")
        context = InterviewContext(
            pack_id="us-va", jurisdiction_code="US-VA", pack_version="0.1.0", documents=[]
        )
        with mock.patch("httpx.post", return_value=_fake_chat_response()) as post:
            resp = provider.chat(
                system_prompt="SYS",
                messages=[ChatMessage(role="user", content="hello")],
                context=context,
            )
        sent = post.call_args.kwargs["json"]
        assert sent["messages"][0]["content"] == "SYS"
        assert resp.citations == []
