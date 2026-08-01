from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import evoeventmem.linking as linking_module
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


class FakeEmbeddingCandidateIndex:
    def __init__(
        self,
        *,
        entity_refs: Sequence[object] = (),
        event_ids: Sequence[UUID] = (),
    ) -> None:
        self.entity_refs = list(entity_refs)
        self.event_ids = list(event_ids)
        self.entity_queries: list[tuple[str, int]] = []
        self.event_queries: list[tuple[str, int]] = []

    def query_entity_candidates(self, query: str, *, limit: int) -> Sequence[object]:
        self.entity_queries.append((query, limit))
        return self.entity_refs[:limit]

    def query_event_candidates(self, query: str, *, limit: int) -> Sequence[UUID]:
        self.event_queries.append((query, limit))
        return self.event_ids[:limit]


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
        candidate.candidate_kind is LinkCandidateKind.EVENT for candidate in result.event_candidates
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


def test_normalized_linking_keys_preserve_unicode_letters_and_ascii_boundaries() -> None:
    assert linking_module.normalized_linking_key(" Café_東京 42 ") == "café 東京 42"
    assert linking_module.normalized_linking_key("ＡＢＣ") == "abc"


def test_distinct_unicode_entity_names_do_not_get_an_exact_direct_match() -> None:
    source, existing, _ = _fixture_records()
    unicode_source = source.model_copy(
        update={
            "content": "source event",
            "entities": [source.entities[0].model_copy(update={"name": "北京"})],
            "metadata": {},
        }
    )
    unicode_target = existing[0].model_copy(
        update={
            "content": "target event",
            "entities": [source.entities[0].model_copy(update={"name": "上海"})],
            "metadata": {},
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=unicode_source,
            existing=[unicode_target],
            max_entity_candidates=1,
            max_event_candidates=1,
            min_embedding_similarity=0.5,
        )
    )

    assert result.entity_candidates == []


def test_distinct_unicode_event_contents_do_not_get_an_exact_direct_match() -> None:
    source, existing, _ = _fixture_records()
    unicode_source = source.model_copy(
        update={"content": "今天下雨", "entities": [], "metadata": {}}
    )
    unicode_target = existing[0].model_copy(
        update={"content": "明天晴天", "entities": [], "metadata": {}}
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=unicode_source,
            existing=[unicode_target],
            max_entity_candidates=1,
            max_event_candidates=1,
            min_embedding_similarity=0.5,
        )
    )

    assert result.event_candidates == []


def test_empty_normalized_keys_do_not_bypass_embedding_thresholds() -> None:
    source, existing, _ = _fixture_records()
    punctuation_source = source.model_copy(
        update={
            "content": "!!!",
            "entities": [source.entities[0].model_copy(update={"name": "???"})],
            "metadata": {"entity_aliases": {"???": ["..."]}},
        }
    )
    punctuation_target = existing[0].model_copy(
        update={
            "content": "...",
            "entities": [source.entities[0].model_copy(update={"name": "---"})],
            "metadata": {"entity_aliases": {"---": ["!!!"]}},
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=punctuation_source,
            existing=[punctuation_target],
            max_entity_candidates=1,
            max_event_candidates=1,
            min_embedding_similarity=0.5,
        )
    )

    assert result.entity_candidates == []
    assert result.event_candidates == []


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
    event_target_ids = {candidate.target_memory.memory_id for candidate in result.event_candidates}

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


