from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError, field_validator

from evoeventmem.core.ports import ChatMessage, ChatModel
from evoeventmem.domain.models import EntityRef, EvidenceRef, MemoryKind, MemoryRecord


class ExtractionTurn(BaseModel):
    turn_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    speaker: str = Field(min_length=1)
    content: str = Field(min_length=1)
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionEventSummary(BaseModel):
    session_id: str = Field(min_length=1)
    date: datetime | None = None
    events: dict[str, list[str]] = Field(default_factory=dict)


class ExtractionObservation(BaseModel):
    content: str = Field(min_length=1)
    observation_id: str | None = None
    index: int = Field(ge=0)
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _coerce_observation_sequence(value: object) -> object:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return value
    coerced: list[object] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            coerced.append({"content": item, "index": index})
        elif isinstance(item, Mapping) and "index" not in item:
            coerced.append({**item, "index": index})
        else:
            coerced.append(item)
    return coerced


def _observations_from_metadata(
    metadata: Mapping[str, object],
) -> list[ExtractionObservation]:
    raw_mapping = metadata.get("observation")
    if isinstance(raw_mapping, Mapping) and raw_mapping:
        observations: list[ExtractionObservation] = []
        for raw_session_key, raw_speakers in sorted(
            raw_mapping.items(), key=lambda item: str(item[0])
        ):
            if not isinstance(raw_speakers, Mapping):
                continue
            session_key = str(raw_session_key)
            session_id = session_key.removesuffix("_observation")
            local_index = 0
            for raw_speaker, raw_items in sorted(
                raw_speakers.items(), key=lambda item: str(item[0])
            ):
                if not isinstance(raw_items, Sequence) or isinstance(
                    raw_items, (str, bytes)
                ):
                    continue
                for raw_item in raw_items:
                    observation_id: str | None = None
                    if isinstance(raw_item, str):
                        content = raw_item
                    elif isinstance(raw_item, Sequence) and not isinstance(
                        raw_item, (str, bytes)
                    ) and raw_item:
                        content = str(raw_item[0])
                        if len(raw_item) > 1 and raw_item[1] is not None:
                            observation_id = str(raw_item[1])
                    else:
                        continue
                    observations.append(
                        ExtractionObservation(
                            content=content,
                            observation_id=observation_id,
                            index=local_index,
                            session_id=session_id,
                            metadata={"speaker": str(raw_speaker)},
                        )
                    )
                    local_index += 1
        return observations

    raw_sequence = raw_mapping
    if not isinstance(raw_sequence, Sequence) or isinstance(
        raw_sequence, (str, bytes)
    ):
        raw_sequence = metadata.get("observations", [])
    coerced = _coerce_observation_sequence(raw_sequence)
    if not isinstance(coerced, Sequence) or isinstance(coerced, (str, bytes)):
        return []
    return [ExtractionObservation.model_validate(item) for item in coerced]


class ExtractionInput(BaseModel):
    schema_version: Literal["event-extraction-input.v1"] = "event-extraction-input.v1"
    user_id: str = Field(min_length=1)
    tenant_id: str | None = None
    sample_id: str | None = None
    dataset: str | None = None
    turns: list[ExtractionTurn] = Field(default_factory=list)
    event_summaries: list[ExtractionEventSummary] = Field(default_factory=list)
    observations: list[ExtractionObservation] = Field(default_factory=list)

    @field_validator("observations", mode="before")
    @classmethod
    def coerce_observations(cls, value: object) -> object:
        return _coerce_observation_sequence(value)

    @classmethod
    def from_normalized_record(cls, record: Any, *, user_id: str) -> ExtractionInput:
        turns: list[ExtractionTurn] = []
        for session in record.sessions:
            session_id = str(session.session_id)
            for turn in session.turns:
                turns.append(
                    ExtractionTurn(
                        turn_id=str(turn.turn_id),
                        session_id=session_id,
                        speaker=str(turn.speaker),
                        content=str(turn.content),
                        timestamp=cast(datetime | None, turn.timestamp),
                        metadata=dict(turn.metadata),
                    )
                )

        event_summaries = [
            ExtractionEventSummary(
                session_id=str(summary.session_id),
                date=cast(datetime | None, summary.date),
                events={
                    str(speaker): [str(event) for event in events]
                    for speaker, events in cast(
                        Mapping[str, Sequence[object]],
                        summary.events,
                    ).items()
                },
            )
            for summary in getattr(record, "event_summaries", [])
        ]
        metadata = cast(Mapping[str, object], getattr(record, "metadata", {}))
        return cls(
            user_id=user_id,
            sample_id=str(getattr(record, "sample_id", "")) or None,
            dataset=str(getattr(record, "dataset", "")) or None,
            turns=turns,
            event_summaries=event_summaries,
            observations=_observations_from_metadata(metadata),
        )


