"""AI provider abstraction for the grounded gateway (Slice 4).

The gateway never lets a provider answer from its own weights: every
provider receives an :class:`AIContext` of already-retrieved, approved pack
documents and must ground every claim in them.

* :class:`GroundedTemplateProvider` — template-based answers assembled
  from retrieved excerpts (no LLM calls, no network). This IS the
  production provider for the Ask gateway (F4, 2026-10-26): the
  earlier production class was an unimplemented 501 stub, which left
  Ask broken in production while this working provider sat beside it.
  Wiring an LLM into Ask is a separate product decision, not a
  config flip — there is deliberately no LLM provider for the gateway.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.errors import ConfigurationError, ProviderError
from app.domains.ai.schemas import Citation

import httpx

logger = logging.getLogger(__name__)

_DETERMINISTIC_DEV = "deterministic_dev"
_PRODUCTION = "production"

# Shared HTTP client with connection pooling — reuses TCP/TLS connections
# to api.openai.com across turns instead of a fresh handshake every time.
_http_client: httpx.Client | None = None


def get_http_client() -> httpx.Client:
    """Return the shared httpx client, creating it on first use."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=60.0)
    return _http_client


@dataclass(frozen=True)
class RetrievedDocument:
    """One approved pack document matched for the question."""

    content_id: str
    title: str
    section: str | None
    body_markdown: str


@dataclass(frozen=True)
class AIContext:
    """Everything a provider may use to compose an answer."""

    pack_id: str
    jurisdiction_code: str
    pack_version: str
    documents: list[RetrievedDocument] = field(default_factory=list)


@dataclass(frozen=True)
class AIResponse:
    """Provider output: answer text plus one citation per cited document."""

    answer: str
    citations: list[Citation] = field(default_factory=list)


class AIProvider(Protocol):
    """Generate a grounded answer from already-retrieved approved content."""

    def generate(self, question: str, context: AIContext) -> AIResponse:
        """Compose an answer grounded in ``context.documents``."""
        ...


def _excerpt(body: str, limit: int) -> str:
    """Collapse whitespace and truncate a body to ``limit`` characters."""
    collapsed = re.sub(r"\s+", " ", body).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"


def _approved_excerpts_block(documents: list[RetrievedDocument]) -> str:
    """Render the retrieved approved excerpts for the model's system prompt.

    The interview system prompt instructs the model to ground every factual
    claim in "the approved educational excerpts provided with this turn" —
    this block is what makes that claim true. Deterministic: one excerpt per
    retrieved document, in retrieval order. Empty when nothing was retrieved,
    so turns without matching pack content are byte-identical to before.
    """
    if not documents:
        return ""
    lines = [
        "",
        "APPROVED EDUCATIONAL EXCERPTS — the only sources you may rely on for "
        "factual claims. Ground every factual claim in these excerpts and cite "
        "them; never answer from your own general knowledge.",
    ]
    for index, doc in enumerate(documents, start=1):
        section = f" ({doc.section})" if doc.section else ""
        lines.append(f"[{index}] {doc.title}{section}:\n{_excerpt(doc.body_markdown, 2000)}")
    return "\n".join(lines)


class GroundedTemplateProvider:
    """Template-based answers from retrieved docs (no LLM, no network).

    The answer is an assembly of verbatim excerpts from approved pack
    content plus fixed educational framing. It never synthesizes new
    legal claims, clauses, or advice. This is the Ask gateway's
    production provider as well as its dev/test provider (F4).
    """

    def generate(self, question: str, context: AIContext) -> AIResponse:
        del question  # The answer template ignores the question wording.
        lines = [
            f"Based on the approved educational materials in the {context.pack_id} pack:",
            "",
        ]
        citations: list[Citation] = []
        for index, doc in enumerate(context.documents, start=1):
            excerpt = _excerpt(doc.body_markdown, 500)
            citations.append(Citation(source=context.pack_id, section=doc.section, excerpt=excerpt))
            lines.append(f"[{index}] From '{doc.title}': {_excerpt(doc.body_markdown, 320)}")
            lines.append("")
        lines.append(
            "This summary is educational information from approved pack materials "
            "only. It is not legal advice, and your attorney finalizes all "
            "planning decisions."
        )
        return AIResponse(answer="\n".join(lines), citations=citations)


# Back-compat alias: the provider was named for its original dev/test
# role; it is the production provider now (F4).
DeterministicDevProvider = GroundedTemplateProvider


