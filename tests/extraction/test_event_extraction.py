from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks.common.normalization import iter_locomo_records
from evoeventmem.core.ports import ChatMessage, ChatResponse
from evoeventmem.domain.models import MemoryKind
from evoeventmem.extraction import (
    EvidenceValidationError,
    ExtractionEventSummary,
    ExtractionInput,
    ExtractionTurn,
    LLMEventExtractor,
    RuleEventExtractor,
)
from evoeventmem.models.cache import CachedChatModel, FileModelCache

FIXTURES = Path("tests/fixtures")


class StaticJSONChatModel:
    model_id = "static-json"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        prompt = "\n".join(message.content for message in messages)
        return ChatResponse(
            text=json.dumps(self.payload),
            model_id=self.model_id,
            input_tokens=len(prompt.split()),
            output_tokens=10,
        )


def test_rule_extractor_preserves_fixture_speaker_entity_time_and_evidence() -> None:
    record = next(iter_locomo_records(FIXTURES / "locomo/locomo_tiny.json"))
    request = ExtractionInput.from_normalized_record(record, user_id="u1")

    result = RuleEventExtractor().extract(request)

    memory = result.candidates[0].memory
    assert memory.memory_kind is MemoryKind.EVENT
    assert memory.content == "Caroline went to an LGBTQ support group on 7 May 2023."
    assert memory.entities[0].name == "Caroline"
    assert memory.entities[0].role == "speaker"
    assert memory.roles == {"Caroline": "speaker"}
    assert memory.event_time == datetime(2023, 5, 7, tzinfo=UTC)
    assert memory.valid_from is None
    assert memory.valid_to is None
    assert len(memory.evidence_refs) == 1

    evidence = memory.evidence_refs[0]
    assert evidence.source_type == "event_summary"
    assert evidence.source_id == (
        "dataset=locomo/sample=conv-tiny/session=session_1/"
        "event_summary=0/speaker=Caroline/event=0"
    )
    assert evidence.locator == "events[Caroline][0]"
    assert evidence.quote == memory.content
    assert evidence.metadata == {
        "dataset": "locomo",
        "sample_id": "conv-tiny",
        "session_id": "session_1",
        "raw_summary_id": "0",
        "raw_event_id": "0",
        "speaker": "Caroline",
    }
    assert result.prompt_version == "rule.v1"


def test_llm_extractor_rejects_hallucinated_evidence_with_structured_error() -> None:
    record = next(iter_locomo_records(FIXTURES / "locomo/locomo_tiny.json"))
    request = ExtractionInput.from_normalized_record(record, user_id="u1")
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Caroline went to a support group.",
                    "speaker": "Caroline",
                    "entities": ["Caroline"],
                    "event_time": "2023-05-07T00:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "D1:99",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "not real",
                        }
                    ],
                }
            ]
        }
    )

    with pytest.raises(EvidenceValidationError) as exc_info:
        LLMEventExtractor(model).extract(request)

    error = exc_info.value.errors[0]
    assert error.code == "unknown_turn_id"
    assert error.source_turn_id == "D1:99"
    assert error.event_index == 0


def test_llm_extractor_caches_requests_and_raw_outputs(tmp_path: Path) -> None:
    record = next(iter_locomo_records(FIXTURES / "locomo/locomo_tiny.json"))
    request = ExtractionInput.from_normalized_record(record, user_id="u1")
    wrapped = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Caroline went to an LGBTQ support group on 7 May 2023.",
                    "speaker": "Caroline",
                    "entities": ["Caroline"],
                    "event_time": "2023-05-07T00:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "D1:1",
                            "start_char": 0,
                            "end_char": 43,
                            "quote": "I went to an LGBTQ support group yesterday.",
                        }
                    ],
                }
            ]
        }
    )
    model = CachedChatModel(wrapped, FileModelCache(tmp_path))
    extractor = LLMEventExtractor(model)

    first = extractor.extract(request)
    second = extractor.extract(request)

    assert first.candidates[0].memory.content == second.candidates[0].memory.content
    assert first.raw_output == second.raw_output
    assert wrapped.calls == 1
    assert first.cache_key is not None
    cache_files = sorted(tmp_path.glob("chat/*.json"))
    assert len(cache_files) == 1
    entry = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert entry["input"]["messages"][0]["content"] == LLMEventExtractor.PROMPT_VERSION
    assert entry["output"]["text"] == json.dumps(wrapped.payload)