def test_injected_embedding_index_retrieves_semantic_gold_beyond_uuid_fallback() -> None:
    source, existing, _ = _fixture_records()
    smaller_fallback = existing[0].model_copy(
        update={
            "memory_id": UUID("00000000-0000-4000-8000-000000000010"),
            "content": "Carrie quartz.",
            "entities": [source.entities[0].model_copy(update={"name": "Carrie Zephyr"})],
            "event_time": source.event_time,
            "valid_from": source.event_time,
            "metadata": {},
        }
    )
    semantic_gold = smaller_fallback.model_copy(
        update={
            "memory_id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            "content": "Nebula cobalt.",
            "entities": [source.entities[0].model_copy(update={"name": "Cora"})],
        }
    )
    entity_ref = linking_module.EntityCandidateTargetRef(
        memory_id=semantic_gold.memory_id,
        entity_position=0,
    )
    candidate_index = FakeEmbeddingCandidateIndex(
        entity_refs=[entity_ref],
        event_ids=[semantic_gold.memory_id],
    )

    result = LinkCandidateGenerator(
        DeterministicFakeEmbeddingModel(),
        candidate_index=candidate_index,
    ).generate(
        CandidateGenerationRequest(
            source=source,
            existing=[smaller_fallback, semantic_gold],
            max_entity_candidates=1,
            max_event_candidates=1,
            min_embedding_similarity=-1.0,
        )
    )

    assert result.entity_candidates[0].target_memory.memory_id == semantic_gold.memory_id
    assert result.event_candidates[0].target_memory.memory_id == semantic_gold.memory_id
    assert "embedding_index_candidate" in result.entity_candidates[0].reasons
    assert "embedding_index_candidate" in result.event_candidates[0].reasons
    assert candidate_index.entity_queries == [(source.entities[0].name, 1)]
    assert candidate_index.event_queries == [(source.content, 1)]


