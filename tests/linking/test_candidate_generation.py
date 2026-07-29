from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from evoeventmem.domain.models import MemoryRecord
from evoeventmem.linking import (
    CandidateGenerationRequest,
    LinkCandidateGenerator,
    LinkCandidateKind,
    calculate_candidate_recall,
)
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

FIXTURE = Path("tests/fixtures/linking/m09_tiny_linking.json")


def _fixture_records() -> tuple[MemoryRecord, list[MemoryRecord], dict[str, list[str]]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = [MemoryRecord.model_validate(item) for item in payload["memories"]]
    source_id = UUID(payload["source_memory_id"])
    source = next(record for record in records if record.memory_id == source_id)
    existing = [record for record in records if record.memory_id != source_id]
    return source, existing, payload["gold"]


def test_entity_candidates_are_bounded_deterministic_and_alias_aware() -> None:
    source, existing, gold = _fixture_records()
    generator = LinkCandidateGenerator(embedding_model=DeterministicFakeEmbeddingModel())
    request = CandidateGenerationRequest(
        source=source,
        existing=existing,
        max_entity_candidates=2,
        max_event_candidates=1,
    )

    first = generator.generate(request)
    second = generator.generate(request)

    assert [candidate.candidate_id for candidate in first.entity_candidates] == [
        candidate.candidate_id for candidate in second.entity_candidates
    ]
    assert len(first.entity_candidates) == 2
    assert first.entity_candidates[0].candidate_kind is LinkCandidateKind.ENTITY
    assert first.entity_candidates[0].policy_name == "entity-normalized-alias-embedding.v1"
    assert "alias_match" in first.entity_candidates[0].reasons
    assert first.entity_candidates[0].target_memory.memory_id == UUID(
        gold["entity_target_memory_ids"][0]
    )
    assert first.entity_candidates[0].target_entity is not None
    assert first.entity_candidates[0].target_entity.name == "Caroline"


def test_event_candidates_use_time_window_and_preserve_memory_provenance() -> None:
    source, existing, gold = _fixture_records()
    generator = LinkCandidateGenerator(embedding_model=DeterministicFakeEmbeddingModel())

    result = generator.generate(
        CandidateGenerationRequest(
            source=source,
            existing=existing,
            max_entity_candidates=4,
            max_event_candidates=3,
            event_time_window_days=3,
        )
    )

    target_ids = [str(candidate.target_memory.memory_id) for candidate in result.event_candidates]
    assert target_ids == gold["event_target_memory_ids"]
    assert {candidate.policy_name for candidate in result.event_candidates} == {
        "event-time-window-embedding.v1"
    }
    assert all(
        candidate.candidate_kind is LinkCandidateKind.EVENT
        for candidate in result.event_candidates
    )
    assert all("within_time_window" in candidate.reasons for candidate in result.event_candidates)
    assert str(existing[1].memory_id) not in target_ids

    candidate = result.event_candidates[0]
    assert candidate.source_memory.evidence_refs == source.evidence_refs
    assert candidate.target_memory.evidence_refs[0].source_id == "D1:1"
    assert candidate.source_memory.event_time == datetime(2023, 5, 8, tzinfo=UTC)
    assert candidate.source_memory.valid_from == candidate.source_memory.event_time
    assert candidate.target_memory.valid_to is None


def test_gold_candidate_recall_at_k_and_latency_are_measurable() -> None:
    source, existing, gold = _fixture_records()
    generator = LinkCandidateGenerator(embedding_model=DeterministicFakeEmbeddingModel())
    result = generator.generate(
        CandidateGenerationRequest(
            source=source,
            existing=existing,
            max_entity_candidates=2,
            max_event_candidates=2,
        )
    )

    metrics = calculate_candidate_recall(
        result,
        gold_entity_target_memory_ids={UUID(value) for value in gold["entity_target_memory_ids"]},
        gold_event_target_memory_ids={UUID(value) for value in gold["event_target_memory_ids"]},
        k=2,
    )

    assert metrics.entity_recall_at_k == 1.0
    assert metrics.event_recall_at_k == 1.0
    assert metrics.k == 2
    assert metrics.latency_ms >= 0.0
    assert metrics.generated_entity_candidates == 2
    assert metrics.generated_event_candidates == 1
