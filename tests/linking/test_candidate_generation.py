from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from evoeventmem.core.ports import EmbeddingResponse
from evoeventmem.domain.models import MemoryKind, MemoryRecord, MemoryStatus
from evoeventmem.linking import (
    CandidateGenerationRequest,
    CandidateGenerationResult,
    LinkCandidateGenerator,
    LinkCandidateKind,
    calculate_candidate_recall,
)
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel

FIXTURE = Path("tests/fixtures/linking/m09_tiny_linking.json")


class CountingEmbeddingModel:
    model_id = "counting-embedding"

    def __init__(self) -> None:
        self.calls = 0
        self.embedded_texts: list[str] = []
        self._wrapped = DeterministicFakeEmbeddingModel(model_id=self.model_id)

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        self.calls += 1
        self.embedded_texts.extend(texts)
        return self._wrapped.embed_texts(texts)


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


def test_zero_day_event_window_excludes_both_directions_symmetrically() -> None:
    source, existing, _ = _fixture_records()
    assert source.event_time is not None
    base = existing[0]
    exact = base.model_copy(
        update={
            "memory_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "event_time": source.event_time,
            "valid_from": source.event_time,
        }
    )
    before = base.model_copy(
        update={
            "memory_id": UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            "event_time": source.event_time - timedelta(hours=1),
            "valid_from": source.event_time - timedelta(hours=1),
        }
    )
    after = base.model_copy(
        update={
            "memory_id": UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            "event_time": source.event_time + timedelta(hours=1),
            "valid_from": source.event_time + timedelta(hours=1),
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=source,
            existing=[before, exact, after],
            max_entity_candidates=1,
            max_event_candidates=3,
            event_time_window_days=0,
        )
    )

    assert [candidate.target_memory.memory_id for candidate in result.event_candidates] == [
        exact.memory_id
    ]


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


def test_normalized_alias_and_lexical_entity_indexes_produce_direct_candidates() -> None:
    source, existing, _ = _fixture_records()
    exact = existing[0].model_copy(
        update={
            "memory_id": UUID("55555555-5555-4555-8555-555555555555"),
            "entities": [source.entities[0].model_copy(update={"name": " CARRIE!! "})],
            "metadata": {},
        }
    )
    lexical = existing[0].model_copy(
        update={
            "memory_id": UUID("66666666-6666-4666-8666-666666666666"),
            "entities": [source.entities[0].model_copy(update={"name": "Carrie Smith"})],
            "metadata": {},
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=source,
            existing=[exact, existing[0], lexical],
            max_entity_candidates=3,
            max_event_candidates=1,
        )
    )
    reasons_by_target = {
        candidate.target_memory.memory_id: candidate.reasons
        for candidate in result.entity_candidates
    }

    assert "exact_normalized_entity_key" in reasons_by_target[exact.memory_id]
    assert "alias_match" in reasons_by_target[existing[0].memory_id]
    assert "lexical_token_match" in reasons_by_target[lexical.memory_id]


def test_fact_slot_is_a_direct_event_policy_candidate_and_reports_recall() -> None:
    source, existing, _ = _fixture_records()
    source_fact = source.model_copy(
        update={
            "memory_kind": MemoryKind.FACT,
            "content": "Carrie lives in Taipei.",
            "entities": [],
            "event_time": None,
            "valid_from": datetime(2024, 1, 1, tzinfo=UTC),
            "metadata": {"fact_slot": " Profile.City ", "fact_value": "Taipei"},
        }
    )
    slot_target = existing[2].model_copy(
        update={
            "memory_id": UUID("77777777-7777-4777-8777-777777777777"),
            "content": "Carrie lives in Seattle.",
            "entities": [],
            "valid_from": datetime(2020, 1, 1, tzinfo=UTC),
            "metadata": {"fact_slot": "profile.city", "fact_value": "Seattle"},
        }
    )
    different_slot = slot_target.model_copy(
        update={
            "memory_id": UUID("88888888-8888-4888-8888-888888888888"),
            "metadata": {"fact_slot": "work.city", "fact_value": "Seattle"},
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=source_fact,
            existing=[different_slot, slot_target],
            max_entity_candidates=1,
            max_event_candidates=1,
            event_time_window_days=1,
        )
    )

    assert [candidate.target_memory.memory_id for candidate in result.event_candidates] == [
        slot_target.memory_id
    ]
    assert result.event_candidates[0].policy_name == LinkCandidateGenerator.EVENT_POLICY
    assert "fact_slot_match" in result.event_candidates[0].reasons
    metrics = calculate_candidate_recall(
        result,
        gold_entity_target_memory_ids=set(),
        gold_event_target_memory_ids={slot_target.memory_id},
        k=1,
    )
    assert metrics.event_recall_at_k == 1.0


