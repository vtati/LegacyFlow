"""F4 (2026-10-26): the Ask gateway's production wiring.

Production previously selected a provider class whose generate() raised
a 501 ConfigurationError, so every Ask in production failed while the
working grounded provider sat next to it. Now 'production' selects the
same deterministic grounded provider as dev: template assembly over
retrieved, refusal-fenced pack excerpts. No LLM is wired into Ask —
that remains a separate product decision.
"""

from __future__ import annotations

import pytest

from app.domains.ai import provider as ai_provider
from app.domains.ai.provider import AIContext, RetrievedDocument


def _context() -> AIContext:
    return AIContext(
        pack_id="us-va",
        jurisdiction_code="US-VA",
        pack_version="0.1.0",
        documents=[
            RetrievedDocument(
                content_id="guardian-basics-v1",
                title="Choosing Guardians",
                section="Backups",
                body_markdown="Naming a guardian tells the court who you would want to care for your minor children.",
            )
        ],
    )


def test_production_selects_the_working_grounded_provider() -> None:
    provider = ai_provider.get_provider("production")
    response = provider.generate("What is a guardian?", _context())
    assert "Choosing Guardians" in response.answer
    assert "not legal advice" in response.answer
    assert response.citations and response.citations[0].source == "us-va"


def test_default_and_dev_select_the_same_provider() -> None:
    default = ai_provider.get_provider()
    dev = ai_provider.get_provider("deterministic_dev")
    assert type(default) is type(dev) is type(ai_provider.get_provider("production"))


def test_unknown_provider_still_fails_closed() -> None:
    from app.core.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        ai_provider.get_provider("some-llm")
