from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.common.normalization import iter_locomo_records
from evoeventmem.core.ports import ChatMessage, ChatResponse
from evoeventmem.domain.models import MemoryKind
from evoeventmem.extraction import (
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
        self.requests: list[list[ChatMessage]] = []

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        self.requests.append(list(messages))
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

    result = LLMEventExtractor(model).extract(request)

    assert result.candidates == []
    assert len(result.rejections) == 1
    error = result.rejections[0]
    assert error.code == "unknown_turn_id"
    assert error.source_turn_id == "D1:99"
    assert error.event_index == 0


def test_llm_extractor_rejects_ambiguous_local_turn_id_without_session() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-a",
                speaker="Alice",
                content="same fact",
            ),
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-b",
                speaker="Alice",
                content="same fact",
            ),
        ],
    )
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "same fact",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "evidence": [
                        {
                            "source_turn_id": "turn-1",
                            "start_char": 0,
                            "end_char": 4,
                            "quote": "same",
                        }
                    ],
                }
            ]
        }
    )

    result = LLMEventExtractor(model).extract(request)

    assert result.candidates == []
    assert len(result.rejections) == 1
    error = result.rejections[0]
    assert error.code == "ambiguous_turn_id"
    assert error.source_turn_id == "turn-1"
    assert error.source_session_id is None


def test_llm_extractor_resolves_duplicate_turn_id_by_session_independent_of_order() -> None:
    first_turn = ExtractionTurn(
        turn_id="turn-1",
        session_id="session-a",
        speaker="Alice",
        content="alpha fact",
    )
    second_turn = ExtractionTurn(
        turn_id="turn-1",
        session_id="session-b",
        speaker="Bob",
        content="beta fact",
    )

    def extract(
        turns: list[ExtractionTurn],
    ) -> tuple[object, dict[str, object]]:
        model = StaticJSONChatModel(
            {
                "events": [
                    {
                        "content": "alpha fact",
                        "speaker": "Alice",
                        "entities": ["Alice"],
                        "evidence": [
                            {
                                "source_turn_id": "turn-1",
                                "source_session_id": "session-a",
                                "start_char": 0,
                                "end_char": 10,
                                "quote": "alpha fact",
                            }
                        ],
                    }
                ]
            }
        )
        request = ExtractionInput(
            user_id="u1",
            dataset="dataset-a",
            sample_id="sample-a",
            turns=turns,
        )
        result = LLMEventExtractor(model).extract(request)
        prompt = json.loads(model.requests[0][1].content)
        return result.candidates[0].memory.evidence_refs[0], prompt

    forward_evidence, prompt = extract([first_turn, second_turn])
    reverse_evidence, _ = extract([second_turn, first_turn])

    assert forward_evidence == reverse_evidence
    assert forward_evidence.source_id == (
        "dataset=dataset-a/sample=sample-a/session=session-a/turn=turn-1"
    )
    assert forward_evidence.quote == "alpha fact"
    evidence_schema = prompt["schema"]["events"][0]["evidence"][0]
    assert "source_session_id" in evidence_schema
    assert any(
        "duplicate turn_id" in constraint for constraint in prompt["constraints"]
    )


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


def test_rule_extractor_does_not_link_inside_unicode_casefold_expansion() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="ß",
            )
        ],
        event_summaries=[
            ExtractionEventSummary(
                session_id="session-1",
                events={"Alice": ["s"]},
            )
        ],
    )

    memory = RuleEventExtractor().extract(request).candidates[0].memory

    assert [evidence.source_type for evidence in memory.evidence_refs] == [
        "event_summary"
    ]


def test_rule_extractor_links_complete_unicode_casefold_expansion() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="ß",
            )
        ],
        event_summaries=[
            ExtractionEventSummary(
                session_id="session-1",
                events={"Alice": ["SS"]},
            )
        ],
    )

    memory = RuleEventExtractor().extract(request).candidates[0].memory

    turn_evidence = memory.evidence_refs[1]
    assert turn_evidence.quote == "ß"
    assert turn_evidence.quote.casefold() == memory.content.casefold()


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


def test_normalized_locomo_observation_mapping_preserves_scoped_identity(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (FIXTURES / "locomo/locomo_tiny.json").read_text(encoding="utf-8")
    )
    payload[0]["observation"] = {
        "session_1_observation": {
            "Caroline": [
                ["Caroline joined a support group.", "D1:1"],
                ["Caroline felt accepted there.", "D1:1"],
            ]
        }
    }
    fixture_path = tmp_path / "locomo_with_observations.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    record = next(iter_locomo_records(fixture_path))

    request = ExtractionInput.from_normalized_record(record, user_id="u1")

    assert [observation.content for observation in request.observations] == [
        "Caroline joined a support group.",
        "Caroline felt accepted there.",
    ]
    assert [observation.observation_id for observation in request.observations] == [
        "D1:1",
        "D1:1",
    ]
    assert [observation.index for observation in request.observations] == [0, 1]
    assert [observation.session_id for observation in request.observations] == [
        "session_1",
        "session_1",
    ]

    result = RuleEventExtractor().extract(request)
    observation_evidence = [
        candidate.memory.evidence_refs[0]
        for candidate in result.candidates
        if candidate.memory.evidence_refs[0].source_type == "observation"
    ]
    assert [evidence.source_id for evidence in observation_evidence] == [
        "dataset=locomo/sample=conv-tiny/session=session_1/"
        "observation=D1%3A1/index=0",
        "dataset=locomo/sample=conv-tiny/session=session_1/"
        "observation=D1%3A1/index=1",
    ]
    assert observation_evidence[0].metadata == {
        "dataset": "locomo",
        "sample_id": "conv-tiny",
        "session_id": "session_1",
        "raw_observation_id": "D1:1",
        "raw_observation_index": 0,
        "speaker": "Caroline",
    }


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


def test_clear_target_fields_removes_answer_metadata_and_observations() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="I live in Austin.",
                metadata={"has_answer": True},
            )
        ],
        observations=["Alice deployed v2."],
    )

    cleared = request.clear_target_fields()

    assert cleared.turns[0].metadata == {}
    assert cleared.observations == []
    assert cleared.turns[0].content == "I live in Austin."


def test_llm_extractor_require_turn_evidence_adds_constraint_to_prompt() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice shipped the release.",
            )
        ],
        require_turn_evidence=True,
    )
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Alice shipped the release.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "evidence": [
                        {
                            "source_turn_id": "turn-1",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice ship",
                        }
                    ],
                }
            ]
        }
    )

    result = LLMEventExtractor(model).extract(request)
    prompt = json.loads(model.requests[0][1].content)
    assert len(result.candidates) == 1
    assert any(
        "Every event MUST reference at least one raw turn" in constraint
        for constraint in prompt["constraints"]
    )


def test_llm_extractor_require_turn_evidence_rejects_unresolvable_evidence() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice shipped the release.",
            )
        ],
        require_turn_evidence=True,
    )
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Alice shipped the release.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
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

    result = LLMEventExtractor(model).extract(request)
    assert result.candidates == []
    assert len(result.rejections) == 1
    assert result.rejections[0].code == "unknown_turn_id"
