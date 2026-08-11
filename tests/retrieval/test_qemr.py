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
    RRF_K,
    EvidencePolicy,
    QEMRRetrievalResult,
    RetrievalControls,
    RetrievalHarness,
    RetrievalService,
    RetrievalStrategy,
    RoutingMode,
    WeightProfile,
    resolve_weights,
)
from evoeventmem.router import QueryIntent, QueryRoutingDecision, TemporalOperator
from evoeventmem.tokenization import DeterministicTokenEstimator


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
    embedding: _FixedEmbeddingModel | None = None,
) -> RetrievalHarness:
    repository = InMemoryMemoryRepository()
    for memory in memories:
        repository.add(memory)
    embedding_model = embedding or _FixedEmbeddingModel(vectors or {})
    return RetrievalHarness(
        repository,
        embedding_model,
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
    harness = _harness(memories, default_budget_tokens=400)
    result = harness.retrieve(
        "What is Caroline's favorite color?",
        user_id="u1",
        budget_tokens=200,
        strategy=RetrievalStrategy.FIXED_HYBRID,
    )
    assert result.total_tokens <= 200
    assert result.budget.total_input_tokens_estimate <= 200
    assert len(result.selected_context) >= 1


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
    harness = _harness([first, duplicate, novel], vectors=vectors, default_budget_tokens=120)
    result = harness.retrieve("query", user_id="u1", strategy=RetrievalStrategy.FIXED_VECTOR)
    selected_ids = [item.memory.memory_id for item in result.selected_context]
    assert selected_ids[0] == first.memory_id
    assert novel.memory_id in selected_ids
    assert duplicate.memory_id not in selected_ids
    assert any(
        exclusion.reason == "budget_exceeded" and exclusion.memory_id == duplicate.memory_id
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


def test_temporal_scores_are_small_capped_feature_for_unconstrained_when() -> None:
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
    assert scores[anchored.memory_id]["temporal"] == pytest.approx(0.2 / (RRF_K + 1.0))
    assert 0.0 < scores[far.memory_id]["temporal"] < scores[anchored.memory_id]["temporal"]


def test_result_model_rejects_budget_overflow() -> None:
    harness = _harness([_memory(content="Caroline's favorite color is teal.")])
    result = harness.retrieve("What is Caroline's favorite color?", user_id="u1")
    assert result.selected_context
    payload = result.model_dump(mode="python")
    payload["selected_context"][0]["token_count"] = 11
    payload["total_tokens"] = 11
    payload["budget_tokens"] = 10
    payload["budget"]["content_tokens"] = 9
    payload["budget"]["prompt_overhead_tokens"] = 2
    payload["budget"]["total_input_tokens_estimate"] = 11
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


def test_wrrf_final_scores_discriminate_without_saturation() -> None:
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
    assert result.selected_context[0].final_score == pytest.approx(2.0 / (RRF_K + 1.0) / 5.0)
    assert result.selected_context[1].final_score == pytest.approx(1.0 / (RRF_K + 2.0) / 5.0)
    assert all(0.0 <= item.final_score <= 1.0 for item in result.selected_context)


def test_single_candidate_source_gets_no_artificial_authority() -> None:
    dense_strong = _memory(
        content="Caroline lives in Seattle.",
        entities=[{"name": "Priya", "role": "subject"}],
        memory_id=UUID("40000000-0000-0000-0000-000000000001"),
    )
    weak_solo = _memory(
        content="Company annual report.",
        entities=[{"name": "Caroline", "role": "subject"}],
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        memory_id=UUID("40000000-0000-0000-0000-000000000002"),
    )
    vectors = {
        "Where does Caroline live?": (1.0, 0.0),
        dense_strong.content: (0.9, 0.435889894354067),
        weak_solo.content: (0.2, 0.9797958971132712),
    }
    harness = _harness([dense_strong, weak_solo], vectors=vectors)
    result = harness.retrieve("Where does Caroline live?", user_id="u1")
    assert result.intent is QueryIntent.SEMANTIC
    by_id = {item.memory.memory_id: item for item in result.candidates}
    graph = next(
        score
        for score in by_id[weak_solo.memory_id].source_scores
        if score.source.value == "graph"
    )
    assert graph.rank == 1
    assert graph.fusion_contribution == pytest.approx(0.3 / (RRF_K + 1.0))
    dense_best = next(
        score
        for score in by_id[dense_strong.memory_id].source_scores
        if score.source.value == "dense"
    )
    assert dense_best.rank == 1
    assert dense_best.fusion_contribution == pytest.approx(1.0 / (RRF_K + 1.0))
    assert graph.fusion_contribution < dense_best.fusion_contribution


def test_wrrf_persists_raw_rank_weight_and_contribution() -> None:
    moved = _memory(
        content="Caroline moved to Lisbon.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("40000000-0000-0000-0000-000000000011"),
    )
    ended = _memory(
        content="Project Zephyr ended.",
        valid_from=datetime(2024, 12, 1, tzinfo=UTC),
        entities=[{"name": "Zephyr", "role": "subject"}],
        memory_id=UUID("40000000-0000-0000-0000-000000000012"),
    )
    vectors = {
        "When did Caroline move?": (1.0, 0.0),
        moved.content: (0.95, 0.3122498999199199),
        ended.content: (0.1, 0.99498743710662),
    }
    harness = _harness([moved, ended], vectors=vectors)
    result = harness.retrieve("When did Caroline move?", user_id="u1")
    by_id = {item.memory.memory_id: item for item in result.candidates}
    assert by_id[moved.memory_id].source_scores
    for score in by_id[moved.memory_id].source_scores:
        assert score.raw_score > 0.0
        assert score.rank >= 1
        assert score.weight > 0.0
        assert score.fusion_contribution == pytest.approx(
            score.weight / (RRF_K + score.rank)
        )
    ended_dense = next(
        score
        for score in by_id[ended.memory_id].source_scores
        if score.source.value == "dense"
    )
    assert ended_dense.rank == 2
    assert ended_dense.fusion_contribution == pytest.approx(0.3 / (RRF_K + 2.0))
    ended_temporal = next(
        score
        for score in by_id[ended.memory_id].source_scores
        if score.source.value == "temporal"
    )
    assert ended_temporal.rank == 1
    assert ended_temporal.fusion_contribution == pytest.approx(0.2 / (RRF_K + 1.0))


def test_wrrf_tie_breaking_is_stable_and_deterministic() -> None:
    lower_id = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        memory_id=UUID("40000000-0000-0000-0000-000000000021"),
    )
    higher_id = _memory(
        content="gamma delta",
        evidence_id="evidence-2",
        memory_id=UUID("40000000-0000-0000-0000-000000000022"),
    )
    vectors = {
        "query": (1.0, 0.0),
        lower_id.content: (0.5, 0.0),
        higher_id.content: (0.5, 0.0),
    }
    harness = _harness([lower_id, higher_id], vectors=vectors)
    first = harness.retrieve("query", user_id="u1")
    second = harness.retrieve("query", user_id="u1")
    for result in (first, second):
        by_id = {item.memory.memory_id: item for item in result.candidates}
        lower_dense = next(
            score
            for score in by_id[lower_id.memory_id].source_scores
            if score.source.value == "dense"
        )
        higher_dense = next(
            score
            for score in by_id[higher_id.memory_id].source_scores
            if score.source.value == "dense"
        )
        assert lower_dense.rank == 1
        assert higher_dense.rank == 2
        ranked_ids = [item.memory.memory_id for item in result.selected_context]
        assert ranked_ids[0] == lower_id.memory_id


def test_reference_time_controls_temporal_ranks() -> None:
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

    near_old_ref = harness.retrieve(
        query,
        user_id="u1",
        reference_time=datetime(2021, 6, 1, tzinfo=UTC),
    )
    far_old_ref = harness.retrieve(
        query,
        user_id="u1",
        reference_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    near_old = _component(near_old_ref, old.memory_id, "temporal")
    far_old = _component(far_old_ref, old.memory_id, "temporal")
    near_recent = _component(near_old_ref, recent.memory_id, "temporal")
    far_recent = _component(far_old_ref, recent.memory_id, "temporal")

    assert near_old == pytest.approx(0.2 / (RRF_K + 1.0))
    assert near_recent == pytest.approx(0.2 / (RRF_K + 2.0))
    assert far_old == pytest.approx(0.2 / (RRF_K + 2.0))
    assert far_recent == pytest.approx(0.2 / (RRF_K + 1.0))
    assert near_old > far_old

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


def test_unrelated_newest_memory_cannot_beat_older_relevant_memory_when_unconstrained() -> None:
    relevant_old = _memory(
        content="Caroline moved to Lisbon in March 2021.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000001"),
    )
    unrelated_new = _memory(
        content="The team launched project Zephyr.",
        valid_from=datetime(2024, 12, 1, tzinfo=UTC),
        entities=[{"name": "Zephyr", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000002"),
    )
    vectors = {
        "When did Caroline move?": (1.0, 0.0, 0.0),
        relevant_old.content: (0.95, 0.0, 0.0),
        unrelated_new.content: (0.1, 0.9, 0.0),
    }
    harness = _harness([relevant_old, unrelated_new], vectors=vectors)
    result = harness.retrieve("When did Caroline move?", user_id="u1")
    assert result.intent is QueryIntent.TEMPORAL
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert ranked_ids[0] == relevant_old.memory_id, (
        "relevant older memory must rank above unrelated newest memory"
    )


def test_latest_ordering_applies_only_within_relevant_pool() -> None:
    relevant_recent = _memory(
        content="Caroline moved to Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000011"),
    )
    relevant_old = _memory(
        content="Caroline moved to Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000012"),
    )
    unrelated_recent = _memory(
        content="David bought new shoes.",
        valid_from=datetime(2024, 12, 1, tzinfo=UTC),
        entities=[{"name": "David", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000013"),
    )
    vectors = {
        "When did Caroline last move?": (1.0, 0.0),
        relevant_recent.content: (0.9, 0.0),
        relevant_old.content: (0.85, 0.0),
        unrelated_recent.content: (0.0, 1.0),
    }
    harness = _harness([relevant_recent, relevant_old, unrelated_recent], vectors=vectors)
    result = harness.retrieve(
        "When did Caroline last move?",
        user_id="u1",
        reference_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert ranked_ids[0] == relevant_recent.memory_id
    assert ranked_ids[1] == relevant_old.memory_id
    assert unrelated_recent.memory_id not in ranked_ids


def test_earliest_orders_only_relevant_pool() -> None:
    relevant_old = _memory(
        content="Caroline's first trip to Lisbon.",
        valid_from=datetime(2019, 6, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000021"),
    )
    relevant_recent = _memory(
        content="Caroline's second trip to Lisbon.",
        valid_from=datetime(2022, 6, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000022"),
    )
    unrelated_recent = _memory(
        content="Priya adopted a cat.",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        entities=[{"name": "Priya", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000023"),
    )
    vectors = {
        "When was Caroline's earliest trip?": (1.0, 0.0),
        relevant_old.content: (0.9, 0.0),
        relevant_recent.content: (0.85, 0.0),
        unrelated_recent.content: (0.0, 1.0),
    }
    harness = _harness([relevant_old, relevant_recent, unrelated_recent], vectors=vectors)
    result = harness.retrieve(
        "When was Caroline's earliest trip?",
        user_id="u1",
        reference_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert ranked_ids[0] == relevant_old.memory_id
    assert unrelated_recent.memory_id not in ranked_ids


def test_before_after_between_use_interval_agreement() -> None:
    in_range = _memory(
        content="The merger closed.",
        valid_from=datetime(2021, 6, 15, tzinfo=UTC),
        memory_id=UUID("30000000-0000-0000-0000-000000000031"),
    )
    before_range = _memory(
        content="The merger was proposed.",
        valid_from=datetime(2019, 2, 1, tzinfo=UTC),
        memory_id=UUID("30000000-0000-0000-0000-000000000032"),
    )
    vectors = {
        "What happened between 2020 and 2022?": (1.0, 0.0),
        in_range.content: (0.9, 0.0),
        before_range.content: (0.8, 0.0),
    }
    harness = _harness([in_range, before_range], vectors=vectors)
    result = harness.retrieve(
        "What happened between 2020 and 2022?",
        user_id="u1",
        reference_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert ranked_ids[0] == in_range.memory_id
    assert before_range.memory_id not in ranked_ids
    assert any(
        exclusion.reason == "temporal_interval_excluded"
        and exclusion.memory_id == before_range.memory_id
        for exclusion in result.exclusions
    ), "out-of-range memories must be excluded with an observable reason"


def test_after_filters_out_events_before_bound() -> None:
    early = _memory(
        content="Company founded.",
        valid_from=datetime(2018, 1, 1, tzinfo=UTC),
        memory_id=UUID("30000000-0000-0000-0000-000000000041"),
    )
    late = _memory(
        content="Company IPO.",
        valid_from=datetime(2023, 1, 1, tzinfo=UTC),
        memory_id=UUID("30000000-0000-0000-0000-000000000042"),
    )
    vectors = {
        "What happened after 2020?": (1.0, 0.0),
        early.content: (0.85, 0.0),
        late.content: (0.9, 0.0),
    }
    harness = _harness([early, late], vectors=vectors)
    result = harness.retrieve(
        "What happened after 2020?",
        user_id="u1",
        reference_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert late.memory_id in ranked_ids
    assert early.memory_id not in ranked_ids
    assert any(
        exclusion.reason == "temporal_interval_excluded"
        and exclusion.memory_id == early.memory_id
        for exclusion in result.exclusions
    ), "events before the after-bound must be excluded with an observable reason"


def test_fixed_vector_results_remain_unchanged_with_temporal_sources_present() -> None:
    old = _memory(
        content="Caroline lived in Austin.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000051"),
    )
    recent = _memory(
        content="Caroline lives in Seattle.",
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("30000000-0000-0000-0000-000000000052"),
    )
    vectors = {
        "What is Caroline's favorite color?": (1.0, 0.0),
        old.content: (0.9, 0.0),
        recent.content: (0.85, 0.0),
    }
    harness = _harness([old, recent], vectors=vectors)
    result = harness.retrieve(
        "What is Caroline's favorite color?",
        user_id="u1",
        strategy=RetrievalStrategy.FIXED_VECTOR,
    )
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert set(ranked_ids) == {old.memory_id, recent.memory_id}
    assert all(
        item.component_scores.get("temporal", 0.0) == 0.0
        for item in result.selected_context
    ), "fixed vector must not apply temporal weights"


def test_fixed_vector_path_keeps_per_source_max_normalization() -> None:
    first = _memory(
        content="Caroline lives in Seattle.",
        entities=[{"name": "Caroline", "role": "subject"}],
        valid_from=datetime(2023, 5, 1, tzinfo=UTC),
        memory_id=UUID("40000000-0000-0000-0000-000000000031"),
    )
    second = _memory(
        content="Project Zephyr ended.",
        entities=[{"name": "Zephyr", "role": "subject"}],
        valid_from=datetime(2024, 12, 1, tzinfo=UTC),
        memory_id=UUID("40000000-0000-0000-0000-000000000032"),
    )
    vectors = {
        "What is Caroline's favorite color?": (1.0, 0.0),
        first.content: (0.9, 0.435889894354067),
        second.content: (0.45, 0.8930296298303741),
    }
    harness = _harness([first, second], vectors=vectors)
    result = harness.retrieve(
        "What is Caroline's favorite color?",
        user_id="u1",
        strategy=RetrievalStrategy.FIXED_VECTOR,
    )
    by_id = {item.memory.memory_id: item for item in result.selected_context}
    assert by_id[first.memory_id].component_scores["dense"] == pytest.approx(1.0)
    assert by_id[second.memory_id].component_scores["dense"] == pytest.approx(0.5)
    ranked_ids = [item.memory.memory_id for item in result.selected_context]
    assert ranked_ids[0] == first.memory_id


def test_sequence_and_duration_constraints_remain_observable() -> None:
    first = _memory(
        content="Caroline flew to Lisbon first.",
        valid_from=datetime(2021, 3, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    second = _memory(
        content="Caroline flew to Porto afterwards.",
        valid_from=datetime(2021, 4, 1, tzinfo=UTC),
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([first, second])
    sequence = harness.retrieve(
        "In what order did Caroline visit Lisbon and Porto?",
        user_id="u1",
    )
    assert sequence.routing is not None
    assert sequence.routing.temporal_constraint.operator is TemporalOperator.SEQUENCE
    assert sequence.routing.temporal_constraint.rule_hits == ["sequence_rule"]
    assert sequence.routing.temporal_constraint.matched_spans
    assert sequence.selected_context

    duration = harness.retrieve("How long did the meeting last?", user_id="u1")
    assert duration.routing is not None
    assert duration.routing.temporal_constraint.operator is TemporalOperator.DURATION
    assert duration.routing.temporal_constraint.rule_hits == ["duration_rule"]
    assert duration.routing.temporal_constraint.matched_spans
    assert duration.selected_context


class _ExplodingEmbedding(_FixedEmbeddingModel):
    """Embedding stub whose dense source always fails."""

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResponse]:
        raise RuntimeError("embedding service unavailable")


class _FlakyGraphHarness(RetrievalHarness):
    """Harness whose graph source always fails."""

    def _graph_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: list[MemoryRecord],
        reference: datetime,
    ) -> list:
        raise RuntimeError("graph store unavailable")


def test_dense_source_failure_records_observable_event() -> None:
    memory = _memory(
        content="Caroline's favorite color is teal.",
        entities=[{"name": "Caroline", "role": "subject"}],
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
    )
    harness = _harness([memory], embedding=_ExplodingEmbedding({}))
    result = harness.retrieve(
        "What is Caroline's favorite color?",
        user_id="u1",
        controls=RetrievalControls(evidence_policy=EvidencePolicy.PROVENANCE_ONLY),
    )
    assert result.selected_context, "remaining sources must still be retrieved"
    assert result.strategy is RetrievalStrategy.QEMR
    dense_failure = next(f for f in result.source_failures if f.source.value == "dense")
    assert dense_failure.reason_code == "dense_source_error"
    assert dense_failure.degraded_policy is EvidencePolicy.PROVENANCE_ONLY
    assert dense_failure.duration_ms >= 0.0


def test_graph_source_failure_records_observable_event() -> None:
    memory = _memory(
        content="alpha beta",
        entities=[{"name": "Caroline", "role": "subject"}],
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
    )
    repository = InMemoryMemoryRepository()
    repository.add(memory)
    embedding = _FixedEmbeddingModel(
        {
            "What is Caroline's favorite color?": (1.0, 0.0),
            memory.content: (0.9, 0.435889894354067),
        }
    )
    harness = _FlakyGraphHarness(repository, embedding)
    result = harness.retrieve("What is Caroline's favorite color?", user_id="u1")
    assert result.selected_context
    assert result.strategy is RetrievalStrategy.QEMR
    graph_failure = next(f for f in result.source_failures if f.source.value == "graph")
    assert graph_failure.reason_code == "graph_source_error"
    assert graph_failure.duration_ms >= 0.0


def test_controls_evidence_policy_changes_packing_decision() -> None:
    covered_first = _memory(
        content="alpha beta",
        evidence_id="shared-evidence",
        memory_id=UUID("50000000-0000-0000-0000-000000000031"),
    )
    covered_second = _memory(
        content="gamma delta",
        evidence_id="shared-evidence",
        memory_id=UUID("50000000-0000-0000-0000-000000000032"),
    )
    novel = _memory(
        content="epsilon zeta",
        evidence_id="novel-evidence",
        memory_id=UUID("50000000-0000-0000-0000-000000000033"),
    )
    vectors = {
        "What is Caroline's favorite color?": (1.0, 0.0),
        covered_first.content: (1.0, 0.0),
        covered_second.content: (0.9, 0.435889894354067),
        novel.content: (0.89, 0.45596052437906664),
    }
    harness = _harness(
        [covered_first, covered_second, novel],
        vectors=vectors,
        default_budget_tokens=300,
    )
    query = "What is Caroline's favorite color?"
    constrained = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(evidence_policy=EvidencePolicy.CONSTRAINED, budget_tokens=300),
    )
    provenance_only = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(
            evidence_policy=EvidencePolicy.PROVENANCE_ONLY,
            budget_tokens=300,
        ),
    )
    constrained_ids = [item.memory.memory_id for item in constrained.selected_context]
    provenance_ids = [item.memory.memory_id for item in provenance_only.selected_context]
    assert constrained_ids.index(novel.memory_id) < constrained_ids.index(covered_second.memory_id)
    assert provenance_ids.index(covered_second.memory_id) < provenance_ids.index(novel.memory_id)
    assert all(item.evidence_refs for item in provenance_only.selected_context)


def test_controls_disable_temporal_changes_selection() -> None:
    in_2024 = _memory(
        content="Project launch.",
        valid_from=datetime(2024, 6, 1, tzinfo=UTC),
        memory_id=UUID("50000000-0000-0000-0000-000000000041"),
    )
    in_2019 = _memory(
        content="Office opened.",
        valid_from=datetime(2019, 3, 1, tzinfo=UTC),
        memory_id=UUID("50000000-0000-0000-0000-000000000042"),
    )
    vectors = {
        "What happened in 2024?": (1.0, 0.0),
        in_2024.content: (0.9, 0.435889894354067),
        in_2019.content: (0.95, 0.3122498999199199),
    }
    harness = _harness([in_2024, in_2019], vectors=vectors)
    query = "What happened in 2024?"
    enabled = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(enable_temporal_source=True),
    )
    disabled = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(enable_temporal_source=False),
    )
    enabled_ids = [item.memory.memory_id for item in enabled.selected_context]
    disabled_ids = [item.memory.memory_id for item in disabled.selected_context]
    assert enabled_ids == [in_2024.memory_id]
    assert in_2019.memory_id in disabled_ids
    assert any(
        exclusion.reason == "temporal_interval_excluded"
        and exclusion.memory_id == in_2019.memory_id
        for exclusion in enabled.exclusions
    )


def test_controls_disable_graph_changes_ranking() -> None:
    entity_memory = _memory(
        content="alpha beta",
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("50000000-0000-0000-0000-000000000051"),
    )
    plain_memory = _memory(
        content="gamma delta",
        memory_id=UUID("50000000-0000-0000-0000-000000000052"),
    )
    vectors = {
        "Who works with Caroline?": (1.0, 0.0),
        entity_memory.content: (0.7, 0.714142842854285),
        plain_memory.content: (1.0, 0.0),
    }
    harness = _harness([entity_memory, plain_memory], vectors=vectors)
    query = "Who works with Caroline?"
    enabled = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(enable_graph_source=True),
    )
    disabled = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(enable_graph_source=False),
    )
    assert [item.memory.memory_id for item in enabled.selected_context][0] == (
        entity_memory.memory_id
    )
    assert [item.memory.memory_id for item in disabled.selected_context][0] == (
        plain_memory.memory_id
    )


def test_controls_forced_intent_changes_routing_decision() -> None:
    memory = _memory(
        content="Caroline's favorite color is teal.",
        entities=[{"name": "Caroline", "role": "subject"}],
    )
    harness = _harness([memory])
    query = "What is Caroline's favorite color?"
    rule = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(routing_mode=RoutingMode.RULE),
    )
    forced = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(
            routing_mode=RoutingMode.FORCED,
            forced_intent=QueryIntent.NO_MEMORY,
        ),
    )
    assert rule.routing is not None
    assert rule.routing.intent is QueryIntent.SEMANTIC
    assert rule.selected_context
    assert forced.routing is not None
    assert forced.routing.intent is QueryIntent.NO_MEMORY
    assert forced.selected_context == []


def test_controls_strategy_changes_decisions() -> None:
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
        fact.content: (0.9, 0.435889894354067),
    }
    harness = _harness([procedure, fact], vectors=vectors)
    query = "How do I create a memory in evoeventmem?"
    qemr = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(strategy=RetrievalStrategy.QEMR),
    )
    vector = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(strategy=RetrievalStrategy.FIXED_VECTOR),
    )
    assert qemr.strategy is RetrievalStrategy.QEMR
    assert vector.strategy is RetrievalStrategy.FIXED_VECTOR
    assert qemr.selected_context[0].memory.memory_id == procedure.memory_id
    assert vector.selected_context[0].memory.memory_id == fact.memory_id


