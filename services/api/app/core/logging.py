"""Structured JSON logging for the LegacyFlow API (stdlib only).

Every record carries ``timestamp``, ``level``, ``logger``, ``message``,
``request_id`` and ``service``. The ``request_id`` comes from a
:mod:`contextvars` variable populated by the request-ID middleware in
``app.main``.

Engineering-contract rule 9: never log PII, plan content, credentials, or
raw AI prompts/responses. Log identifiers (request IDs, resource UUIDs) and
counts — never payloads.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime

SERVICE_NAME = "legacyflow-api"

_request_id: ContextVar[str | None] = ContextVar("legacyflow_request_id", default=None)

_configured = False


# Standard LogRecord attributes — never treated as extras.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }
)


class JSONFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(),
            "service": SERVICE_NAME,
        }
        # ERR-018: merge extra={} fields (e.g. the unhandled handler's
        # `path`, escalation's `rule_id`) into the payload — they were
        # silently dropped, so alerts keyed on them never fired. Core
        # fields are never overwritten by an extra.
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key in payload:
                continue
            payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind ``request_id`` for the current request context."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous ``request_id`` binding."""
    _request_id.reset(token)


def get_request_id() -> str | None:
    """Return the current request's ID, if one is bound."""
    return _request_id.get()


def configure_logging(level: int = logging.INFO) -> None:
    """Attach the JSON handler to the root logger (idempotent)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (renders as JSON once configured)."""
    return logging.getLogger(name)