def test_scope_identity_and_status_filters_apply_to_both_policies() -> None:
    source, existing, _ = _fixture_records()
    valid = existing[0]
    other_user = valid.model_copy(
        update={
            "memory_id": UUID("99999999-9999-4999-8999-999999999991"),
            "user_id": "u2",
        }
    )
    other_tenant = valid.model_copy(
        update={
            "memory_id": UUID("99999999-9999-4999-8999-999999999992"),
            "tenant_id": "tenant-2",
        }
    )
    deleted = valid.model_copy(
        update={
            "memory_id": UUID("99999999-9999-4999-8999-999999999993"),
            "status": MemoryStatus.DELETED,
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=source,
            existing=[source, other_user, other_tenant, deleted, valid],
            max_entity_candidates=10,
            max_event_candidates=10,
        )
    )
    entity_target_ids = {
        candidate.target_memory.memory_id for candidate in result.entity_candidates
    }
    event_target_ids = {
        candidate.target_memory.memory_id for candidate in result.event_candidates
    }

    assert entity_target_ids == {valid.memory_id}
    assert event_target_ids == {valid.memory_id}


def test_stable_fallback_is_deterministic_and_comparisons_are_reported() -> None:
    source, existing, _ = _fixture_records()
    unrelated = existing[0].model_copy(
        update={
            "memory_id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            "content": "Zephyr quartz.",
            "entities": [source.entities[0].model_copy(update={"name": "Zed"})],
            "metadata": {},
        }
    )
    request = CandidateGenerationRequest(
        source=source,
        existing=[unrelated],
        max_entity_candidates=1,
        max_event_candidates=1,
        min_embedding_similarity=-1.0,
    )
    generator = LinkCandidateGenerator(DeterministicFakeEmbeddingModel())

    first = generator.generate(request)
    second = generator.generate(request)

    assert first.entity_candidates[0].candidate_id == second.entity_candidates[0].candidate_id
    assert first.event_candidates[0].candidate_id == second.event_candidates[0].candidate_id
    assert "stable_fallback" in first.entity_candidates[0].reasons
    assert "stable_fallback" in first.event_candidates[0].reasons
    assert first.entity_comparison_count == 1
    assert first.event_comparison_count == 1
    metrics = calculate_candidate_recall(
        first,
        gold_entity_target_memory_ids=set(),
        gold_event_target_memory_ids=set(),
        k=1,
    )
    assert metrics.entity_comparison_count == 1
    assert metrics.event_comparison_count == 1


def test_candidate_limits_bound_embeddings_independent_of_existing_size() -> None:
    source, existing, _ = _fixture_records()
    template = existing[0].model_copy(
        update={
            "metadata": {},
            "event_time": source.event_time,
            "valid_from": source.valid_from,
        }
    )

    def run(size: int) -> tuple[CandidateGenerationResult, CountingEmbeddingModel]:
        records = [
            template.model_copy(
                update={
                    "memory_id": UUID(int=index + 100),
                    "content": f"Target event {index}",
                    "entities": [
                        source.entities[0].model_copy(update={"name": f"Target Person {index}"})
                    ],
                }
            )
            for index in range(size)
        ]
        model = CountingEmbeddingModel()
        result = LinkCandidateGenerator(model).generate(
            CandidateGenerationRequest(
                source=source,
                existing=records,
                max_entity_candidates=3,
                max_event_candidates=2,
                min_embedding_similarity=-1.0,
            )
        )
        return result, model

    small_result, small_model = run(100)
    large_result, large_model = run(10_000)

    assert small_model.calls == large_model.calls == 1
    assert len(small_model.embedded_texts) == len(large_model.embedded_texts)
    assert len(large_model.embedded_texts) <= 2 * (3 + 2)
    assert len(large_model.embedded_texts) == len(set(large_model.embedded_texts))
    assert small_result.entity_comparison_count == large_result.entity_comparison_count == 3
    assert small_result.event_comparison_count == large_result.event_comparison_count == 2