def test_controls_weight_profile_changes_ranking_but_not_reported_strategy() -> None:
    dense_leader = _memory(
        content="alpha beta",
        memory_id=UUID("50000000-0000-0000-0000-000000000061"),
    )
    graph_plus_dense = _memory(
        content="gamma delta",
        entities=[{"name": "Caroline", "role": "subject"}],
        memory_id=UUID("50000000-0000-0000-0000-000000000062"),
    )
    temporal_plus_episodic = _memory(
        content="eta theta",
        kind=MemoryKind.EPISODE,
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        memory_id=UUID("50000000-0000-0000-0000-000000000063"),
    )
    vectors = {
        "Where does Caroline live?": (1.0, 0.0),
        dense_leader.content: (0.9, 0.435889894354067),
        graph_plus_dense.content: (0.5, 0.8660254037844386),
        temporal_plus_episodic.content: (0.0, 1.0),
    }
    harness = _harness(
        [dense_leader, graph_plus_dense, temporal_plus_episodic],
        vectors=vectors,
        default_budget_tokens=200,
    )
    query = "Where does Caroline live?"
    intent_profile = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(
            strategy=RetrievalStrategy.QEMR,
            weight_profile=WeightProfile.INTENT,
            budget_tokens=200,
        ),
    )
    hybrid_profile = harness.retrieve(
        query,
        user_id="u1",
        controls=RetrievalControls(
            strategy=RetrievalStrategy.QEMR,
            weight_profile=WeightProfile.FIXED_HYBRID,
            budget_tokens=200,
        ),
    )
    assert intent_profile.strategy is RetrievalStrategy.QEMR
    assert hybrid_profile.strategy is RetrievalStrategy.QEMR
    intent_ids = [item.memory.memory_id for item in intent_profile.selected_context]
    hybrid_ids = [item.memory.memory_id for item in hybrid_profile.selected_context]
    assert intent_ids == [
        graph_plus_dense.memory_id,
        dense_leader.memory_id,
        temporal_plus_episodic.memory_id,
    ]
    assert hybrid_ids == [
        temporal_plus_episodic.memory_id,
        graph_plus_dense.memory_id,
        dense_leader.memory_id,
    ]


