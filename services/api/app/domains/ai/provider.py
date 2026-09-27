"""AI provider abstraction for the grounded gateway (Slice 4).

The gateway never lets a provider answer from its own weights: every
provider receives an :class:`AIContext` of already-retrieved, approved pack
documents and must ground every claim in them.

* :class:`DeterministicDevProvider` — template-based answers assembled
  from retrieved excerpts (no LLM calls, no network). Used in dev/test.
* :class:`ProductionAIProvider` — interface placeholder; raising a 501
  :class:`ConfigurationError` until a production provider is wired in.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import ConfigurationError
from app.domains.ai.schemas import Citation

_DETERMINISTIC_DEV = "deterministic_dev"
_PRODUCTION = "production"


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


class DeterministicDevProvider:
    """Template-based answers from retrieved docs (no LLM, no network).

    The answer is an assembly of verbatim excerpts from approved pack
    content plus fixed educational framing. It never synthesizes new
    legal claims, clauses, or advice.
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


class ProductionAIProvider:
    """Production provider interface (not implemented in this deployment)."""

    def generate(self, question: str, context: AIContext) -> AIResponse:
        del question, context
        raise ConfigurationError(
            "The production AI provider is not configured for this deployment. "
            "Set AI_PROVIDER=deterministic_dev to use the template-based "
            "development provider."
        )


def get_provider(name: str | None = None) -> AIProvider:
    """Select the provider from ``AI_PROVIDER`` (default: deterministic_dev)."""
    selected = (name or os.environ.get("AI_PROVIDER", _DETERMINISTIC_DEV)).strip().lower()
    if selected == _DETERMINISTIC_DEV:
        return DeterministicDevProvider()
    if selected == _PRODUCTION:
        return ProductionAIProvider()
    raise ConfigurationError(
        f"Unknown AI provider {selected!r}. Expected 'deterministic_dev' or 'production'."
    )
