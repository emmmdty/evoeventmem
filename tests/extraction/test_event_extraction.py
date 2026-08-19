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
                    "fact_slot": "activity.support_group_attendance",
                    "fact_value": "LGBTQ support group on 7 May 2023",
                    "valid_from": "2023-05-07T00:00:00+00:00",
                    "valid_until": None,
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


def test_v3_prompt_version_is_incremented() -> None:
    assert LLMEventExtractor.PROMPT_VERSION == "event-extraction.v3"


def test_v3_prompt_advertises_fact_slot_schema_and_rules() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice lives in Seattle.",
            )
        ],
    )
    model = StaticJSONChatModel({"events": []})
    LLMEventExtractor(model).extract(request)
    prompt = json.loads(model.requests[0][1].content)

    event_schema = prompt["schema"]["events"][0]
    assert "fact_slot" in event_schema
    assert "fact_value" in event_schema
    assert "valid_from" in event_schema
    assert "valid_until" in event_schema
    # v3 prompt must advertise that fact_slot is REQUIRED and that the
    # "none" sentinel is the legitimate way to mark non-fact events.
    fact_slot_desc = event_schema["fact_slot"]
    assert "REQUIRED" in fact_slot_desc
    assert "none" in fact_slot_desc

    assert "fact_slot_rules" in prompt
    rules_text = " ".join(prompt["fact_slot_rules"])
    assert "profile.city" in rules_text
    assert "EXACT same fact_slot string" in rules_text
    assert "valid_from" in rules_text
    assert "valid_until" in rules_text
    # v3 rule must instruct the LLM to use "none" sentinel (not null)
    # for transient activity.
    assert "fact_slot='none'" in rules_text or "fact_slot = 'none'" in rules_text

    assert "fact_slot_examples" in prompt
    examples = prompt["fact_slot_examples"]
    assert len(examples) >= 2
    move_example = next(
        (ex for ex in examples if "city" in ex["scenario"] or "move" in ex["scenario"]),
        None,
    )
    assert move_example is not None
    assert move_example["turn_1_event"]["fact_slot"] == move_example["turn_2_event"]["fact_slot"]
    assert move_example["turn_1_event"]["fact_value"] != move_example["turn_2_event"]["fact_value"]
    assert move_example["turn_1_event"]["valid_until"] is not None
    assert move_example["turn_2_event"]["valid_from"] is not None
    assert move_example["turn_2_event"]["valid_until"] is None

    # v3 sentinel example: a non-fact event must use fact_slot="none"
    # (not None). At least one such example must exist so the LLM learns
    # the sentinel pattern for transient activity.
    sentinel_example = next(
        (ex for ex in examples if ex["turn_1_event"]["fact_slot"] == "none"),
        None,
    )
    assert sentinel_example is not None
    assert sentinel_example["turn_1_event"]["fact_value"] == "none"
    assert sentinel_example["turn_1_event"]["valid_from"] is None
    assert sentinel_example["turn_1_event"]["valid_until"] is None


def test_event_draft_requires_fact_slot_and_accepts_none_sentinel() -> None:
    from pydantic import ValidationError

    from evoeventmem.extraction import _FACT_SLOT_NONE_SENTINEL, _EventDraft

    # Real fact_slot strings are stripped of surrounding whitespace and
    # paired with fact_value + valid_from as before.
    draft_stripped = _EventDraft(
        content="x",
        fact_slot="  profile.city  ",
        fact_value="  Portland  ",
        valid_from="2023-06-01T00:00:00+00:00",
    )
    assert draft_stripped.fact_slot == "profile.city"
    assert draft_stripped.fact_value == "Portland"
    assert draft_stripped.valid_from == datetime(2023, 6, 1, tzinfo=UTC)
    assert draft_stripped.valid_until is None

    # v3: fact_slot is REQUIRED. Empty string / whitespace-only / missing
    # all raise ValidationError so the LLMEventExtractor retry framework
    # can re-prompt the model.
    with pytest.raises(ValidationError, match="fact_slot"):
        _EventDraft(content="x", fact_slot="")
    with pytest.raises(ValidationError, match="fact_slot"):
        _EventDraft(content="x", fact_slot="   ")
    with pytest.raises(ValidationError, match="fact_slot"):
        _EventDraft(content="x")  # fact_slot absent entirely

    # v3 sentinel: fact_slot="none" is the legitimate marker for
    # non-fact events. fact_value is auto-filled to "none" when missing
    # so the model_validator's "fact_value must be set" rule is satisfied.
    sentinel_draft = _EventDraft(content="x", fact_slot="none")
    assert sentinel_draft.fact_slot == _FACT_SLOT_NONE_SENTINEL
    assert sentinel_draft.fact_value == _FACT_SLOT_NONE_SENTINEL
    assert sentinel_draft.valid_from is None
    assert sentinel_draft.valid_until is None

    # Explicit fact_slot="none" + fact_value="none" is also accepted.
    explicit_sentinel = _EventDraft(
        content="x", fact_slot="none", fact_value="none"
    )
    assert explicit_sentinel.fact_slot == "none"
    assert explicit_sentinel.fact_value == "none"