def test_controls_budget_pair_changes_packed_decision() -> None:
    first = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        memory_id=UUID("50000000-0000-0000-0000-000000000071"),
    )
    second = _memory(
        content="gamma delta",
        evidence_id="evidence-2",
        memory_id=UUID("50000000-0000-0000-0000-000000000072"),
    )
    vectors = {
        "query": (1.0, 0.0),
        first.content: (1.0, 0.0),
        second.content: (0.9, 0.435889894354067),
    }
    harness = _harness([first, second], vectors=vectors, default_budget_tokens=200)
    tight = harness.retrieve(
        "query",
        user_id="u1",
        strategy=RetrievalStrategy.FIXED_HYBRID,
        controls=RetrievalControls(budget_tokens=80),
    )
    roomy = harness.retrieve(
        "query",
        user_id="u1",
        strategy=RetrievalStrategy.FIXED_HYBRID,
        controls=RetrievalControls(budget_tokens=400),
    )
    assert tight.budget_tokens < roomy.budget_tokens
    assert tight.total_tokens <= tight.budget_tokens
    assert len(roomy.selected_context) >= len(tight.selected_context)


def test_reader_budget_accounts_for_directive_question_roles_labels_and_metadata() -> None:
    memory = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
    )
    vectors = {"query": (1.0, 0.0), memory.content: (1.0, 0.0)}
    harness = _harness([memory], vectors=vectors)
    result = harness.retrieve("query", user_id="u1", budget_tokens=500)
    assert result.budget.prompt_overhead_tokens > 0
    assert result.budget.content_tokens > 0
    assert result.budget.total_input_tokens_estimate == (
        result.budget.content_tokens + result.budget.prompt_overhead_tokens
    )
    assert result.estimator_name == "evoeventmem-deterministic-tokens"
    assert result.estimator_version == "v1"
    assert [message.role for message in result.reader_messages] == ["system", "user"]
    rendered = "\n".join(message.content for message in result.reader_messages)
    assert "Use the cited evidence" in rendered
    assert "Question: query" in rendered
    assert "[1] alpha beta" in rendered
    assert "evidence=evidence-1" in rendered
    assert "kind=fact" in rendered
    assert "status=active" in rendered
    assert "anchor=2024-01-01T00:00:00+00:00" in rendered


