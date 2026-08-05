from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class NormalizationError(ValueError):
    """Raised when a source sample cannot be normalized."""


class NormalizedEvidenceRef(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None


class NormalizedTurn(BaseModel):
    turn_id: str = Field(min_length=1)
    speaker: str = Field(min_length=1)
    content: str = Field(min_length=1)
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class NormalizedSession(BaseModel):
    session_id: str = Field(min_length=1)
    timestamp: datetime
    turns: list[NormalizedTurn] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class NormalizedQuestion(BaseModel):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str | None = None
    category: str | None = None
    asked_at: datetime | None = None
    evidence: list[NormalizedEvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("asked_at")
    @classmethod
    def require_aware_asked_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("asked_at must be timezone-aware")
        return value


class NormalizedEventSummary(BaseModel):
    session_id: str = Field(min_length=1)
    date: datetime | None = None
    events: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("date")
    @classmethod
    def require_aware_date(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("date must be timezone-aware")
        return value


class NormalizedRecord(BaseModel):
    dataset: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    sessions: list[NormalizedSession] = Field(min_length=1)
    questions: list[NormalizedQuestion] = Field(min_length=1)
    event_summaries: list[NormalizedEventSummary] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def iter_longmemeval_records(path: Path) -> Iterator[NormalizedRecord]:
    for index, sample in enumerate(_iter_json_array(path)):
        try:
            yield _normalize_longmemeval_sample(sample)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise NormalizationError(f"longmemeval sample index {index}: {exc}") from exc


def iter_locomo_records(path: Path) -> Iterator[NormalizedRecord]:
    for index, sample in enumerate(_iter_json_array(path)):
        sample_id = _diagnostic_sample_id(sample, index)
        try:
            yield _normalize_locomo_sample(sample)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise NormalizationError(f"locomo sample {sample_id}: {exc}") from exc


def _normalize_longmemeval_sample(sample: Any) -> NormalizedRecord:
    record = _require_mapping(sample)
    sample_id = _require_str(record, "question_id")
    session_ids = _require_list(record, "haystack_session_ids")
    session_dates = _require_list(record, "haystack_dates")
    session_turns = _require_list(record, "haystack_sessions")
    if not (len(session_ids) == len(session_dates) == len(session_turns)):
        raise ValueError("haystack_session_ids, haystack_dates, and haystack_sessions must align")

    sessions = [
        _normalize_longmemeval_session(session_id, session_date, turns)
        for session_id, session_date, turns in zip(
            session_ids, session_dates, session_turns, strict=True
        )
    ]
    answer_session_ids = [str(source_id) for source_id in record.get("answer_session_ids", [])]
    question = NormalizedQuestion(
        question_id=sample_id,
        question=_require_str(record, "question"),
        answer=_optional_str(record.get("answer")),
        category=_optional_str(record.get("question_type")),
        asked_at=_parse_longmemeval_datetime(_require_str(record, "question_date")),
        evidence=[
            NormalizedEvidenceRef(
                source_type="longmemeval_session",
                source_id=source_id,
                locator="answer_session_ids",
            )
            for source_id in answer_session_ids
        ],
    )
    return NormalizedRecord(
        dataset="longmemeval",
        sample_id=sample_id,
        sessions=sessions,
        questions=[question],
    )


def _normalize_longmemeval_session(
    session_id_value: Any, session_date: Any, turns_value: Any
) -> NormalizedSession:
    session_id = str(session_id_value)
    turns = _require_list_value(turns_value, "haystack_sessions item")
    timestamp = _parse_longmemeval_datetime(str(session_date))
    return NormalizedSession(
        session_id=session_id,
        timestamp=timestamp,
        turns=[
            NormalizedTurn(
                turn_id=f"{session_id}:{index}",
                speaker=_require_str(turn, "role"),
                content=_require_str(turn, "content"),
                timestamp=timestamp,
                metadata={"has_answer": True}
                if _require_mapping(turn).get("has_answer") is True
                else {},
            )
            for index, turn in enumerate(turns)
        ],
    )


def _normalize_locomo_sample(sample: Any) -> NormalizedRecord:
    record = _require_mapping(sample)
    sample_id = _require_str(record, "sample_id")
    conversation = _require_mapping(record["conversation"])
    sessions = _normalize_locomo_sessions(conversation)
    questions = [
        _normalize_locomo_question(sample_id, index, qa)
        for index, qa in enumerate(_require_list(record, "qa"))
    ]
    return NormalizedRecord(
        dataset="locomo",
        sample_id=sample_id,
        sessions=sessions,
        questions=questions,
        event_summaries=_normalize_locomo_event_summaries(record.get("event_summary", {})),
        metadata={
            "speaker_a": _optional_str(conversation.get("speaker_a")),
            "speaker_b": _optional_str(conversation.get("speaker_b")),
            "session_summary": record.get("session_summary", {}),
            "observation": record.get("observation", {}),
        },
    )


def _normalize_locomo_sessions(conversation: Mapping[str, Any]) -> list[NormalizedSession]:
    sessions: list[NormalizedSession] = []
    for key in sorted(conversation, key=_session_sort_key):
        if not (key.startswith("session_") and isinstance(conversation[key], list)):
            continue
        timestamp = _parse_locomo_datetime(_require_str(conversation, f"{key}_date_time"))
        sessions.append(
            NormalizedSession(
                session_id=key,
                timestamp=timestamp,
                turns=[
                    _normalize_locomo_turn(turn, timestamp)
                    for turn in _require_list_value(conversation[key], key)
                ],
            )
        )
    if not sessions:
        raise ValueError("conversation must contain at least one session")
    return sessions


def _normalize_locomo_turn(turn_value: Any, timestamp: datetime) -> NormalizedTurn:
    turn = _require_mapping(turn_value)
    text = _require_str(turn, "text")
    metadata: dict[str, Any] = {}
    for key in ("img_url", "blip_caption", "query"):
        if key in turn:
            metadata[key] = turn[key]
    return NormalizedTurn(
        turn_id=_require_str(turn, "dia_id"),
        speaker=_require_str(turn, "speaker"),
        content=text,
        timestamp=timestamp,
        metadata=metadata,
    )


def _normalize_locomo_question(sample_id: str, index: int, qa_value: Any) -> NormalizedQuestion:
    qa = _require_mapping(qa_value)
    evidence_values = _require_list(qa, "evidence") if "evidence" in qa else []
    evidence = [
        NormalizedEvidenceRef(
            source_type="locomo_dialogue",
            source_id=str(source_id),
            locator="qa.evidence",
        )
        for source_id in evidence_values
    ]
    return NormalizedQuestion(
        question_id=f"{sample_id}:qa:{index}",
        question=_require_str(qa, "question"),
        answer=_optional_str(qa.get("answer")),
        category=_optional_str(qa.get("category")),
        evidence=evidence,
        metadata={"adversarial_answer": qa["adversarial_answer"]}
        if "adversarial_answer" in qa
        else {},
    )


def _normalize_locomo_event_summaries(value: Any) -> list[NormalizedEventSummary]:
    event_summary = _require_mapping(value)
    summaries: list[NormalizedEventSummary] = []
    for key in sorted(event_summary, key=_session_sort_key):
        if not key.startswith("events_session_"):
            continue
        session_id = key.replace("events_", "", 1)
        raw_events = _require_mapping(event_summary[key])
        date_text = _optional_str(raw_events.get("date"))
        events = {
            str(speaker): [str(event) for event in events]
            for speaker, events in raw_events.items()
            if speaker != "date" and isinstance(events, list)
        }
        summaries.append(
            NormalizedEventSummary(
                session_id=session_id,
                date=_parse_locomo_event_date(date_text) if date_text else None,
                events=events,
            )
        )
    return summaries


def _iter_json_array(path: Path, chunk_size: int = 65536) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    with path.open(encoding="utf-8") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk and not buffer.strip():
                break
            buffer += chunk
            while True:
                buffer = buffer.lstrip()
                if not started:
                    if not buffer:
                        break
                    if buffer[0] != "[":
                        raise NormalizationError(f"{path}: expected top-level JSON array")
                    started = True
                    buffer = buffer[1:]
                    continue
                if buffer.startswith("]"):
                    return
                if buffer.startswith(","):
                    buffer = buffer[1:].lstrip()
                if not buffer:
                    break
                try:
                    item, offset = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    if not chunk:
                        raise
                    break
                yield item
                buffer = buffer[offset:]
            if not chunk:
                if buffer.strip() and buffer.strip() != "]":
                    raise NormalizationError(f"{path}: trailing malformed JSON")
                break


def _parse_longmemeval_datetime(value: str) -> datetime:
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d (%a) %H:%M",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    raise ValueError(f"unsupported LongMemEval datetime: {value}")


def _parse_locomo_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%I:%M %p on %d %B, %Y").replace(tzinfo=UTC)


def _parse_locomo_event_date(value: str) -> datetime:
    return datetime.strptime(value, "%d %B, %Y").replace(tzinfo=UTC)


def _session_sort_key(value: str) -> tuple[str, int, str]:
    for prefix in ("events_session_", "session_"):
        if value.startswith(prefix):
            suffix = value.removeprefix(prefix)
            if suffix.isdigit():
                return (prefix, int(suffix), value)
    return ("", 0, value)


def _diagnostic_sample_id(sample: Any, index: int) -> str:
    if isinstance(sample, Mapping):
        sample_id = sample.get("sample_id")
        if sample_id is not None:
            return str(sample_id)
    return f"index {index}"


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("expected object")
    return value


def _require_list(record: Mapping[str, Any], key: str) -> list[Any]:
    return _require_list_value(record[key], key)


def _require_list_value(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a list")
    return value


def _require_str(record: Mapping[str, Any], key: str) -> str:
    value = record[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
