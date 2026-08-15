"""JSONL trace capture for adapter demo runs.

Captures structured records of tool calls and agent decisions without any
dependency on the agent runtime; a trace is a plain, JSON-serializable list.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


class TraceCapture:
    """Accumulates trace records and optionally flushes them to a JSONL file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._records: list[dict[str, Any]] = []

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    def record(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {"event": event, "ts": datetime.now(UTC).isoformat()}
        payload.update(fields)
        self._records.append(payload)

    def save(self) -> Path | None:
        if self._path is None:
            return None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(record, default=_json_default, ensure_ascii=False)
            for record in self._records
        ]
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self._path