class ExtractedEventCandidate(BaseModel):
    memory: MemoryRecord
    prompt_version: str


class ExtractionResult(BaseModel):
    prompt_version: str
    candidates: list[ExtractedEventCandidate]
    raw_output: str | None = None
    model_id: str | None = None
    cache_key: str | None = None


class EvidenceReferenceError(BaseModel):
    code: Literal[
        "unknown_turn_id",
        "ambiguous_turn_id",
        "invalid_span",
        "quote_mismatch",
    ]
    event_index: int
    source_turn_id: str
    source_session_id: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    quote: str | None = None
    message: str


class EvidenceValidationError(ValueError):
    def __init__(self, errors: Sequence[EvidenceReferenceError]) -> None:
        self.errors = tuple(errors)
        message = "; ".join(error.message for error in self.errors)
        super().__init__(message)


class _EvidenceDraft(BaseModel):
    source_turn_id: str = Field(min_length=1)
    source_session_id: str | None = None
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str = Field(min_length=1)


class _EventDraft(BaseModel):
    content: str = Field(min_length=1)
    speaker: str | None = None
    entities: list[str] = Field(default_factory=list)
    event_time: datetime | None = None
    evidence: list[_EvidenceDraft] = Field(min_length=1)

    @field_validator("event_time", mode="before")
    @classmethod
    def parse_event_time(cls, value: object) -> object:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class _LLMExtractionPayload(BaseModel):
    events: list[_EventDraft] = Field(default_factory=list)


def _evidence_scope(request: ExtractionInput, *parts: str) -> str:
    scope_parts: list[str] = []
    if request.dataset is not None:
        scope_parts.extend(("dataset", request.dataset))
    if request.sample_id is not None:
        scope_parts.extend(("sample", request.sample_id))
    scope_parts.extend(parts)
    return "/".join(
        f"{quote(key, safe='')}={quote(value, safe='')}"
        for key, value in zip(scope_parts[::2], scope_parts[1::2], strict=True)
    )


def _evidence_metadata(
    request: ExtractionInput,
    *,
    session_id: str | None,
) -> dict[str, Any]:
    return {
        "dataset": request.dataset,
        "sample_id": request.sample_id,
        "session_id": session_id,
    }


def _summary_evidence(
    request: ExtractionInput,
    *,
    summary: ExtractionEventSummary,
    summary_index: int,
    speaker: str,
    event_index: int,
    event: str,
) -> EvidenceRef:
    return EvidenceRef(
        source_type="event_summary",
        source_id=_evidence_scope(
            request,
            "session",
            summary.session_id,
            "event_summary",
            str(summary_index),
            "speaker",
            speaker,
            "event",
            str(event_index),
        ),
        locator=f"events[{speaker}][{event_index}]",
        quote=event,
        metadata={
            **_evidence_metadata(request, session_id=summary.session_id),
            "raw_summary_id": str(summary_index),
            "raw_event_id": str(event_index),
            "speaker": speaker,
        },
    )


