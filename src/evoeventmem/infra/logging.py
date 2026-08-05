from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class StructuredLogFormatter(logging.Formatter):
    """Emit one JSON object per log record with a stable key set.

    Extras attached to the record (for example ``event``, ``memory_id``,
    ``store``) are copied into the payload; the reserved logging fields are
    never included. Callers must never pass raw user memory content or
    secrets as extras.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.__dict__.get("event", record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED_KEYS or key in {"event"}:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Handler:
    """Attach a structured handler to the ``evoeventmem`` logger and return it."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(StructuredLogFormatter())
    logger = logging.getLogger("evoeventmem")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    return handler