def get_provider(name: str | None = None) -> AIProvider:
    """Select the provider from ``AI_PROVIDER`` (default: deterministic_dev).

    Both ``deterministic_dev`` and ``production`` select the grounded
    template provider — it is the only Ask provider that exists, by
    design (F4). Unknown names fail closed.
    """
    selected = (name or os.environ.get("AI_PROVIDER", _DETERMINISTIC_DEV)).strip().lower()
    if selected in (_DETERMINISTIC_DEV, _PRODUCTION):
        return GroundedTemplateProvider()
    raise ConfigurationError(
        f"Unknown AI provider {selected!r}. Expected 'deterministic_dev' or 'production'."
    )


# ---------------------------------------------------------------------------
# Interview providers: conversational LLM + speech-to-text (Phase 1)
#
# The interviewer needs more than grounded Q&A: multi-turn conversation with
# structured field proposals (via tool calls) and audio transcription for
# dictation. Both capabilities sit behind registries so models are swappable
# by configuration. The grounding contract is unchanged: providers receive
# already-retrieved approved content and every legal claim must cite it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """One message in the interview conversation history."""

    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass(frozen=True)
class FieldProposalDraft:
    """A model-proposed field value, before validation and user confirmation."""

    question_key: str
    value: Any
    confidence: float
    source_snippet: str


@dataclass(frozen=True)
class InterviewContext:
    """Everything an interview provider may use for one turn."""

    pack_id: str
    jurisdiction_code: str
    pack_version: str
    documents: list[RetrievedDocument] = field(default_factory=list)
    missing_required_keys: list[str] = field(default_factory=list)
    answered_required: int = 0
    total_required: int = 0
    pending_proposal_keys: list[str] = field(default_factory=list)
    question_labels: dict[str, str] = field(default_factory=dict)
    # Interview v2 focus for THIS turn: when set, the no-text fallback
    # reply asks about the focus (cluster gap keys, or the bundle
    # question verbatim) instead of the pack-order missing list — the
    # visible question must match what the prompt intended, because the
    # user's next answer is interpreted against this turn's focus.
    focus_keys: list[str] = field(default_factory=list)
    focus_question_text: str | None = None


@dataclass(frozen=True)
class InterviewTurnResponse:
    """Provider output for one interview turn."""

    reply: str
    citations: list[Citation] = field(default_factory=list)
    proposals: list[FieldProposalDraft] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(Protocol):
    """Multi-turn interview generation with structured field proposals."""

    name: str

    def chat(
        self,
        *,
        system_prompt: str,
        messages: list[ChatMessage],
        context: InterviewContext,
    ) -> InterviewTurnResponse:
        """Generate the next assistant turn, proposing field values as drafts."""
        ...


class STTProvider(Protocol):
    """Speech-to-text for the dictation input accessory."""

    name: str

    def transcribe(self, audio: bytes, *, filename: str, language: str | None = None) -> str:
        """Transcribe ``audio``; return the transcript text."""
        ...


# ---------------------------------------------------------------------------
# Deterministic interview providers (dev/test — no network)
# ---------------------------------------------------------------------------


class DeterministicDevInterviewProvider:
    """Canned interview turns for tests: no LLM, no network, no proposals."""

    name = "deterministic_dev"

    def chat(
        self,
        *,
        system_prompt: str,
        messages: list[ChatMessage],
        context: InterviewContext,
    ) -> InterviewTurnResponse:
        del system_prompt, messages
        citations = [
            Citation(
                source=context.pack_id,
                section=doc.section,
                excerpt=_excerpt(doc.body_markdown, 320),
            )
            for doc in context.documents
        ]
        if context.missing_required_keys:
            nxt = context.missing_required_keys[0]
            reply = (
                f"Let's continue with your information. The next item we need is "
                f"'{nxt}' ({context.answered_required} of {context.total_required} "
                f"required items complete). What would you like to provide?"
            )
        else:
            reply = (
                "All required items are collected. Please review each proposed "
                "value in the verification list, and your attorney will review "
                "the package next."
            )
        return InterviewTurnResponse(reply=reply, citations=citations, proposals=[])


class DeterministicDevSTTProvider:
    """Canned transcription for tests."""

    name = "deterministic_dev"

    def transcribe(self, audio: bytes, *, filename: str, language: str | None = None) -> str:
        del audio, filename, language
        return "[test transcription]"