def test_question_length_counts_toward_reader_budget() -> None:
    memory = _memory(content="alpha beta", evidence_id="evidence-1")
    vectors = {
        "short": (1.0, 0.0),
        "a much longer question": (1.0, 0.0),
        memory.content: (1.0, 0.0),
    }
    harness = _harness([memory], vectors=vectors)
    short = harness.retrieve("short", user_id="u1", budget_tokens=500)
    long = harness.retrieve("a much longer question", user_id="u1", budget_tokens=500)
    assert long.budget.total_input_tokens_estimate > short.budget.total_input_tokens_estimate


def test_punctuation_counts_toward_reader_budget() -> None:
    plain = _memory(content="alpha beta", evidence_id="evidence-1")
    punctuated = _memory(content="alpha beta!", evidence_id="evidence-1")
    plain_harness = _harness([plain], vectors={"query": (1.0, 0.0), plain.content: (1.0, 0.0)})
    punct_harness = _harness(
        [punctuated],
        vectors={"query": (1.0, 0.0), punctuated.content: (1.0, 0.0)},
    )
    plain_result = plain_harness.retrieve(
            "query", user_id="u1", budget_tokens=200, strategy=RetrievalStrategy.FIXED_HYBRID
        )
    punctuated_result = punct_harness.retrieve(
            "query", user_id="u1", budget_tokens=200, strategy=RetrievalStrategy.FIXED_HYBRID
        )
    assert len(plain_result.selected_context) == 1
    assert len(punctuated_result.selected_context) == 1
    assert punctuated_result.budget.total_input_tokens_estimate == (
        plain_result.budget.total_input_tokens_estimate + 1
    )


