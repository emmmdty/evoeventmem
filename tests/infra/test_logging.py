from __future__ import annotations

import io
import json
import logging

from evoeventmem.infra.config import redact_dsn
from evoeventmem.infra.logging import StructuredLogFormatter, request_id_var


def _capture(level: int = logging.INFO) -> tuple[list[str], logging.Logger]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredLogFormatter())
    logger = logging.getLogger("evoeventmem.test")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    return stream, logger


def test_formatter_emits_stable_json_payload() -> None:
    stream, logger = _capture()
    logger.info("memory written", extra={"event": "memory.written", "memory_id": "abc-123"})

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "memory.written"
    assert payload["memory_id"] == "abc-123"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "evoeventmem.test"
    assert set(payload) == {"ts", "level", "logger", "event", "memory_id"}


def test_formatter_includes_request_id_from_contextvar() -> None:
    stream, logger = _capture()
    token = request_id_var.set("req-42")
    try:
        logger.info("request handled", extra={"event": "http.request"})
    finally:
        request_id_var.reset(token)

    payload = json.loads(stream.getvalue())
    assert payload["request_id"] == "req-42"


def test_formatter_omits_request_id_when_contextvar_unset() -> None:
    stream, logger = _capture()
    logger.info("no request id", extra={"event": "startup"})
    payload = json.loads(stream.getvalue())
    assert "request_id" not in payload


def test_formatter_records_exception_text() -> None:
    stream, logger = _capture()
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("operation failed", extra={"event": "store.fallback"})

    payload = json.loads(stream.getvalue())
    assert "ValueError: boom" in payload["exception"]


def test_redact_dsn_masks_password() -> None:
    dsn = "postgresql://user:swordfish@host:5432/db"
    assert redact_dsn(dsn) == "postgresql://user:***@host:5432/db"
    assert "swordfish" not in redact_dsn(dsn)


def test_redact_dsn_keeps_dsn_without_password_unchanged() -> None:
    dsn = "postgresql://host:5432/db"
    assert redact_dsn(dsn) == dsn
