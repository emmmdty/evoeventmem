from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from evoeventmem.core.ports import EmbeddingResponse
from evoeventmem.domain.models import (
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
)
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.retrieval import (
    ALL_SOURCES,
    FIXED_HYBRID_WEIGHTS,
    FIXED_VECTOR_WEIGHTS,
    QEMR_WEIGHT_PROFILES,
    QEMRRetrievalResult,
    RetrievalHarness,
    RetrievalService,
    RetrievalStrategy,
    resolve_weights,
)
from evoeventmem.router import QueryIntent


class _FixedEmbeddingModel:
    """Deterministic embedding stub; dense scores are fully controlled per text."""

    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self.vectors = vectors
        self.model_id = "test-fixed-embedding"

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResponse]:
        return [
            EmbeddingResponse(vector=self.vectors.get(text, (1.0,)), model_id=self.model_id)
            for text in texts
        ]


def _memory(
    *,
    content: str,
    kind: MemoryKind = MemoryKind.FACT,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    evidence_id: str | None = None,
    event_time: datetime | None = None,
    valid_from: datetime | None = None,
    superseded_by: UUID | None = None,
    entities: list[dict[str, str]] | None = None,
    relations: list[dict[str, str | float]] | None = None,
    memory_id: UUID | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id or uuid4(),
        user_id="u1",
        memory_kind=kind,
        content=content,
        status=status,
        evidence_refs=[EvidenceRef(source_type="turn", source_id=evidence_id or content)],
        event_time=event_time,
        valid_from=valid_from,
        superseded_by=superseded_by,
        entities=entities or [],
        relations=relations or [],
    )


def _harness(
    memories: list[MemoryRecord],
    *,
    vectors: dict[str, tuple[float, ...]] | None = None,
    default_budget_tokens: int = 200,
    max_items_per_source: int = 4,
    max_candidates_per_source: int | None = None,
) -> RetrievalHarness:
    repository = InMemoryMemoryRepository()
    for memory in memories:
        repository.add(memory)
    embedding = _FixedEmbeddingModel(vectors or {})
    return RetrievalHarness(
        repository,
        embedding,
        default_budget_tokens=default_budget_tokens,
        max_items_per_source=max_items_per_source,
        max_candidates_per_source=max_candidates_per_source,
    )


def test_fixed_vector_weights_use_dense_only() -> None:
    weights = resolve_weights(RetrievalStrategy.FIXED_VECTOR, QueryIntent.TEMPORAL)
    assert weights == FIXED_VECTOR_WEIGHTS
    assert set(weights) == set(ALL_SOURCES)
    assert weights["dense"] == 1.0
    assert all(weight == 0.0 for source, weight in weights.items() if source != "dense")


def test_fixed_hybrid_weights_are_equal_and_intent_independent() -> None:
    weights = resolve_weights(RetrievalStrategy.FIXED_HYBRID, QueryIntent.TEMPORAL)
    assert weights == FIXED_HYBRID_WEIGHTS
    assert len({weight for weight in weights.values()}) == 1


def test_qemr_weights_are_hand_set_and_differ_by_intent() -> None:
    assert set(QEMR_WEIGHT_PROFILES) == set(QueryIntent)
    for _intent, profile in QEMR_WEIGHT_PROFILES.items():
        assert set(profile) <= set(ALL_SOURCES)
        assert all(weight >= 0.0 for weight in profile.values())
    assert QEMR_WEIGHT_PROFILES[QueryIntent.NO_MEMORY] == {}
    temporal = resolve_weights(RetrievalStrategy.QEMR, QueryIntent.TEMPORAL)
    semantic = resolve_weights(RetrievalStrategy.QEMR, QueryIntent.SEMANTIC)
    assert temporal["temporal"] == 1.0
    assert semantic["dense"] == 1.0
    assert temporal != semantic


