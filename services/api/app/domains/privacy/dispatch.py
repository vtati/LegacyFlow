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

REMINDER_DISPATCH_ACTOR = "send_due_reminders"
REMINDER_DISPATCH_QUEUE = "default"


def enqueue_reminder_dispatch(redis_url: str) -> None:
    """Enqueue one ``send_due_reminders`` run on the shared broker."""
    from dramatiq import Message
    from dramatiq.brokers.redis import RedisBroker

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
