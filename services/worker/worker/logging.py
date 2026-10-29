"""Structured JSON logging for the LegacyFlow worker (ERR-012).

SHARED COPY of the API's ``app/core/logging.py`` JSONFormatter: the
worker is a separate service with its own environment and does NOT
depend on the API package, so the formatter cannot be imported across
the boundary. Keep the two in sync — same payload shape (timestamp,
level, logger, message, request_id, service + merged ``extra={}``
fields); only the service name and the request-id source differ
(worker messages carry a dramatiq message id via ``extra={}`` where
relevant; there is no HTTP request context here).

Contract rule 9 applies identically: identifiers and counts only,
never PII or payloads.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

SERVICE_NAME = "legacyflow-worker"

_configured = False

# Standard LogRecord attributes — never copied into the payload as
# extras (mirrors the API formatter's reserved set after ERR-018).
_RESERVED = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }
)

_CORE_FIELDS = frozenset({"timestamp", "level", "logger", "message", "request_id", "service"})


class JSONFormatter(logging.Formatter):
    """Render log records as single-line JSON objects (API-identical)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "service": SERVICE_NAME,
        }
        # Merge extra={} fields (ERR-018 behaviour): non-reserved record
        # attributes join the payload; core fields are never overwritten.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in _CORE_FIELDS or key in payload:
                continue
            payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


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
