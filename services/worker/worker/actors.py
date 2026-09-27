"""Slice-1 stub actors. No real jobs exist yet; jobs arrive in Slice 4."""

from __future__ import annotations

import logging

import dramatiq

from worker.broker import get_broker

logger = logging.getLogger(__name__)

broker = get_broker()


@dramatiq.actor(broker=broker)
def example_ping() -> dict[str, bool]:
    """Slice-1 stub actor: logs and returns ``{"pong": True}``.

    Exists only to prove the broker wiring end to end; it performs no real
    work and must not be treated as a production job.
    """
    logger.info("example_ping executed")
    return {"pong": True}