# ---------------------------------------------------------------------------
# OpenAI adapters (production)
# ---------------------------------------------------------------------------

_PROPOSE_TOOL_NAME = "propose_field_values"


def _propose_tool_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _PROPOSE_TOOL_NAME,
            "description": (
                "Record structured field values extracted from the user's message. "
                "Call once per turn with every confident value; omit uncertain ones."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question_key": {"type": "string"},
                                "value": {},
                                "confidence": {"type": "number"},
                                "source_snippet": {"type": "string"},
                            },
                            "required": ["question_key", "value", "confidence", "source_snippet"],
                        },
                    }
                },
                "required": ["proposals"],
            },
        },
    }


def _apply_model_contract(
    payload: dict[str, Any],
    model: str,
    *,
    temperature: float,
    completion_cap: int = 6000,
    with_tools: bool,
) -> dict[str, Any]:
    """Shape ``payload`` to the model's explicit capability contract.

    The contract comes from :data:`app.core.config.MODEL_CAPABILITIES`
    (F5) — never from the model name's prefix. Verified live
    (2026-10-25) for the gpt-5 family: function tools + reasoning_effort
    other than 'none' is a 400 on /v1/chat/completions, temperature is
    not accepted, and the output cap is max_completion_tokens. The
    gpt-4o / gpt-4o-mini payload shape stays byte-identical for the
    legacy models every non-v2 path still uses.
    """
    from app.core.config import model_capabilities

    caps = model_capabilities(model)
    if caps.send_temperature:
        payload["temperature"] = temperature
    if caps.completion_token_param is not None:
        payload[caps.completion_token_param] = completion_cap
    if with_tools and caps.reasoning_effort_none_with_tools:
        # Reasoning pinned to 'none' so function tools are accepted.
        payload["reasoning_effort"] = "none"
    return payload