def test_event_draft_enforces_fact_contract_invariants() -> None:
    from pydantic import ValidationError

    from evoeventmem.extraction import _EventDraft

    # fact_slot set without fact_value -> invalid (existing v2 rule
    # preserved in v3).
    with pytest.raises(ValidationError, match="fact_value must be set"):
        _EventDraft(content="x", fact_slot="profile.city")

    # valid_until set without valid_from -> invalid (existing v2 rule
    # preserved in v3).
    with pytest.raises(ValidationError, match="valid_from must be set"):
        _EventDraft(
            content="x",
            fact_slot="profile.city",
            fact_value="Seattle",
            valid_until="2023-06-01T00:00:00+00:00",
        )

    # v3: fact_value set without fact_slot now raises "Field required"
    # on fact_slot FIRST (the v2 "fact_slot must be set" rule is gone
    # because fact_slot is no longer Optional — it's required at the
    # schema level).
    with pytest.raises(ValidationError, match="fact_slot"):
        _EventDraft(content="x", fact_value="Portland")

    # v3: valid_from set without fact_slot raises "Field required" on
    # fact_slot FIRST.
    with pytest.raises(ValidationError, match="fact_slot"):
        _EventDraft(
            content="x",
            valid_from="2023-06-01T00:00:00+00:00",
        )

    # v3 sentinel invariants: fact_slot="none" forbids a real fact_value
    # and forbids temporal bounds (no SUPERSEDE keying on sentinel events).
    with pytest.raises(ValidationError, match="fact_value must be 'none'"):
        _EventDraft(
            content="x",
            fact_slot="none",
            fact_value="Seattle",
        )
    with pytest.raises(ValidationError, match="valid_from / valid_until"):
        _EventDraft(
            content="x",
            fact_slot="none",
            fact_value="none",
            valid_from="2023-06-01T00:00:00+00:00",
        )

    # Full state-change pair (old value end) is valid
    end_draft = _EventDraft(
        content="x",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from="2023-01-15T00:00:00+00:00",
        valid_until="2023-06-01T00:00:00+00:00",
    )
    assert end_draft.fact_slot == "profile.city"
    assert end_draft.valid_until == datetime(2023, 6, 1, tzinfo=UTC)


def test_llm_extractor_propagates_fact_slot_metadata_and_validity_bounds() -> None:
    request = ExtractionInput(
        user_id="u1",
        dataset="dataset-a",
        sample_id="sample-a",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice lives in Portland now.",
            )
        ],
    )
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Alice lives in Portland.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "event_time": "2023-06-01T10:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "turn-1",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice lives",
                        }
                    ],
                    "fact_slot": "profile.city",
                    "fact_value": "Portland",
                    "valid_from": "2023-06-01T00:00:00+00:00",
                    "valid_until": None,
                }
            ]
        }
    )

    result = LLMEventExtractor(model).extract(request)
    assert len(result.candidates) == 1
    memory = result.candidates[0].memory

    assert memory.metadata["fact_slot"] == "profile.city"
    assert memory.metadata["fact_value"] == "Portland"
    assert memory.metadata["valid_from"] == "2023-06-01T00:00:00+00:00"
    assert "valid_until" not in memory.metadata
    assert memory.valid_from == datetime(2023, 6, 1, tzinfo=UTC)
    assert memory.valid_to is None
    assert memory.event_time == datetime(2023, 6, 1, 10, 0, tzinfo=UTC)
    assert memory.metadata["extractor_prompt_version"] == "event-extraction.v3"
    assert memory.metadata["source_dataset"] == "dataset-a"
    assert memory.metadata["source_sample_id"] == "sample-a"


