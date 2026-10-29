"""Deployment glue: enqueue worker actors on the shared Dramatiq broker.

The API and the worker share the Redis broker addressed by ``REDIS_URL``.
The worker registers its actors by plain function name (e.g.
``send_due_reminders``), so the API can address a message at that actor
without importing worker code.

``dramatiq``/``redis`` are imported lazily so the API still imports — and
the dispatch endpoint still returns 501 ``worker_broker_unavailable`` —
in environments without the worker client libraries installed.
"""

from __future__ import annotations

import logging

from app.core.errors import AppError

logger = logging.getLogger(__name__)

REMINDER_DISPATCH_ACTOR = "send_due_reminders"
REMINDER_DISPATCH_QUEUE = "default"


class WorkerBrokerUnavailableError(AppError):
    """Raised when the worker broker cannot accept a dispatch (ERR-007).

    Covers BOTH failure shapes: ``REDIS_URL`` unset (not configured) and
    configured-but-unreachable (broker enqueue raising ConnectionError
    etc.) — previously the second escaped as a raw 500 and the runbook's
    501 degradation signal never fired.
    """

    type = "urn:legacyflow:error:worker_broker_unavailable"
    title = "Worker broker unavailable"
    status = 501
    detail = (
        "Reminder dispatch is not configured for this deployment "
        "(REDIS_URL is unset). Configure the shared Dramatiq broker to "
        "enqueue worker runs."
    )


def enqueue_reminder_dispatch(redis_url: str) -> None:
    """Enqueue one ``send_due_reminders`` run on the shared broker.

    Raises :class:`WorkerBrokerUnavailableError` (501) when the broker
    cannot be reached, instead of leaking the raw broker exception.
    """
    from dramatiq import Message
    from dramatiq.brokers.redis import RedisBroker

    try:
        broker = RedisBroker(url=redis_url)  # type: ignore[no-untyped-call]
        broker.enqueue(
            Message(
                queue_name=REMINDER_DISPATCH_QUEUE,
                actor_name=REMINDER_DISPATCH_ACTOR,
                args=(),
                kwargs={},
                options={},
            )
        )
    except Exception as exc:
        logger.exception("reminder dispatch enqueue failed (broker unavailable)")
        raise WorkerBrokerUnavailableError() from exc