def _turn_evidence(
    request: ExtractionInput,
    turn: ExtractionTurn,
    *,
    start_char: int = 0,
    end_char: int | None = None,
) -> EvidenceRef:
    span_end = len(turn.content) if end_char is None else end_char
    return EvidenceRef(
        source_type="turn",
        source_id=_evidence_scope(
            request,
            "session",
            turn.session_id,
            "turn",
            turn.turn_id,
        ),
        locator=f"chars={start_char}:{span_end}",
        quote=turn.content[start_char:span_end],
        metadata={
            **_evidence_metadata(request, session_id=turn.session_id),
            "raw_turn_id": turn.turn_id,
            "speaker": turn.speaker,
        },
    )


def _observation_evidence(
    request: ExtractionInput,
    observation: ExtractionObservation,
) -> EvidenceRef:
    raw_observation_id = observation.observation_id or str(observation.index)
    scope_parts: list[str] = []
    if observation.session_id is not None:
        scope_parts.extend(("session", observation.session_id))
    scope_parts.extend(("observation", raw_observation_id))
    if observation.observation_id is not None:
        scope_parts.extend(("index", str(observation.index)))
    metadata: dict[str, Any] = {
        **observation.metadata,
        **_evidence_metadata(request, session_id=observation.session_id),
        "raw_observation_id": raw_observation_id,
    }
    if observation.observation_id is not None:
        metadata["raw_observation_index"] = observation.index
    locator = (
        f"observations[{observation.session_id}][{observation.index}]"
        if observation.session_id is not None
        else f"observations[{observation.index}]"
    )
    return EvidenceRef(
        source_type="observation",
        source_id=_evidence_scope(request, *scope_parts),
        locator=locator,
        quote=observation.content,
        metadata=metadata,
    )


def _normalized_text_with_positions(text: str) -> tuple[str, list[int], list[int]]:
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for token_index, match in enumerate(re.finditer(r"\S+", text)):
        if token_index:
            normalized.append(" ")
            starts.append(match.start() - 1)
            ends.append(match.start())
        for offset, character in enumerate(match.group()):
            for folded_character in character.casefold():
                normalized.append(folded_character)
                starts.append(match.start() + offset)
                ends.append(match.start() + offset + 1)
    return "".join(normalized), starts, ends


def _exact_normalized_span(needle: str, haystack: str) -> tuple[int, int] | None:
    normalized_needle, _, _ = _normalized_text_with_positions(needle)
    normalized_haystack, starts, ends = _normalized_text_with_positions(haystack)
    if not normalized_needle:
        return None

    search_from = 0
    while True:
        normalized_start = normalized_haystack.find(normalized_needle, search_from)
        if normalized_start < 0:
            return None
        normalized_end = normalized_start + len(normalized_needle)
        starts_inside_expansion = (
            normalized_start > 0
            and starts[normalized_start] == starts[normalized_start - 1]
            and ends[normalized_start] == ends[normalized_start - 1]
        )
        ends_inside_expansion = (
            normalized_end < len(normalized_haystack)
            and starts[normalized_end - 1] == starts[normalized_end]
            and ends[normalized_end - 1] == ends[normalized_end]
        )
        start_char = starts[normalized_start]
        end_char = ends[normalized_end - 1]
        normalized_quote, _, _ = _normalized_text_with_positions(
            haystack[start_char:end_char]
        )
        if (
            not starts_inside_expansion
            and not ends_inside_expansion
            and normalized_quote == normalized_needle
        ):
            return start_char, end_char
        search_from = normalized_start + 1