def test_llm_extractor_propagates_state_change_end_event_valid_until() -> None:
    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice used to live in Seattle.",
            )
        ],
    )
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Alice lived in Seattle.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "event_time": "2023-01-15T10:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "turn-1",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice used",
                        }
                    ],
                    "fact_slot": "profile.city",
                    "fact_value": "Seattle",
                    "valid_from": "2023-01-15T00:00:00+00:00",
                    "valid_until": "2023-06-01T00:00:00+00:00",
                }
            ]
        }
    )

    result = LLMEventExtractor(model).extract(request)
    memory = result.candidates[0].memory

    assert memory.metadata["fact_slot"] == "profile.city"
    assert memory.metadata["fact_value"] == "Seattle"
    assert memory.metadata["valid_from"] == "2023-01-15T00:00:00+00:00"
    assert memory.metadata["valid_until"] == "2023-06-01T00:00:00+00:00"
    assert memory.valid_from == datetime(2023, 1, 15, tzinfo=UTC)
    assert memory.valid_to == datetime(2023, 6, 1, tzinfo=UTC)


def test_llm_extractor_salvages_missing_fact_slot_as_none_sentinel() -> None:
    """v3 salvage path: when the LLM emits events without fact_slot (or with
    null/empty fact_slot) and 3 retries fail to fix it, the extractor falls
    back to setting ``fact_slot="none"`` on the offending events so they are
    still recorded (preserving evidence provenance) rather than dropping the
    whole chunk.

    The salvaged events carry ``metadata.fact_slot == "none"`` and
    ``metadata.fact_value == "none"``, signalling 'no durable fact' to the
    consolidator. The stats script counts them as non-empty ``fact_slot``
    (since "none" is a non-null string); the S1c review reports the sentinel
    rate separately so reviewers can detect prompt regressions.
    """
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
    )
    # Payload omits fact_slot entirely. The v3 schema rejects this on
    # every retry; the salvage path on the 3rd attempt substitutes
    # fact_slot="none" so the event is still recorded.
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
    # Salvage should have produced 1 candidate from the otherwise-invalid
    # chunk; without salvage, the chunk would have been dropped entirely.
    assert len(result.candidates) == 1
    memory = result.candidates[0].memory

    assert memory.metadata["fact_slot"] == "none"
    assert memory.metadata["fact_value"] == "none"
    # Salvage clears temporal bounds -> _build_memory omits the keys.
    assert "valid_from" not in memory.metadata
    assert "valid_until" not in memory.metadata
    assert memory.valid_from is None
    assert memory.valid_to is None
    assert memory.metadata["extractor_prompt_version"] == "event-extraction.v3"
    # 3 retries were attempted before salvage kicked in.
    assert model.calls == 3