def test_rule_extractor_adds_turn_evidence_only_for_exact_normalized_span() -> None:
    request = ExtractionInput(
        user_id="u1",
        dataset="dataset-a",
        sample_id="sample-a",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="  alice   shipped the release.  ",
            )
        ],
        event_summaries=[
            ExtractionEventSummary(
                session_id="session-1",
                events={"Alice": ["Alice shipped the release."]},
            )
        ],
    )

    memory = RuleEventExtractor().extract(request).candidates[0].memory

    assert [evidence.source_type for evidence in memory.evidence_refs] == [
        "event_summary",
        "turn",
    ]
    turn_evidence = memory.evidence_refs[1]
    assert turn_evidence.source_id == (
        "dataset=dataset-a/sample=sample-a/session=session-1/turn=turn-1"
    )
    assert turn_evidence.locator == "chars=2:30"
    assert turn_evidence.quote == "alice   shipped the release."
    assert turn_evidence.metadata == {
        "dataset": "dataset-a",
        "sample_id": "sample-a",
        "session_id": "session-1",
        "raw_turn_id": "turn-1",
        "speaker": "Alice",
    }


def test_rule_extractor_falls_back_to_exact_turn_candidates() -> None:
    timestamp = datetime(2025, 2, 3, 4, 5, tzinfo=UTC)
    request = ExtractionInput(
        user_id="u1",
        dataset="dataset-a",
        sample_id="sample-a",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice shipped the release.",
                timestamp=timestamp,
            )
        ],
    )

    result = RuleEventExtractor().extract(request)

    assert len(result.candidates) == 1
    memory = result.candidates[0].memory
    assert memory.content == "Alice shipped the release."
    assert memory.event_time == timestamp
    assert memory.valid_from is None
    assert memory.valid_to is None
    assert memory.evidence_refs[0].source_id == (
        "dataset=dataset-a/sample=sample-a/session=session-1/turn=turn-1"
    )
    assert memory.evidence_refs[0].quote == memory.content


def test_rule_extractor_creates_deterministic_exact_observation_candidates() -> None:
    request = ExtractionInput(
        user_id="u1",
        dataset="dataset-a",
        sample_id="sample-a",
        observations=["Alice deployed version 2 on 3 February 2025."],
    )
    extractor = RuleEventExtractor()

    first = extractor.extract(request)
    second = extractor.extract(request)

    assert len(first.candidates) == 1
    evidence = first.candidates[0].memory.evidence_refs[0]
    assert evidence.source_type == "observation"
    assert evidence.source_id == "dataset=dataset-a/sample=sample-a/observation=0"
    assert evidence.locator == "observations[0]"
    assert evidence.quote == "Alice deployed version 2 on 3 February 2025."
    assert evidence.metadata == {
        "dataset": "dataset-a",
        "sample_id": "sample-a",
        "session_id": None,
        "raw_observation_id": "0",
    }
    assert second.candidates[0].memory.evidence_refs[0] == evidence


def test_rule_extractor_keeps_observations_distinct_from_summary_candidates() -> None:
    request = ExtractionInput(
        user_id="u1",
        dataset="dataset-a",
        sample_id="sample-a",
        event_summaries=[
            ExtractionEventSummary(
                session_id="session-1",
                events={"Alice": ["Alice shipped the release."]},
            )
        ],
        observations=["Alice monitored the rollout."],
    )

    result = RuleEventExtractor().extract(request)

    assert [candidate.memory.content for candidate in result.candidates] == [
        "Alice shipped the release.",
        "Alice monitored the rollout.",
    ]
    assert [
        candidate.memory.evidence_refs[0].source_type
        for candidate in result.candidates
    ] == ["event_summary", "observation"]


def test_identical_raw_turn_ids_in_different_samples_have_distinct_evidence_ids() -> None:
    def extract_source_id(sample_id: str) -> str:
        request = ExtractionInput(
            user_id="u1",
            dataset="dataset-a",
            sample_id=sample_id,
            turns=[
                ExtractionTurn(
                    turn_id="turn-1",
                    session_id="session-1",
                    speaker="Alice",
                    content="Alice shipped the release.",
                )
            ],
        )
        result = RuleEventExtractor().extract(request)
        return result.candidates[0].memory.evidence_refs[0].source_id

    assert extract_source_id("sample-a") != extract_source_id("sample-b")


def test_rule_extractor_deduplicates_exact_turn_candidates() -> None:
    turn = ExtractionTurn(
        turn_id="turn-1",
        session_id="session-1",
        speaker="Alice",
        content="Alice shipped the release.",
    )
    request = ExtractionInput(
        user_id="u1",
        dataset="dataset-a",
        sample_id="sample-a",
        turns=[turn, turn.model_copy(deep=True)],
    )

    result = RuleEventExtractor().extract(request)

    assert len(result.candidates) == 1


def test_llm_extractor_documents_cached_model_gateway_requirement() -> None:
    model = StaticJSONChatModel({"events": []})
    extractor = LLMEventExtractor(model)

    first = extractor.extract(ExtractionInput(user_id="u1"))
    second = extractor.extract(ExtractionInput(user_id="u1"))
    documentation = inspect.getdoc(LLMEventExtractor)

    assert first.cache_key is None
    assert second.cache_key is None
    assert model.calls == 2
    assert documentation is not None
    assert "CachedChatModel" in documentation