def test_harness_runs_all_three_strategies_on_one_input() -> None:
    memory = _memory(
        content="Caroline's favorite color is teal.",
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([memory])
    query = "What is Caroline's favorite color?"
    for strategy in RetrievalStrategy:
        result = harness.retrieve(query, user_id="u1", strategy=strategy)
        assert result.strategy is strategy
        assert result.total_tokens <= result.budget_tokens
        assert result.selected_context
        assert result.routing is not None


def test_fixed_vector_ignores_non_dense_sources() -> None:
    procedure = _memory(
        content="To create a memory, call the write endpoint with evidence.",
        kind=MemoryKind.PROCEDURE,
    )
    fact = _memory(
        content="Caroline's favorite color is teal.",
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    vectors = {
        "How do I create a memory in evoeventmem?": (1.0, 0.0),
        procedure.content: (0.0, 1.0),
        fact.content: (0.9, 0.0),
    }
    harness = _harness([procedure, fact], vectors=vectors)
    query = "How do I create a memory in evoeventmem?"

    qemr = harness.retrieve(query, user_id="u1", strategy=RetrievalStrategy.QEMR)
    assert qemr.intent is QueryIntent.PROCEDURAL
    assert qemr.selected_context[0].memory.memory_id == procedure.memory_id

    vector = harness.retrieve(query, user_id="u1", strategy=RetrievalStrategy.FIXED_VECTOR)
    assert vector.selected_context[0].memory.memory_id == fact.memory_id


def test_selected_context_never_exceeds_budget() -> None:
    memories = [_memory(content=f"memory number {index} with enough tokens.") for index in range(5)]
    harness = _harness(memories, default_budget_tokens=3)
    result = harness.retrieve(
        "What is Caroline's favorite color?",
        user_id="u1",
        budget_tokens=3,
    )
    assert result.total_tokens <= 3
    assert all(item.token_count <= 3 for item in result.selected_context)
    assert any(exclusion.reason == "budget_exceeded" for exclusion in result.exclusions)


def test_every_packed_item_has_evidence_and_score_decomposition() -> None:
    memory = _memory(content="Caroline's favorite color is teal.")
    harness = _harness([memory])
    result = harness.retrieve("What is Caroline's favorite color?", user_id="u1")
    assert result.selected_context
    for item in result.selected_context:
        assert item.evidence_refs, "packed item must cite source evidence"
        assert item.component_scores, "packed item must expose a score decomposition"
        assert item.final_score >= 0.0
        assert item.reason
        assert item.token_count >= 1


def test_superseded_memory_is_not_a_current_fact_for_semantic_queries() -> None:
    current = _memory(
        content="Caroline lives in Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    stale = _memory(
        content="Caroline lived in Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        status=MemoryStatus.SUPERSEDED,
        superseded_by=current.memory_id,
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([current, stale])
    result = harness.retrieve("What is Caroline's favorite color?", user_id="u1")
    selected_ids = {item.memory.memory_id for item in result.selected_context}
    assert stale.memory_id not in selected_ids
    assert any(
        exclusion.reason == "superseded_memory_not_current_fact"
        and exclusion.memory_id == stale.memory_id
        for exclusion in result.exclusions
    )


def test_superseded_memory_retrieved_as_historical_for_temporal_queries() -> None:
    current = _memory(
        content="Caroline lives in Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    stale = _memory(
        content="Caroline lived in Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        status=MemoryStatus.SUPERSEDED,
        superseded_by=current.memory_id,
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([current, stale])
    result = harness.retrieve("When did Caroline move to Seattle?", user_id="u1")
    assert result.intent is QueryIntent.TEMPORAL
    historical = [
        item for item in result.selected_context if item.memory.memory_id == stale.memory_id
    ]
    assert historical, "superseded memory must be retrievable as history"
    assert historical[0].historical is True
    assert historical[0].reason == "packed as historical memory with superseded penalty"
    assert any(
        item.memory.status is not MemoryStatus.SUPERSEDED or item.historical
        for item in result.selected_context
    )


def test_no_memory_intent_returns_empty_context_with_observable_reason() -> None:
    memory = _memory(content="Caroline's favorite color is teal.")
    harness = _harness([memory])
    result = harness.retrieve("Hello!", user_id="u1")
    assert result.intent is QueryIntent.NO_MEMORY
    assert result.selected_context == []
    assert result.total_tokens == 0
    assert any(
        exclusion.reason == "no_memory_intent" and exclusion.memory_id == memory.memory_id
        for exclusion in result.exclusions
    )


def test_evidence_coverage_bonus_prefers_new_evidence_over_duplicate() -> None:
    first = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
    )
    duplicate = _memory(
        content="gamma delta",
        evidence_id="evidence-1",
        valid_from=datetime(2024, 1, 2, tzinfo=UTC),
    )
    novel = _memory(
        content="epsilon zeta",
        evidence_id="evidence-2",
        valid_from=datetime(2024, 1, 3, tzinfo=UTC),
    )
    vectors = {
        "query": (1.0, 0.0),
        first.content: (1.0, 0.0),
        duplicate.content: (0.9, 0.435889894354067),
        novel.content: (0.86, 0.5099019513592785),
    }
    harness = _harness([first, duplicate, novel], vectors=vectors, default_budget_tokens=4)
    result = harness.retrieve("query", user_id="u1", strategy=RetrievalStrategy.FIXED_VECTOR)
    selected_ids = [item.memory.memory_id for item in result.selected_context]
    assert selected_ids[0] == first.memory_id
    assert novel.memory_id in selected_ids
    assert duplicate.memory_id not in selected_ids
    assert any(
        exclusion.reason == "not_selected_by_packing" and exclusion.memory_id == duplicate.memory_id
        for exclusion in result.exclusions
    )


def test_source_diversity_cap_excludes_oversubscribed_source() -> None:
    dense_a = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        entities=[{"name": "Alpha", "role": "subject"}],
        memory_id=UUID("10000000-0000-0000-0000-000000000001"),
    )
    dense_b = _memory(
        content="gamma delta",
        evidence_id="evidence-2",
        entities=[{"name": "Gamma", "role": "subject"}],
        memory_id=UUID("10000000-0000-0000-0000-000000000002"),
    )
    graph = _memory(
        content="epsilon zeta",
        evidence_id="evidence-3",
        entities=[{"name": "Epsilon", "role": "subject"}],
        memory_id=UUID("10000000-0000-0000-0000-000000000003"),
    )
    vectors = {
        "epsilon": (1.0, 0.0),
        dense_a.content: (1.0, 0.0),
        dense_b.content: (0.9, 0.435889894354067),
        graph.content: (0.5, 0.8660254037844386),
    }
    harness = _harness(
        [dense_a, dense_b, graph],
        vectors=vectors,
        max_items_per_source=1,
    )
    result = harness.retrieve("epsilon", user_id="u1", strategy=RetrievalStrategy.FIXED_HYBRID)
    selected_ids = {item.memory.memory_id for item in result.selected_context}
    assert dense_a.memory_id in selected_ids
    assert graph.memory_id in selected_ids
    assert dense_b.memory_id not in selected_ids
    assert any(
        exclusion.reason == "source_diversity_cap" and exclusion.memory_id == dense_b.memory_id
        for exclusion in result.exclusions
    )


def test_scores_are_normalized_per_source_max() -> None:
    anchored = _memory(
        content="Caroline lives in Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    far = _memory(
        content="Caroline lived in Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([anchored, far])
    result = harness.retrieve("When did Caroline move to Seattle?", user_id="u1")
    scores = {
        item.memory.memory_id: item.component_scores for item in result.selected_context
    }
    assert scores[anchored.memory_id]["temporal"] == 1.0
    assert scores[far.memory_id]["temporal"] < 1.0
    assert scores[far.memory_id]["temporal"] > 0.0


def test_result_model_rejects_budget_overflow() -> None:
    harness = _harness([_memory(content="Caroline's favorite color is teal.")])
    result = harness.retrieve("What is Caroline's favorite color?", user_id="u1")
    assert result.selected_context
    payload = result.model_dump(mode="python")
    payload["selected_context"][0]["token_count"] = 11
    payload["total_tokens"] = 11
    payload["budget_tokens"] = 10
    with pytest.raises(ValueError, match="exceeds the configured token budget"):
        QEMRRetrievalResult.model_validate(payload)


def test_retrieval_service_persists_component_scores_and_exclusions() -> None:
    current = _memory(
        content="Caroline lives in Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
    )
    stale = _memory(
        content="Caroline lived in Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        status=MemoryStatus.SUPERSEDED,
        superseded_by=current.memory_id,
    )
    service = RetrievalService(_harness([current, stale]))
    service.retrieve("When did Caroline move to Seattle?", user_id="u1")
    service.retrieve("Hello!", user_id="u1")

    assert len(service.list_results()) == 2
    exported = service.export_jsonl()
    packed = exported[0]["selected_context"]
    assert packed, "temporal query must pack candidates"
    assert all(item["component_scores"] for item in packed)
    assert all(item["evidence_refs"] for item in packed)
    assert exported[1]["exclusions"], "no-memory query must record exclusion reasons"
    assert exported[1]["selected_context"] == []


def test_weighted_average_final_score_discriminates_without_saturation() -> None:
    multi_facet = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        entities=[{"name": "Epsilon", "role": "subject"}],
        memory_id=UUID("20000000-0000-0000-0000-000000000001"),
    )
    single_facet = _memory(
        content="gamma delta",
        evidence_id="evidence-2",
        memory_id=UUID("20000000-0000-0000-0000-000000000002"),
    )
    vectors = {
        "epsilon": (1.0, 0.0),
        multi_facet.content: (1.0, 0.0),
        single_facet.content: (0.8, 0.6),
    }
    harness = _harness([multi_facet, single_facet], vectors=vectors)
    result = harness.retrieve("epsilon", user_id="u1", strategy=RetrievalStrategy.FIXED_HYBRID)

    assert result.selected_context[0].memory.memory_id == multi_facet.memory_id
    assert result.selected_context[0].final_score == pytest.approx(0.4)
    assert result.selected_context[1].final_score == pytest.approx(0.16)
    assert all(0.0 <= item.final_score <= 1.0 for item in result.selected_context)


def test_reference_time_controls_temporal_scores() -> None:
    old = _memory(
        content="Caroline lived in Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    recent = _memory(
        content="Caroline lives in Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([old, recent])
    query = "When did Caroline move to Seattle?"

    near = harness.retrieve(query, user_id="u1", reference_time=datetime(2023, 6, 1, tzinfo=UTC))
    far = harness.retrieve(query, user_id="u1", reference_time=datetime(2026, 8, 1, tzinfo=UTC))
    near_old = _component(near, old.memory_id, "temporal")
    far_old = _component(far, old.memory_id, "temporal")
    near_recent = _component(near, recent.memory_id, "temporal")
    far_recent = _component(far, recent.memory_id, "temporal")

    assert near_recent == pytest.approx(1.0)
    assert far_recent == pytest.approx(1.0)
    assert 0.0 < near_old < near_recent
    assert 0.0 < far_old < far_recent
    assert far_old > near_old

    default = harness.retrieve(query, user_id="u1")
    assert default.total_tokens <= default.budget_tokens


def test_candidate_cap_truncates_and_records_exclusion() -> None:
    memories = [
        _memory(
            content=f"candidate memory number {index}",
            evidence_id=f"cap:{index}",
        )
        for index in range(4)
    ]
    vectors = {
        "query": (1.0, 0.0),
        memories[0].content: (1.0, 0.0),
        memories[1].content: (0.9, 0.435889894354067),
        memories[2].content: (0.8, 0.6),
        memories[3].content: (0.7, 0.714142842854285),
    }
    harness = _harness(memories, vectors=vectors, max_candidates_per_source=2)
    result = harness.retrieve("query", user_id="u1", strategy=RetrievalStrategy.FIXED_VECTOR)

    scored_ids = {item.memory.memory_id for item in result.candidates}
    assert memories[0].memory_id in scored_ids
    assert memories[1].memory_id in scored_ids
    assert memories[2].memory_id not in scored_ids
    assert memories[3].memory_id not in scored_ids
    assert any(
        exclusion.reason == "candidate_cap_reached"
        and exclusion.memory_id == memories[3].memory_id
        for exclusion in result.exclusions
    )
    assert any(
        exclusion.reason == "candidate_cap_reached"
        and exclusion.memory_id == memories[2].memory_id
        for exclusion in result.exclusions
    )


def _component(result: QEMRRetrievalResult, memory_id: UUID, source: str) -> float:
    for item in result.selected_context:
        if item.memory.memory_id == memory_id:
            return item.component_scores[source]
    raise AssertionError(f"memory {memory_id} not in selected context")