class OpenAIChatProvider:
    """Interview turns via the OpenAI chat-completions API with tool calls."""

    name = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def chat(
        self,
        *,
        system_prompt: str,
        messages: list[ChatMessage],
        context: InterviewContext,
    ) -> InterviewTurnResponse:
        # The retrieved approved excerpts travel with the system prompt so the
        # model's grounding instruction ("ground every factual claim in the
        # approved educational excerpts provided with this turn") refers to
        # content the model can actually see. Citations are still assembled
        # deterministically from context.documents below.
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt + _approved_excerpts_block(context.documents),
                }
            ]
            + [{"role": m.role, "content": m.content} for m in messages],
            "tools": [_propose_tool_spec()],
            "tool_choice": "auto",
        }
        # Payload contract per the capability table (F5): legacy models
        # get temperature and nothing else; next-gen models get no
        # temperature, reasoning pinned to 'none' (so function tools are
        # accepted), and a generous completion cap so tool-call
        # arguments never truncate mid-JSON.
        _apply_model_contract(payload, self._model, temperature=0.2, with_tools=True)
        try:
            response = get_http_client().post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Log provider internals server-side; the client gets a generic
            # 502 (ProviderError), never network or API detail.
            logger.warning("OpenAI chat request failed: %s", exc)
            raise ProviderError() from exc
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        reply = str(message.get("content") or "").strip()
        proposals: list[FieldProposalDraft] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name") != _PROPOSE_TOOL_NAME:
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                continue
            for item in args.get("proposals") or []:
                try:
                    confidence = float(item.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                proposals.append(
                    FieldProposalDraft(
                        question_key=str(item.get("question_key", "")),
                        value=item.get("value"),
                        confidence=min(max(confidence, 0.0), 1.0),
                        source_snippet=str(item.get("source_snippet", ""))[:500],
                    )
                )
        if not reply:
            # The model emitted only tool calls (or an empty message): never
            # hand the user a blank turn. Build a specific fallback that names
            # what was recorded and asks the next missing item using the
            # pack's human question label — never a generic "tell me more".
            # Only count proposals for keys the pack actually defines, so the
            # count can't promise values that post-chat validation will drop.
            labels = context.question_labels or {}
            valid_proposals = [
                pr for pr in proposals if pr.question_key in labels
            ]
            recorded = (
                f"{len(valid_proposals)} value{'s' if len(valid_proposals) != 1 else ''}"
                if valid_proposals else "that"
            )
            # Skip keys that already have pending proposals awaiting confirmation,
            # plus keys the model just proposed for this turn (they become
            # pending once post-chat validation stores them).
            pending_set = set(context.pending_proposal_keys or [])
            just_proposed = {pr.question_key for pr in valid_proposals}
            skip = pending_set | just_proposed
            # A verbatim focus question (a bundle offer) outranks the
            # generic next-item composition entirely.
            focus_text_used = bool(context.focus_question_text)
            multi_ask_text: str | None = None
            if focus_text_used:
                reply = (
                    f"Thanks — I've recorded {recorded}. "
                    f"{context.focus_question_text}"
                )
                next_key = None
            elif context.focus_keys:
                # A focus is set (this turn's cluster): ask ONLY within
                # it. If every focus key was just proposed or is pending,
                # fall through to the review-cards reply — never escape
                # to a pack-order question outside the focus (the visible
                # question must match the turn's intent; review I-1).
                askable = [
                    k
                    for k in context.focus_keys
                    if k not in skip and k in labels
                ]
                next_key = askable[0] if askable else None
                if len(askable) >= 2:
                    # v2 turn economics (fix iteration 1, A/B set 3):
                    # the focus IS a merged / batched / sweep list —
                    # asking only its first key silently discarded the
                    # rest and cost a turn per key in production, where
                    # this no-text fallback is the habitual ask path.
                    # Render the whole focus as one numbered ask. A
                    # single askable key keeps the legacy rendering.
                    multi_ask_text = " ".join(
                        f"{i}) {labels[k]}"
                        for i, k in enumerate(askable, start=1)
                    )
            else:
                next_key = next(
                    (
                        k
                        for k in context.missing_required_keys
                        if k not in skip and k in labels
                    ),
                    None,
                )
            if focus_text_used:
                pass  # reply already composed above
            elif multi_ask_text:
                reply = (
                    f"Thanks — I've recorded {recorded}. "
                    f"Next, a few things: {multi_ask_text}"
                )
            elif next_key:
                reply = (
                    f"Thanks — I've recorded {recorded}. "
                    f"Next: {labels[next_key]}"
                )
            elif valid_proposals:
                # Every remaining missing key is awaiting user confirmation —
                # the only thing blocking progress is the review cards.
                reply = (
                    f"Thanks — I've recorded {recorded}. "
                    "Please review the proposed values above to continue."
                )
            else:
                reply = "Thanks — I've noted that."
        usage: dict[str, int] = {}
        raw_usage = data.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                usage[key] = int(raw_usage.get(key, 0))
            except (TypeError, ValueError):
                pass
        citations = [
            Citation(
                source=context.pack_id,
                section=doc.section,
                excerpt=_excerpt(doc.body_markdown, 320),
            )
            for doc in context.documents
        ]
        return InterviewTurnResponse(
            reply=reply, citations=citations, proposals=proposals, usage=usage
        )


class WhisperSTTProvider:
    """Dictation transcription via the OpenAI audio API."""

    name = "whisper"

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def transcribe(self, audio: bytes, *, filename: str, language: str | None = None) -> str:
        data: dict[str, str] = {"model": self._model}
        if language:
            data["language"] = language
        try:
            response = get_http_client().post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                data=data,
                files={"file": (filename, audio, "audio/webm")},
                timeout=120.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Whisper transcription failed: %s", exc)
            raise ProviderError() from exc
        return str(response.json().get("text", ""))


# ---------------------------------------------------------------------------
# JSON completion provider (F1, 2026-10-26)
#
# Document extraction, consistency review, and vault analysis each need
# one structured JSON answer from a model — no tools, no conversation.
# They used to hand-roll identical httpx POSTs to api.openai.com; they
# now share this gateway so the HTTP call, error mapping, usage
# accounting, and the F5 payload contract live in exactly one place.
# Callers keep their own prompts, output parsing, and domain fallbacks.
# ---------------------------------------------------------------------------


class JsonCompletionProvider(Protocol):
    """One-shot JSON-mode chat completion."""

    name: str

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        temperature: float,
        timeout: float = 90.0,
    ) -> tuple[str, dict[str, int]]:
        """Return (raw message content, token usage).

        Raises :class:`ProviderError` on any transport/HTTP failure —
        the client-facing error stays generic, as with the interview
        provider.
        """
        ...


