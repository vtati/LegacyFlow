"""Dramatiq broker wiring.

Redis in configured deployments, in-process ``StubBroker`` locally.
Retry behaviour is DELIBERATE (ERR-003): the middleware stack is
spelled out here instead of inherited by accident — the ``Retries``
middleware carries the broker-level fallback bounds (max 3 retries,
5s–1h backoff); each actor in ``worker/actors.py`` narrows those with
its own ``max_retries``/backoff/``max_age`` options and names
``record_dead_letter`` as its ``on_retry_exhausted`` sink.

Two give-up paths exist and they are NOT equivalent: a message that
exhausts its retries is forwarded to ``record_dead_letter`` and
persisted in ``worker_dead_letters``; a message that exceeds its
``max_age`` is dropped by the ``AgeLimit`` middleware with only a
WARNING log — it never reaches the dead-letter sink. Age-expired
messages are dropped BY DESIGN and recovered by the periodic drains
instead: reminder dispatch re-runs each cadence (the
``send_due_reminders`` drain re-derives its work from the reminders
table), and ``scan_file`` is recovered by ``scan_pending_uploads``,
which re-derives pending uploads from ``file_uploads``.
"""

from __future__ import annotations

import logging
from typing import Any

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import (
    AgeLimit,
    Callbacks,
    Pipelines,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)

from worker.settings import get_worker_settings

logger = logging.getLogger(__name__)


def _middleware() -> list[Any]:
    """The explicit middleware stack (dramatiq defaults, with the
    Retries bounds made deliberate — see module docstring)."""
    return [
        AgeLimit(),
        TimeLimit(),
        ShutdownNotifications(),
        Callbacks(),
        Pipelines(),
        Retries(max_retries=3, min_backoff=5_000, max_backoff=3_600_000),
    ]


def get_broker() -> dramatiq.Broker:
    """Return the Dramatiq broker for this deployment.

    Uses ``RedisBroker`` when ``REDIS_URL`` is configured; otherwise falls
    back to an in-process ``StubBroker`` and logs a warning so local
    development and tests run without Redis.
    """
    settings = get_worker_settings()
    if settings.REDIS_URL:
        # dramatiq ships without type stubs; the constructor call is typed via ignore.
        return RedisBroker(url=settings.REDIS_URL, middleware=_middleware())  # type: ignore[no-untyped-call]
    logger.warning("REDIS_URL is not set; using Dramatiq StubBroker (dev only).")
    return StubBroker(middleware=_middleware())