def test_embedding_index_results_are_revalidated_against_request_scope_and_time() -> None:
    source, existing, _ = _fixture_records()
    base = existing[0].model_copy(
        update={
            "content": "Quartz zephyr.",
            "entities": [source.entities[0].model_copy(update={"name": "Zed"})],
            "event_time": source.event_time,
            "valid_from": source.event_time,
            "metadata": {},
        }
    )
    valid = base.model_copy(update={"memory_id": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1")})
    other_user = base.model_copy(
        update={
            "memory_id": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2"),
            "user_id": "u2",
        }
    )
    other_tenant = base.model_copy(
        update={
            "memory_id": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee3"),
            "tenant_id": "tenant-2",
        }
    )
    deleted = base.model_copy(
        update={
            "memory_id": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee4"),
            "status": MemoryStatus.DELETED,
        }
    )
    outside_window = base.model_copy(
        update={
            "memory_id": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee5"),
            "event_time": source.event_time + timedelta(days=2),
            "valid_from": source.event_time + timedelta(days=2),
        }
    )
    indexed = [other_user, other_tenant, deleted, valid]
    candidate_index = FakeEmbeddingCandidateIndex(
        entity_refs=[
            linking_module.EntityCandidateTargetRef(
                memory_id=target.memory_id,
                entity_position=0,
            )
            for target in indexed
        ],
        event_ids=[
            other_user.memory_id,
            other_tenant.memory_id,
            deleted.memory_id,
            outside_window.memory_id,
            valid.memory_id,
        ],
    )

    result = LinkCandidateGenerator(
        DeterministicFakeEmbeddingModel(),
        candidate_index=candidate_index,
    ).generate(
        CandidateGenerationRequest(
            source=source,
            existing=[*indexed, outside_window],
            max_entity_candidates=4,
            max_event_candidates=5,
            event_time_window_days=0,
            min_embedding_similarity=-1.0,
        )
    )

    assert {candidate.target_memory.memory_id for candidate in result.entity_candidates} == {
        valid.memory_id
    }
    assert {candidate.target_memory.memory_id for candidate in result.event_candidates} == {
        valid.memory_id
    }


def test_invalid_entity_index_results_still_consume_global_query_budget() -> None:
    source, _, _ = _fixture_records()
    source_with_many_entities = source.model_copy(
        update={
            "entities": [
                source.entities[0].model_copy(update={"name": f"query entity {index}"})
                for index in range(20)
            ],
            "metadata": {},
        }
    )
    candidate_index = FakeEmbeddingCandidateIndex(
        entity_refs=[
            linking_module.EntityCandidateTargetRef(
                memory_id=UUID(int=index + 20_000),
                entity_position=0,
            )
            for index in range(4)
        ]
    )

    result = LinkCandidateGenerator(
        DeterministicFakeEmbeddingModel(),
        candidate_index=candidate_index,
    ).generate(
        CandidateGenerationRequest(
            source=source_with_many_entities,
            existing=[],
            max_entity_candidates=4,
            max_event_candidates=1,
        )
    )

    assert result.entity_comparison_count == 0
    assert len(candidate_index.entity_queries) == 1
    assert candidate_index.entity_queries[0][1] == 4


def test_duplicate_same_name_entity_occurrences_have_unique_candidate_ids() -> None:
    source, existing, _ = _fixture_records()
    duplicate_source = source.model_copy(
        update={"entities": [source.entities[0], source.entities[0].model_copy()]}
    )
    target = existing[0].model_copy(
        update={
            "entities": [source.entities[0], source.entities[0].model_copy()],
            "metadata": {},
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=duplicate_source,
            existing=[target],
            max_entity_candidates=4,
            max_event_candidates=1,
        )
    )
    candidate_ids = [candidate.candidate_id for candidate in result.entity_candidates]

    assert len(candidate_ids) == 4
    assert len(set(candidate_ids)) == 4


def test_inactive_sources_short_circuit_before_index_or_embedding_calls() -> None:
    source, existing, _ = _fixture_records()
    target = existing[0]

    for status in (
        MemoryStatus.DELETED,
        MemoryStatus.SUPERSEDED,
        MemoryStatus.REJECTED,
    ):
        inactive_source = source.model_copy(
            update={
                "status": status,
                "superseded_by": (target.memory_id if status is MemoryStatus.SUPERSEDED else None),
            }
        )
        candidate_index = FakeEmbeddingCandidateIndex(
            entity_refs=[
                linking_module.EntityCandidateTargetRef(
                    memory_id=target.memory_id,
                    entity_position=0,
                )
            ],
            event_ids=[target.memory_id],
        )
        embedding_model = CountingEmbeddingModel()

        result = LinkCandidateGenerator(
            embedding_model,
            candidate_index=candidate_index,
        ).generate(
            CandidateGenerationRequest(
                source=inactive_source,
                existing=[target],
                max_entity_candidates=1,
                max_event_candidates=1,
            )
        )

        assert result.entity_candidates == []
        assert result.event_candidates == []
        assert result.entity_comparison_count == 0
        assert result.event_comparison_count == 0
        assert result.embedding_model_id == embedding_model.model_id
        assert result.latency_ms >= 0.0
        assert candidate_index.entity_queries == []
        assert candidate_index.event_queries == []
        assert embedding_model.calls == 0


def test_fallback_reason_does_not_claim_corpus_wide_embedding_retrieval() -> None:
    source, existing, _ = _fixture_records()
    unrelated = existing[0].model_copy(
        update={
            "content": "Quartz zephyr.",
            "entities": [source.entities[0].model_copy(update={"name": "Zed"})],
            "metadata": {},
        }
    )

    result = LinkCandidateGenerator(DeterministicFakeEmbeddingModel()).generate(
        CandidateGenerationRequest(
            source=source,
            existing=[unrelated],
            max_entity_candidates=1,
            max_event_candidates=1,
            min_embedding_similarity=-1.0,
        )
    )

    for candidate in [*result.entity_candidates, *result.event_candidates]:
        assert "stable_fallback" in candidate.reasons
        assert "embedding_similarity" in candidate.reasons
        assert "embedding_candidate" not in candidate.reasons
        assert "embedding_index_candidate" not in candidate.reasons


def test_common_token_selection_work_is_globally_bounded() -> None:
    source, existing, _ = _fixture_records()
    source_with_common_entities = source.model_copy(
        update={
            "content": "common source event",
            "entities": [
                source.entities[0].model_copy(update={"name": f"common source {index}"})
                for index in range(20)
            ],
            "metadata": {},
        }
    )
    template = existing[0].model_copy(
        update={
            "event_time": source.event_time,
            "valid_from": source.event_time,
            "metadata": {},
        }
    )
    targets = [
        template.model_copy(
            update={
                "memory_id": UUID(int=index + 1_000),
                "content": f"common target event {index}",
                "entities": [
                    source.entities[0].model_copy(update={"name": f"common target {index}"})
                ],
            }
        )
        for index in range(1_000)
    ]
    model = CountingEmbeddingModel()

    result = LinkCandidateGenerator(model).generate(
        CandidateGenerationRequest(
            source=source_with_common_entities,
            existing=targets,
            max_entity_candidates=4,
            max_event_candidates=3,
            min_embedding_similarity=-1.0,
        )
    )

    assert result.entity_comparison_count == 4
    assert result.event_comparison_count == 3
    assert model.calls == 1
    assert len(model.embedded_texts) <= 2 * (4 + 3)