def test_unicode_content_counts_toward_reader_budget() -> None:
    ascii_memory = _memory(content="alpha beta", evidence_id="evidence-1")
    unicode_memory = _memory(content="阿尔法 贝塔", evidence_id="evidence-1")
    ascii_harness = _harness(
        [ascii_memory],
        vectors={"query": (1.0, 0.0), ascii_memory.content: (1.0, 0.0)},
    )
    unicode_harness = _harness(
        [unicode_memory],
        vectors={"query": (1.0, 0.0), unicode_memory.content: (1.0, 0.0)},
    )
    ascii_result = ascii_harness.retrieve(
            "query", user_id="u1", budget_tokens=200, strategy=RetrievalStrategy.FIXED_HYBRID
        )
    unicode_result = unicode_harness.retrieve(
            "query", user_id="u1", budget_tokens=200, strategy=RetrievalStrategy.FIXED_HYBRID
        )
    assert len(ascii_result.selected_context) == 1
    assert len(unicode_result.selected_context) == 1
    assert unicode_result.budget.content_tokens > ascii_result.budget.content_tokens


def test_metadata_length_counts_toward_reader_budget() -> None:
    short_harness = _harness(
        [_memory(content="alpha beta", evidence_id="e1")],
        vectors={"query": (1.0, 0.0), "alpha beta": (1.0, 0.0)},
    )
    long_harness = _harness(
        [_memory(content="gamma delta", evidence_id="a-much-longer-evidence-id")],
        vectors={"query": (1.0, 0.0), "gamma delta": (1.0, 0.0)},
    )
    short_result = short_harness.retrieve("query", user_id="u1", budget_tokens=500)
    long_result = long_harness.retrieve("query", user_id="u1", budget_tokens=500)
    assert long_result.budget.total_input_tokens_estimate == (
        short_result.budget.total_input_tokens_estimate + 8
    )