def _deduplicate_candidates(
    candidates: Sequence[ExtractedEventCandidate],
) -> list[ExtractedEventCandidate]:
    unique: list[ExtractedEventCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = json.dumps(
            candidate.memory.model_dump(
                mode="json",
                exclude={"memory_id", "created_at", "updated_at"},
            ),
            sort_keys=True,
            ensure_ascii=False,
        )
        if identity not in seen:
            seen.add(identity)
            unique.append(candidate)
    return unique


class RuleEventExtractor:
    PROMPT_VERSION = "rule.v1"

    def extract(self, request: ExtractionInput) -> ExtractionResult:
        candidates: list[ExtractedEventCandidate] = []
        for summary_index, summary in enumerate(request.event_summaries):
            session_turns = [
                turn for turn in request.turns if turn.session_id == summary.session_id
            ]
            for speaker, events in summary.events.items():
                for event_index, event in enumerate(events):
                    if not event.strip():
                        continue
                    evidence_refs = [
                        _summary_evidence(
                            request,
                            summary=summary,
                            summary_index=summary_index,
                            speaker=speaker,
                            event_index=event_index,
                            event=event,
                        )
                    ]
                    ordered_turns = [
                        turn for turn in session_turns if turn.speaker == speaker
                    ] + [turn for turn in session_turns if turn.speaker != speaker]
                    for turn in ordered_turns:
                        span = _exact_normalized_span(event, turn.content)
                        if span is not None:
                            evidence_refs.append(
                                _turn_evidence(
                                    request,
                                    turn,
                                    start_char=span[0],
                                    end_char=span[1],
                                )
                            )
                            break
                    candidates.append(
                        ExtractedEventCandidate(
                            memory=_build_memory(
                                request=request,
                                content=event,
                                speaker=speaker,
                                entity_names=[speaker],
                                evidence_refs=evidence_refs,
                                event_time=_parse_event_time(event) or summary.date,
                                session_id=summary.session_id,
                                prompt_version=self.PROMPT_VERSION,
                            ),
                            prompt_version=self.PROMPT_VERSION,
                        )
                    )

        if not candidates:
            for turn in request.turns:
                if not turn.content.strip():
                    continue
                candidates.append(
                    ExtractedEventCandidate(
                        memory=_build_memory(
                            request=request,
                            content=turn.content,
                            speaker=turn.speaker,
                            entity_names=[turn.speaker],
                            evidence_refs=[_turn_evidence(request, turn)],
                            event_time=_parse_event_time(turn.content) or turn.timestamp,
                            session_id=turn.session_id,
                            prompt_version=self.PROMPT_VERSION,
                        ),
                        prompt_version=self.PROMPT_VERSION,
                    )
                )

        for observation in request.observations:
            if not observation.content.strip():
                continue
            candidates.append(
                ExtractedEventCandidate(
                    memory=_build_memory(
                        request=request,
                        content=observation.content,
                        speaker=None,
                        entity_names=[],
                        evidence_refs=[_observation_evidence(request, observation)],
                        event_time=_parse_event_time(observation.content),
                        session_id=observation.session_id,
                        prompt_version=self.PROMPT_VERSION,
                    ),
                    prompt_version=self.PROMPT_VERSION,
                )
            )

        return ExtractionResult(
            prompt_version=self.PROMPT_VERSION,
            candidates=_deduplicate_candidates(candidates),
        )


class LLMEventExtractor:
    """Extract events through a caller-provided chat model gateway.

    Pass a ``CachedChatModel`` when extraction requests and raw outputs must be
    cached. This extractor deliberately does not add a second caching layer.
    """

    PROMPT_VERSION = "event-extraction.v1"

    def __init__(self, model: ChatModel) -> None:
        self._model = model

    def extract(self, request: ExtractionInput) -> ExtractionResult:
        messages = [
            ChatMessage(role="system", content=self.PROMPT_VERSION),
            ChatMessage(role="user", content=_build_llm_prompt(request)),
        ]
        response = self._model.generate(messages)
        try:
            payload = _LLMExtractionPayload.model_validate_json(response.text)
        except ValidationError as exc:
            raise ValueError("LLM extractor returned invalid event JSON") from exc

        candidates: list[ExtractedEventCandidate] = []
        for event_index, draft in enumerate(payload.events):
            evidence_refs = _validate_evidence(request, event_index, draft.evidence)
            session_id = _session_id_for_evidence(evidence_refs)
            speaker = draft.speaker
            candidates.append(
                ExtractedEventCandidate(
                    memory=_build_memory(
                        request=request,
                        content=draft.content,
                        speaker=speaker,
                        entity_names=draft.entities,
                        evidence_refs=evidence_refs,
                        event_time=draft.event_time,
                        session_id=session_id,
                        prompt_version=self.PROMPT_VERSION,
                    ),
                    prompt_version=self.PROMPT_VERSION,
                )
            )
        return ExtractionResult(
            prompt_version=self.PROMPT_VERSION,
            candidates=_deduplicate_candidates(candidates),
            raw_output=response.text,
            model_id=response.model_id,
            cache_key=response.cache_key,
        )


def _build_llm_prompt(request: ExtractionInput) -> str:
    payload = {
        "schema": {
            "events": [
                {
                    "content": "string",
                    "speaker": "string or null",
                    "entities": ["entity names"],
                    "event_time": "ISO-8601 string or null",
                    "evidence": [
                        {
                            "source_turn_id": "existing turn_id",
                            "source_session_id": (
                                "existing session_id or null when turn_id is unique"
                            ),
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "exact substring",
                        }
                    ],
                }
            ]
        },
        "constraints": [
            "Return only JSON.",
            "Every evidence reference must use one of the provided turn_id values.",
            "Provide source_session_id whenever duplicate turn_id values exist.",
            "Every quote must exactly match content[start_char:end_char].",
        ],
        "sample_id": request.sample_id,
        "turns": [
            {
                "turn_id": turn.turn_id,
                "session_id": turn.session_id,
                "speaker": turn.speaker,
                "timestamp": turn.timestamp.isoformat() if turn.timestamp else None,
                "content": turn.content,
            }
            for turn in request.turns
        ],
        "observations": [
            observation.model_dump(mode="json") for observation in request.observations
        ],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _validate_evidence(
    request: ExtractionInput,
    event_index: int,
    evidence: Sequence[_EvidenceDraft],
) -> list[EvidenceRef]:
    turns_by_id: dict[str, list[ExtractionTurn]] = {}
    for turn in request.turns:
        turns_by_id.setdefault(turn.turn_id, []).append(turn)

    errors: list[EvidenceReferenceError] = []
    refs: list[EvidenceRef] = []
    for draft in evidence:
        matching_turns = turns_by_id.get(draft.source_turn_id, [])
        if draft.source_session_id is not None:
            matching_turns = [
                turn
                for turn in matching_turns
                if turn.session_id == draft.source_session_id
            ]
        if not matching_turns:
            errors.append(
                EvidenceReferenceError(
                    code="unknown_turn_id",
                    event_index=event_index,
                    source_turn_id=draft.source_turn_id,
                    source_session_id=draft.source_session_id,
                    start_char=draft.start_char,
                    end_char=draft.end_char,
                    quote=draft.quote,
                    message=(
                        f"event {event_index} references unknown turn_id "
                        f"{draft.source_turn_id}"
                        + (
                            f" in session {draft.source_session_id}"
                            if draft.source_session_id is not None
                            else ""
                        )
                    ),
                )
            )
            continue
        if len(matching_turns) > 1:
            errors.append(
                EvidenceReferenceError(
                    code="ambiguous_turn_id",
                    event_index=event_index,
                    source_turn_id=draft.source_turn_id,
                    source_session_id=draft.source_session_id,
                    start_char=draft.start_char,
                    end_char=draft.end_char,
                    quote=draft.quote,
                    message=(
                        f"event {event_index} references ambiguous turn_id "
                        f"{draft.source_turn_id}; source_session_id is required"
                    ),
                )
            )
            continue
        turn = matching_turns[0]
        if draft.start_char >= draft.end_char or draft.end_char > len(turn.content):
            errors.append(
                EvidenceReferenceError(
                    code="invalid_span",
                    event_index=event_index,
                    source_turn_id=draft.source_turn_id,
                    source_session_id=draft.source_session_id,
                    start_char=draft.start_char,
                    end_char=draft.end_char,
                    quote=draft.quote,
                    message=f"event {event_index} has invalid span for {draft.source_turn_id}",
                )
            )
            continue
        actual_quote = turn.content[draft.start_char : draft.end_char]
        if actual_quote != draft.quote:
            errors.append(
                EvidenceReferenceError(
                    code="quote_mismatch",
                    event_index=event_index,
                    source_turn_id=draft.source_turn_id,
                    source_session_id=draft.source_session_id,
                    start_char=draft.start_char,
                    end_char=draft.end_char,
                    quote=draft.quote,
                    message=(
                        f"event {event_index} quote does not match span for "
                        f"{draft.source_turn_id}"
                    ),
                )
            )
            continue
        refs.append(
            _turn_evidence(
                request,
                turn,
                start_char=draft.start_char,
                end_char=draft.end_char,
            )
        )
    if errors:
        raise EvidenceValidationError(errors)
    return refs


def _build_memory(
    *,
    request: ExtractionInput,
    content: str,
    speaker: str | None,
    entity_names: Sequence[str],
    evidence_refs: Sequence[EvidenceRef],
    event_time: datetime | None,
    session_id: str | None,
    prompt_version: str,
) -> MemoryRecord:
    entities = _entities(entity_names, speaker)
    roles = {speaker: "speaker"} if speaker else {}
    return MemoryRecord(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        session_id=session_id,
        memory_kind=MemoryKind.EVENT,
        content=content,
        entities=entities,
        roles=roles,
        evidence_refs=list(evidence_refs),
        event_time=event_time,
        metadata={
            "extractor_prompt_version": prompt_version,
            "source_dataset": request.dataset,
            "source_sample_id": request.sample_id,
        },
    )


def _entities(entity_names: Sequence[str], speaker: str | None) -> list[EntityRef]:
    names: list[str] = []
    for name in [*(entity_names or []), *([speaker] if speaker else [])]:
        normalized = name.strip()
        if normalized and normalized not in names:
            names.append(normalized)
    return [
        EntityRef(name=name, role="speaker" if speaker and name == speaker else None)
        for name in names
    ]


def _session_id_for_evidence(
    evidence_refs: Sequence[EvidenceRef],
) -> str | None:
    if not evidence_refs:
        return None
    session_id = evidence_refs[0].metadata.get("session_id")
    return str(session_id) if session_id is not None else None


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_DAY_MONTH_YEAR = re.compile(
    r"\b(?P<day>\d{1,2}) (?P<month>[A-Za-z]+),? (?P<year>\d{4})\b"
)
_MONTH_DAY_YEAR = re.compile(
    r"\b(?P<month>[A-Za-z]+) (?P<day>\d{1,2}), (?P<year>\d{4})\b"
)


def _parse_event_time(text: str) -> datetime | None:
    for pattern in (_DAY_MONTH_YEAR, _MONTH_DAY_YEAR):
        match = pattern.search(text)
        if match is None:
            continue
        month = _MONTHS.get(match.group("month").lower())
        if month is None:
            continue
        return datetime(
            int(match.group("year")),
            month,
            int(match.group("day")),
            tzinfo=UTC,
        )
    return None


__all__ = [
    "EvidenceReferenceError",
    "EvidenceValidationError",
    "ExtractedEventCandidate",
    "ExtractionEventSummary",
    "ExtractionInput",
    "ExtractionObservation",
    "ExtractionResult",
    "ExtractionTurn",
    "LLMEventExtractor",
    "RuleEventExtractor",
]