def test_fact_extraction_chain_reaches_supersede_on_real_extraction_output() -> None:
    """End-to-end: v2 LLM output with fact_slot + valid_from makes SUPERSEDE
    logically reachable through the public consolidator API.

    This verifies that the v2 extraction schema lands (fact_slot/fact_value/
    valid_from propagated into MemoryRecord + metadata) AND that the existing
    consolidation decision tree can reach SUPERSEDE on real extraction
    output (closing R1b). It does NOT measure empirical SUPERSEDE trigger
    rate on LongMemEval data, which is the S2 stage.
    """
    from evoeventmem.consolidation import ConsolidationAction, ETECConsolidator
    from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
    from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

    repository = InMemoryMemoryRepository()

    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice lives in Seattle.",
            ),
            ExtractionTurn(
                turn_id="turn-2",
                session_id="session-2",
                speaker="Alice",
                content="Alice moved to Portland.",
            ),
        ],
    )
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Alice lives in Seattle.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "event_time": "2023-01-01T00:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "turn-1",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice live",
                        }
                    ],
                    "fact_slot": "profile.city",
                    "fact_value": "Seattle",
                    "valid_from": "2023-01-01T00:00:00+00:00",
                    "valid_until": None,
                },
                {
                    "content": "Alice lives in Portland.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "event_time": "2023-06-01T00:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "turn-2",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice move",
                        }
                    ],
                    "fact_slot": "profile.city",
                    "fact_value": "Portland",
                    "valid_from": "2023-06-01T00:00:00+00:00",
                    "valid_until": None,
                },
            ]
        }
    )

    result = LLMEventExtractor(model).extract(request)
    assert len(result.candidates) == 2

    old_memory, new_memory = (
        result.candidates[0].memory,
        result.candidates[1].memory,
    )
    assert old_memory.metadata["fact_slot"] == "profile.city"
    assert new_memory.metadata["fact_slot"] == "profile.city"
    assert old_memory.metadata["fact_value"] != new_memory.metadata["fact_value"]
    # R1b closure: fact events now carry valid_from, yielding an open
    # interval (valid_from, +inf) so two same-slot facts asserted at
    # different event_times temporally overlap and the contradiction
    # score formula at consolidation.py:886 can reach >=0.7.
    assert old_memory.valid_from is not None
    assert new_memory.valid_from is not None

    consolidator = ETECConsolidator(embedding_model=DeterministicFakeEmbeddingModel())
    first_decision = consolidator.apply(repository, old_memory)
    assert first_decision.decision.action is ConsolidationAction.ADD
    second_decision = consolidator.apply(repository, new_memory)

    assert second_decision.decision.action is ConsolidationAction.SUPERSEDE
    assert second_decision.decision.features.contradiction_score >= 0.7
    assert second_decision.decision.target_memory_id == old_memory.memory_id


def test_extraction_with_none_sentinel_fact_slot_does_not_supersede() -> None:
    """Negative control: v3 outputs where both events carry the
    ``fact_slot="none"`` sentinel (e.g. two transient activity events) do
    not trigger SUPERSEDE, because the consolidator's first gate
    (``_same_fact_slot``) requires the same slot AND the third gate
    (``not _same_fact_value``) requires distinct values — two sentinel
    events share slot AND value, so the third gate fails. This preserves
    the structural R1 finding that the v3 schema change does not create
    spurious SUPERSEDE decisions on events that carry no durable fact.
    """
    from evoeventmem.consolidation import ConsolidationAction, ETECConsolidator
    from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
    from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

    repository = InMemoryMemoryRepository()

    request = ExtractionInput(
        user_id="u1",
        turns=[
            ExtractionTurn(
                turn_id="turn-1",
                session_id="session-1",
                speaker="Alice",
                content="Alice lives in Seattle.",
            ),
            ExtractionTurn(
                turn_id="turn-2",
                session_id="session-2",
                speaker="Alice",
                content="Alice moved to Portland.",
            ),
        ],
    )
    # v3 payload: both events are non-facts (transient dialogue) so they
    # carry the "none" sentinel instead of null fact_slot.
    model = StaticJSONChatModel(
        {
            "events": [
                {
                    "content": "Alice asked the assistant about Seattle.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "event_time": "2023-01-01T00:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "turn-1",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice live",
                        }
                    ],
                    "fact_slot": "none",
                    "fact_value": "none",
                    "valid_from": None,
                    "valid_until": None,
                },
                {
                    "content": "Alice asked the assistant about Portland.",
                    "speaker": "Alice",
                    "entities": ["Alice"],
                    "event_time": "2023-06-01T00:00:00+00:00",
                    "evidence": [
                        {
                            "source_turn_id": "turn-2",
                            "start_char": 0,
                            "end_char": 10,
                            "quote": "Alice move",
                        }
                    ],
                    "fact_slot": "none",
                    "fact_value": "none",
                    "valid_from": None,
                    "valid_until": None,
                },
            ]
        }
    )

    result = LLMEventExtractor(model).extract(request)
    assert len(result.candidates) == 2

    for candidate in result.candidates:
        assert candidate.memory.metadata["fact_slot"] == "none"
        assert candidate.memory.metadata["fact_value"] == "none"
        assert candidate.memory.valid_from is None
        assert candidate.memory.valid_to is None

    old_memory, new_memory = (
        result.candidates[0].memory,
        result.candidates[1].memory,
    )

    consolidator = ETECConsolidator(embedding_model=DeterministicFakeEmbeddingModel())
    consolidator.apply(repository, old_memory)
    second_decision = consolidator.apply(repository, new_memory)

    assert second_decision.decision.action is not ConsolidationAction.SUPERSEDE
    assert second_decision.decision.features.contradiction_score == 0.0


