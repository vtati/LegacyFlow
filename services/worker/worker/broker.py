"""Dramatiq broker wiring.

Slice-1: Redis in configured deployments, in-process ``StubBroker`` locally.
Real jobs (file scanning, document assembly, reminders — artifact §6.5)
arrive in Slice 4.
"""

from __future__ import annotations

import logging

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

from worker.settings import get_worker_settings

logger = logging.getLogger(__name__)


def get_broker() -> dramatiq.Broker:
    """Return the Dramatiq broker for this deployment.

    Uses ``RedisBroker`` when ``REDIS_URL`` is configured; otherwise falls
    back to an in-process ``StubBroker`` and logs a warning so local
    development and tests run without Redis.
    """
    settings = get_worker_settings()
    if settings.REDIS_URL:
        # dramatiq ships without type stubs; the constructor call is typed via ignore.
        return RedisBroker(url=settings.REDIS_URL)  # type: ignore[no-untyped-call]
    logger.warning("REDIS_URL is not set; using Dramatiq StubBroker (dev only).")
    return StubBroker()
