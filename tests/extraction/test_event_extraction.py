from __future__ import annotations

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
    ExtractionInput,
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
    evidence = memory.evidence_refs[0]
    assert memory.memory_kind is MemoryKind.EVENT
    assert memory.content == "Caroline went to an LGBTQ support group on 7 May 2023."
    assert memory.entities[0].name == "Caroline"
    assert memory.entities[0].role == "speaker"
    assert memory.roles == {"Caroline": "speaker"}
    assert memory.event_time == datetime(2023, 5, 7, tzinfo=UTC)
    assert evidence.source_type == "turn"
    assert evidence.source_id == "D1:1"
    assert evidence.locator == "chars=0:43"
    assert evidence.quote == "I went to an LGBTQ support group yesterday."
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