def test_salvage_missing_fact_slot_replaces_null_and_empty_with_none_sentinel() -> None:
    """v3 salvage function: when 3 retries have failed, the salvage path
    re-parses the LLM response and replaces missing/null/empty ``fact_slot``
    with the ``"none"`` sentinel so the chunk's events are still recorded
    (preserving evidence provenance) rather than dropping the whole chunk.

    The salvage is conservative: it only repairs ``fact_slot`` missingness.
    Other validation errors (bad evidence, malformed datetimes, real
    fact_slot with missing fact_value, etc.) cause salvage to return ``None``,
    letting the caller drop the chunk as before.
    """
    from evoeventmem.extraction import (
        _FACT_SLOT_NONE_SENTINEL,
        _LLMExtractionPayload,
        _salvage_missing_fact_slot,
    )

    # Case 1: events with missing / null / empty / whitespace fact_slot
    # all get salvaged to the "none" sentinel with fact_value aligned.
    raw_with_mixed = json.dumps(
        {
            "events": [
                {"content": "evt-1", "fact_slot": None},
                {"content": "evt-2", "fact_slot": ""},
                {"content": "evt-3", "fact_slot": "   "},
                {
                    "content": "evt-4",
                    "fact_slot": "profile.city",
                    "fact_value": "Portland",
                    "valid_from": "2023-06-01T00:00:00+00:00",
                },
            ]
        }
    )
    salvaged = _salvage_missing_fact_slot(raw_with_mixed)
    assert salvaged is not None
    assert isinstance(salvaged, _LLMExtractionPayload)
    assert salvaged.events[0].fact_slot == _FACT_SLOT_NONE_SENTINEL
    assert salvaged.events[0].fact_value == _FACT_SLOT_NONE_SENTINEL
    assert salvaged.events[0].valid_from is None
    assert salvaged.events[0].valid_until is None
    assert salvaged.events[1].fact_slot == _FACT_SLOT_NONE_SENTINEL
    assert salvaged.events[2].fact_slot == _FACT_SLOT_NONE_SENTINEL
    # Real fact_slot events are untouched by salvage.
    assert salvaged.events[3].fact_slot == "profile.city"
    assert salvaged.events[3].fact_value == "Portland"

    # Case 2: malformed JSON returns None (caller drops chunk as before).
    assert _salvage_missing_fact_slot("not json at all") is None
    assert _salvage_missing_fact_slot("") is None

    # Case 3: payload missing the "events" key returns None.
    assert _salvage_missing_fact_slot(json.dumps({"other": 1})) is None

    # Case 4: real fact_slot with missing fact_value cannot be salvaged —
    # the salvage only fixes fact_slot missingness, not other contract
    # violations. Returns None so the caller drops the chunk.
    raw_bad_contract = json.dumps(
        {
            "events": [
                {"content": "evt-1", "fact_slot": "profile.city"},
            ]
        }
    )
    assert _salvage_missing_fact_slot(raw_bad_contract) is None

    # Case 5: a JSON-fenced response is also salvageable.
    fenced = "```json\n" + raw_with_mixed + "\n```"
    salvaged_fenced = _salvage_missing_fact_slot(fenced)
    assert salvaged_fenced is not None
    assert salvaged_fenced.events[0].fact_slot == _FACT_SLOT_NONE_SENTINEL
