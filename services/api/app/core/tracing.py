"""Optional LangSmith tracing for model calls (eval pipeline, 2026-11-04).

Every model call in the product flows through
``app.domains.ai.provider``; that layer wraps its HTTP calls in
:class:`LLMTrace` (or the :func:`traced_llm_call` context manager built
on it), so one instrumentation point covers the interviewer, the
consistency reviewer, the attorney reviewer, the vault analyst, the
document extractor, and dictation.

Design rules (pinned by tests/test_tracing.py):

- OFF unless ``LANGSMITH_TRACING`` is set AND ``LANGSMITH_API_KEY`` is
  present. With either missing, tracing is a pure no-op and no LangSmith
  client is ever built.
- FAIL-OPEN, always: tracing must never break, mask, or alter a model
  call. Every LangSmith interaction is guarded; failures are logged at
  debug level and vanish. An exception inside a traced block is
  recorded on the run (exception class name only) and propagates
  unchanged.
- NEVER ON THE REQUEST PATH: the client is built with
  ``auto_batch_tracing=True`` requested explicitly, so run posts are
  enqueued to the SDK's background thread rather than performed
  synchronously inside a model call. This is requested, not assumed —
  a future SDK default change must not silently move posting inline.
- The client is built once per process. Rotating the LangSmith key
  therefore takes a process restart, which is how Railway applies env
  changes anyway. If client construction fails (bad install, bad
  endpoint), the failure is cached for a retry window instead of being
  re-attempted on every model call.
- NO client PII in tags or metadata: agent name, model, and environment
  only. (Inputs/outputs carry the prompts themselves — that is the
  point of a trace, and why tracing is an explicit per-environment
  opt-in rather than a default.)
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

logger = logging.getLogger(__name__)

_client: Any = None
_client_failed_at: float | None = None

#: After a client-construction failure, wait this long before retrying.
#: Bounds the per-call overhead of a broken tracing setup to one failed
#: construction per window instead of one per model call.
_CLIENT_RETRY_SECONDS = 300.0


def _settings() -> Any:
    from app.core.config import get_settings

    return get_settings()


def _build_client(settings: Any) -> Any:
    """Construct the LangSmith client (lazy import: zero cost when off).

    ``auto_batch_tracing=True`` is passed explicitly so run posting
    stays on the SDK's background thread (see module docstring).
    """
    from langsmith import Client

    key = getattr(settings, "LANGSMITH_API_KEY", "")
    return Client(
        api_key=key,
        api_url=settings.LANGSMITH_ENDPOINT,
        auto_batch_tracing=True,
    )


def _get_client(settings: Any) -> Any:
    """Return the shared client, or None when unavailable.

    Never raises. Construction failures are cached for
    ``_CLIENT_RETRY_SECONDS`` so a broken setup costs one attempt per
    window, not one per model call. The check-then-set on ``_client``
    can race under threads; the worst case is a duplicate client built
    and discarded, which is harmless.
    """
    global _client, _client_failed_at
    if _client is not None:
        return _client
    if (
        _client_failed_at is not None
        and (time.monotonic() - _client_failed_at) < _CLIENT_RETRY_SECONDS
    ):
        return None
    try:
        _client = _build_client(settings)
        _client_failed_at = None
    except Exception:
        _client_failed_at = time.monotonic()
        logger.debug("LangSmith client construction failed", exc_info=True)
        return None
    return _client


def tracing_enabled(settings: Any | None = None) -> bool:
    settings = settings or _settings()
    return bool(
        getattr(settings, "LANGSMITH_TRACING", False)
        and getattr(settings, "LANGSMITH_API_KEY", "")
    )


class LLMTrace:
    """One traced model call. Use directly when the call site's control
    flow does not fit a ``with`` block; :func:`traced_llm_call` is the
    context-manager form. All methods are safe to call when tracing is
    off or when LangSmith is down: they quietly do nothing."""

    def __init__(
        self,
        *,
        name: str,
        agent: str,
        model: str,
        inputs: dict[str, Any],
    ) -> None:
        # Guarded like everything else here: if settings cannot even be
        # read, tracing is simply off — a settings problem must never
        # become a model-call problem (this exact failure mode appeared
        # in tests that construct providers without a full environment).
        try:
            self._settings = _settings()
            self._enabled = tracing_enabled(self._settings)
        except Exception:
            self._settings = None
            self._enabled = False
        self._name = name
        self._agent = agent
        self._model = model
        self._inputs = inputs
        self._run_id = uuid4()
        self._start_time = datetime.now(timezone.utc)
        self._finished = False
        self.outputs: dict[str, Any] = {}
        self.usage: dict[str, int] = {}

    def set_outputs(self, **kwargs: Any) -> None:
        self.outputs.update(kwargs)

    def set_usage(self, **kwargs: int) -> None:
        self.usage.update(kwargs)

    def finish(self, error: BaseException | None = None) -> None:
        """Post the run. Idempotent; never raises."""
        if self._finished or not self._enabled:
            self._finished = True
            return
        self._finished = True
        try:
            client = _get_client(self._settings)
            if client is None:
                return
            client.create_run(
                id=self._run_id,
                name=self._name,
                run_type="llm",
                inputs=self._inputs,
                project_name=self._settings.LANGSMITH_PROJECT,
                tags=["legacyflow", f"agent:{self._agent}", f"model:{self._model}"],
                extra={
                    "metadata": {
                        "agent": self._agent,
                        "model": self._model,
                        "environment": getattr(self._settings, "ENVIRONMENT", "unknown"),
                    }
                },
                start_time=self._start_time,
            )
            client.update_run(
                self._run_id,
                outputs={**self.outputs, "usage": self.usage},
                # Class name only: exception strings can embed URLs and
                # upstream response text, which do not belong in a trace.
                error=(type(error).__name__ if error is not None else None),
                end_time=datetime.now(timezone.utc),
            )
        except Exception:
            # Fail-open is the contract: a tracing outage is invisible
            # to the product. Debug level so production logs stay clean.
            logger.debug("LangSmith trace post failed", exc_info=True)


@contextmanager
def traced_llm_call(
    *,
    name: str,
    agent: str,
    model: str,
    inputs: dict[str, Any],
) -> Iterator[LLMTrace]:
    """Context-manager form of :class:`LLMTrace`. Yields the trace; the
    caller fills outputs/usage as results arrive. An exception inside
    the block is recorded on the run and re-raised unchanged."""
    trace = LLMTrace(name=name, agent=agent, model=model, inputs=inputs)
    try:
        yield trace
    except BaseException as exc:
        trace.finish(error=exc)
        raise
    else:
        trace.finish()