class OpenAIJsonProvider:
    """JSON completions via the OpenAI chat-completions API."""

    name = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        temperature: float,
        timeout: float = 90.0,
    ) -> tuple[str, dict[str, int]]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        # Same capability contract as the interview provider (F5),
        # minus the tools-specific reasoning pin.
        _apply_model_contract(payload, self._model, temperature=temperature, with_tools=False)
        try:
            response = get_http_client().post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("OpenAI JSON completion request failed: %s", exc)
            raise ProviderError() from exc
        data = response.json()
        usage: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                usage[key] = int((data.get("usage") or {}).get(key, 0))
            except (TypeError, ValueError):
                pass
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
        return str(content), usage


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_LLM_FACTORIES: dict[str, Callable[[], LLMProvider]] = {}
_STT_FACTORIES: dict[str, Callable[[], STTProvider]] = {}
_JSON_FACTORIES: dict[str, Callable[..., JsonCompletionProvider]] = {}


def _openai_chat_factory(model: str | None = None) -> LLMProvider:
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise ConfigurationError("AI_LLM_PROVIDER=openai requires OPENAI_API_KEY to be set.")
    return OpenAIChatProvider(
        api_key=settings.OPENAI_API_KEY,
        model=model or settings.OPENAI_INTERVIEW_MODEL,
    )


def _whisper_factory() -> STTProvider:
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise ConfigurationError("AI_STT_PROVIDER=whisper requires OPENAI_API_KEY to be set.")
    return WhisperSTTProvider(api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_WHISPER_MODEL)


def _openai_json_factory(model: str | None = None) -> JsonCompletionProvider:
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise ConfigurationError("AI_LLM_PROVIDER=openai requires OPENAI_API_KEY to be set.")
    return OpenAIJsonProvider(
        api_key=settings.OPENAI_API_KEY,
        model=model or settings.OPENAI_CHAT_MODEL,
    )


_LLM_FACTORIES["deterministic_dev"] = (
    lambda model=None: DeterministicDevInterviewProvider()
)
_LLM_FACTORIES["openai"] = _openai_chat_factory
_STT_FACTORIES["deterministic_dev"] = lambda: DeterministicDevSTTProvider()
_STT_FACTORIES["whisper"] = _whisper_factory
_JSON_FACTORIES["openai"] = _openai_json_factory


def get_llm_provider(name: str | None = None, *, model: str | None = None) -> LLMProvider:
    """Select the interview LLM from ``AI_LLM_PROVIDER`` (default: deterministic_dev).

    ``model`` overrides the provider's model for this call only (the
    Interview v2 stronger-extractor path); it is honored by the OpenAI
    factory and ignored by the deterministic dev provider.
    """
    from app.core.config import get_settings

    selected = (name or get_settings().AI_LLM_PROVIDER).strip().lower()
    factory = _LLM_FACTORIES.get(selected)
    if factory is None:
        raise ConfigurationError(
            f"Unknown AI_LLM_PROVIDER {selected!r}. Expected one of: {sorted(_LLM_FACTORIES)}."
        )
    return factory(model=model)


def get_stt_provider(name: str | None = None) -> STTProvider:
    """Select the STT provider from ``AI_STT_PROVIDER`` (default: deterministic_dev)."""
    from app.core.config import get_settings

    selected = (name or get_settings().AI_STT_PROVIDER).strip().lower()
    factory = _STT_FACTORIES.get(selected)
    if factory is None:
        raise ConfigurationError(
            f"Unknown AI_STT_PROVIDER {selected!r}. Expected one of: {sorted(_STT_FACTORIES)}."
        )
    return factory()


def get_json_provider(model: str | None = None) -> JsonCompletionProvider:
    """Select the JSON-completion provider from ``AI_LLM_PROVIDER``.

    Only the OpenAI adapter offers JSON completion; the deterministic
    dev provider has no model behind it, so callers on that path keep
    their existing no-call behavior and never reach this factory (the
    ConfigurationError is the fail-closed backstop).
    """
    from app.core.config import get_settings

    selected = get_settings().AI_LLM_PROVIDER.strip().lower()
    factory = _JSON_FACTORIES.get(selected)
    if factory is None:
        raise ConfigurationError(
            f"AI_LLM_PROVIDER {selected!r} does not provide JSON completions. "
            f"Expected one of: {sorted(_JSON_FACTORIES)}."
        )
    return factory(model=model)


def available_llm_providers() -> list[str]:
    return sorted(_LLM_FACTORIES)


def available_stt_providers() -> list[str]:
    return sorted(_STT_FACTORIES)