def test_budget_binds_before_item_cap() -> None:
    memories = [
        _memory(content="alpha beta", evidence_id=f"e{index}")
        for index in range(6)
    ]
    vectors = {"query": (1.0, 0.0), **{memory.content: (1.0, 0.0) for memory in memories}}
    harness = _harness(
        memories,
        vectors=vectors,
        max_items_per_source=100,
        default_budget_tokens=200,
    )
    result = harness.retrieve(
        "query", user_id="u1", budget_tokens=97, strategy=RetrievalStrategy.FIXED_HYBRID
    )
    assert 0 < len(result.selected_context) < 6
    assert result.budget.total_input_tokens_estimate <= result.budget_tokens
    assert all(
        exclusion.reason != "source_diversity_cap" for exclusion in result.exclusions
    ), "token budget must bind before the item-count cap"
    assert sum(
        1 for exclusion in result.exclusions if exclusion.reason == "budget_exceeded"
    ) >= 1


def test_budget_matches_frozen_estimator_on_rendered_messages() -> None:
    memory = _memory(
        content="alpha beta",
        evidence_id="evidence-1",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
    )
    vectors = {"query": (1.0, 0.0), memory.content: (1.0, 0.0)}
    harness = _harness([memory], vectors=vectors)
    result = harness.retrieve("query", user_id="u1", budget_tokens=500)
    recheck = DeterministicTokenEstimator(
        name=result.estimator_name,
        version=result.estimator_version,
    ).count_messages(result.reader_messages)
    assert recheck.total_tokens == result.budget.total_input_tokens_estimate
    assert recheck.content_tokens == result.budget.content_tokens
    assert recheck.message_overhead_tokens == result.budget.prompt_overhead_tokens


def test_duration_constraint_does_not_exclude_untimestamped_events() -> None:
    untimed = _memory(
        content="User has a daily commute that takes 45 minutes each way.",
        evidence_id="evidence-1",
        memory_id=UUID("60000000-0000-0000-0000-000000000001"),
    )
    vectors = {
        "How long is my daily commute to work?": (1.0, 0.0),
        untimed.content: (0.9, 0.0),
    }
    harness = _harness([untimed], vectors=vectors)
    result = harness.retrieve(
        "How long is my daily commute to work?",
        user_id="u1",
        reference_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert untimed.memory_id in [
        item.memory.memory_id for item in result.selected_context
    ]
    assert not any(
        exclusion.reason == "temporal_interval_excluded" for exclusion in result.exclusions
    )
