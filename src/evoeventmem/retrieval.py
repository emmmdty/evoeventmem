from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from evoeventmem.core.ports import EmbeddingModel, MemoryRepository
from evoeventmem.domain.models import (
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
)
from evoeventmem.router import QueryIntent, QueryRouter, QueryRoutingDecision

POLICY_NAME = "qemr-weight-profiles.v1"

_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class CandidateSource(StrEnum):
    DENSE = "dense"
    TEMPORAL = "temporal"
    GRAPH = "graph"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


class RetrievalStrategy(StrEnum):
    FIXED_VECTOR = "fixed_vector"
    FIXED_HYBRID = "fixed_hybrid"
    QEMR = "qemr"


ALL_SOURCES = list(CandidateSource)

QEMR_WEIGHT_PROFILES: dict[QueryIntent, dict[CandidateSource, float]] = {
    QueryIntent.NO_MEMORY: {},
    QueryIntent.SEMANTIC: {
        CandidateSource.DENSE: 1.0,
        CandidateSource.GRAPH: 0.3,
        CandidateSource.TEMPORAL: 0.2,
        CandidateSource.EPISODIC: 0.1,
        CandidateSource.PROCEDURAL: 0.0,
    },
    QueryIntent.TEMPORAL: {
        CandidateSource.TEMPORAL: 1.0,
        CandidateSource.EPISODIC: 0.4,
        CandidateSource.DENSE: 0.3,
        CandidateSource.GRAPH: 0.2,
        CandidateSource.PROCEDURAL: 0.0,
    },
    QueryIntent.GRAPH: {
        CandidateSource.GRAPH: 1.0,
        CandidateSource.DENSE: 0.3,
        CandidateSource.TEMPORAL: 0.1,
        CandidateSource.EPISODIC: 0.1,
        CandidateSource.PROCEDURAL: 0.0,
    },
    QueryIntent.EPISODIC: {
        CandidateSource.EPISODIC: 1.0,
        CandidateSource.TEMPORAL: 0.5,
        CandidateSource.DENSE: 0.2,
        CandidateSource.GRAPH: 0.1,
        CandidateSource.PROCEDURAL: 0.0,
    },
    QueryIntent.PROCEDURAL: {
        CandidateSource.PROCEDURAL: 1.0,
        CandidateSource.DENSE: 0.4,
        CandidateSource.EPISODIC: 0.1,
        CandidateSource.TEMPORAL: 0.0,
        CandidateSource.GRAPH: 0.0,
    },
    QueryIntent.HYBRID: {
        CandidateSource.DENSE: 1.0,
        CandidateSource.TEMPORAL: 1.0,
        CandidateSource.GRAPH: 1.0,
        CandidateSource.EPISODIC: 1.0,
        CandidateSource.PROCEDURAL: 1.0,
    },
}

FIXED_VECTOR_WEIGHTS: dict[CandidateSource, float] = {
    CandidateSource.DENSE: 1.0,
    CandidateSource.TEMPORAL: 0.0,
    CandidateSource.GRAPH: 0.0,
    CandidateSource.EPISODIC: 0.0,
    CandidateSource.PROCEDURAL: 0.0,
}

FIXED_HYBRID_WEIGHTS: dict[CandidateSource, float] = {
    source: 1.0 for source in ALL_SOURCES
}

HISTORICAL_INTENTS = frozenset({QueryIntent.TEMPORAL, QueryIntent.EPISODIC})


class Candidate(BaseModel):
    memory: MemoryRecord
    source: CandidateSource
    raw_score: float = Field(ge=0.0)
    normalized_score: float = Field(ge=0.0, le=1.0)
    reason: str


class SourceScore(BaseModel):
    source: CandidateSource
    normalized_score: float = Field(ge=0.0, le=1.0)
    weighted_score: float = Field(ge=0.0)
    reason: str


class ScoredMemory(BaseModel):
    memory: MemoryRecord
    source_scores: list[SourceScore]
    final_score: float = Field(ge=0.0, le=1.0)
    historical: bool = False
    token_count: int = Field(ge=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class PackedItem(BaseModel):
    memory: MemoryRecord
    component_scores: dict[str, float] = Field(default_factory=dict)
    final_score: float = Field(ge=0.0, le=1.0)
    token_count: int = Field(ge=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    historical: bool = False
    reason: str

    @model_validator(mode="after")
    def require_score_decomposition(self) -> PackedItem:
        if not self.component_scores:
            raise ValueError("packed items require a component score decomposition")
        return self


class ExclusionRecord(BaseModel):
    memory_id: UUID
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class QEMRRetrievalResult(BaseModel):
    schema_version: Literal["qemr-retrieval.v1"] = "qemr-retrieval.v1"
    query: str
    user_id: str
    tenant_id: str | None = None
    intent: QueryIntent
    strategy: RetrievalStrategy
    policy_name: str = POLICY_NAME
    budget_tokens: int = Field(ge=1)
    selected_context: list[PackedItem] = Field(default_factory=list)
    total_tokens: int = Field(ge=0)
    candidates: list[ScoredMemory] = Field(default_factory=list)
    exclusions: list[ExclusionRecord] = Field(default_factory=list)
    routing: QueryRoutingDecision | None = None

    @model_validator(mode="after")
    def enforce_budget(self) -> QEMRRetrievalResult:
        computed = sum(item.token_count for item in self.selected_context)
        if computed != self.total_tokens:
            raise ValueError("total_tokens must equal the sum of selected item tokens")
        if computed > self.budget_tokens:
            raise ValueError("selected context exceeds the configured token budget")
        return self


def resolve_weights(
    strategy: RetrievalStrategy,
    intent: QueryIntent,
) -> dict[CandidateSource, float]:
    if strategy is RetrievalStrategy.FIXED_VECTOR:
        return dict(FIXED_VECTOR_WEIGHTS)
    if strategy is RetrievalStrategy.FIXED_HYBRID:
        return dict(FIXED_HYBRID_WEIGHTS)
    return dict(QEMR_WEIGHT_PROFILES[intent])


class RetrievalHarness:
    """One harness for fixed-vector, fixed-hybrid, and QEMR retrieval."""

    POLICY_NAME = POLICY_NAME
    DEFAULT_BUDGET_TOKENS = 2048
    SUPERSEDED_HISTORICAL_PENALTY = 0.5
    COVERAGE_BONUS = 0.1

    def __init__(
        self,
        repository: MemoryRepository,
        embedding_model: EmbeddingModel,
        *,
        router: QueryRouter | None = None,
        default_budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        max_items_per_source: int = 4,
        max_candidates_per_source: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if default_budget_tokens < 1:
            raise ValueError("default_budget_tokens must be at least 1")
        if max_items_per_source < 1:
            raise ValueError("max_items_per_source must be at least 1")
        if max_candidates_per_source is not None and max_candidates_per_source < 1:
            raise ValueError("max_candidates_per_source must be at least 1")
        self._repository = repository
        self._embedding_model = embedding_model
        self._router = router or QueryRouter()
        self._default_budget_tokens = default_budget_tokens
        self._max_items_per_source = max_items_per_source
        self._max_candidates_per_source = max_candidates_per_source
        self._clock = clock or (lambda: datetime.now(UTC))

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str | None = None,
        strategy: RetrievalStrategy = RetrievalStrategy.QEMR,
        budget_tokens: int | None = None,
        reference_time: datetime | None = None,
    ) -> QEMRRetrievalResult:
        budget = self._default_budget_tokens if budget_tokens is None else budget_tokens
        if budget < 1:
            raise ValueError("budget_tokens must be at least 1")
        routing = self._router.route(query)
        memories = [
            memory
            for memory in self._repository.list_for_user(user_id)
            if memory.tenant_id == tenant_id
        ]
        if routing.intent is QueryIntent.NO_MEMORY:
            return self._no_memory_result(
                query,
                user_id,
                tenant_id,
                routing,
                strategy,
                budget,
                memories,
            )
        weights = resolve_weights(strategy, routing.intent)
        reference = _query_reference_datetime(
            query,
            reference_time if reference_time is not None else self._clock(),
        )
        candidates, capped_memory_ids = self._cap_candidates(
            self._collect_candidates(query, routing, memories, reference)
        )
        normalized = self._normalize(candidates)
        scored = self._merge_candidates(normalized, weights, routing.intent)
        eligible, exclusions = self._classify_memories(scored, routing.intent)
        exclusions.extend(self._capped_memory_exclusions(scored, capped_memory_ids))
        selected, packing_exclusions = self._pack(eligible, budget)
        exclusions.extend(packing_exclusions)
        selected_context = self._build_packed_items(selected, routing.intent)
        return QEMRRetrievalResult(
            query=query,
            user_id=user_id,
            tenant_id=tenant_id,
            intent=routing.intent,
            strategy=strategy,
            policy_name=self.POLICY_NAME,
            budget_tokens=budget,
            selected_context=selected_context,
            total_tokens=sum(item.token_count for item in selected_context),
            candidates=scored,
            exclusions=exclusions,
            routing=routing,
        )

    def _no_memory_result(
        self,
        query: str,
        user_id: str,
        tenant_id: str | None,
        routing: QueryRoutingDecision,
        strategy: RetrievalStrategy,
        budget: int,
        memories: Sequence[MemoryRecord],
    ) -> QEMRRetrievalResult:
        return QEMRRetrievalResult(
            query=query,
            user_id=user_id,
            tenant_id=tenant_id,
            intent=routing.intent,
            strategy=strategy,
            policy_name=self.POLICY_NAME,
            budget_tokens=budget,
            selected_context=[],
            total_tokens=0,
            candidates=[],
            exclusions=[
                ExclusionRecord(
                    memory_id=memory.memory_id,
                    reason="no_memory_intent",
                    details={"intent": routing.intent.value},
                )
                for memory in memories
            ],
            routing=routing,
        )

    def _collect_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        sources = (
            self._dense_candidates,
            self._temporal_candidates,
            self._graph_candidates,
            self._episodic_candidates,
            self._procedural_candidates,
        )
        for source in sources:
            candidates.extend(source(query, routing, memories, reference))
        return candidates

    def _cap_candidates(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[list[Candidate], set[UUID]]:
        if self._max_candidates_per_source is None:
            return list(candidates), set()
        kept: list[Candidate] = []
        dropped: set[UUID] = set()
        for source in ALL_SOURCES:
            source_candidates = sorted(
                [candidate for candidate in candidates if candidate.source is source],
                key=lambda candidate: (-candidate.raw_score, str(candidate.memory.memory_id)),
            )
            kept.extend(source_candidates[: self._max_candidates_per_source])
            dropped.update(
                candidate.memory.memory_id
                for candidate in source_candidates[self._max_candidates_per_source :]
            )
        return kept, dropped

    def _capped_memory_exclusions(
        self,
        scored: Sequence[ScoredMemory],
        capped_memory_ids: set[UUID],
    ) -> list[ExclusionRecord]:
        scored_ids = {item.memory.memory_id for item in scored}
        return [
            ExclusionRecord(
                memory_id=memory_id,
                reason="candidate_cap_reached",
                details={"max_candidates_per_source": self._max_candidates_per_source},
            )
            for memory_id in sorted(capped_memory_ids, key=str)
            if memory_id not in scored_ids
        ]

    def _dense_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
    ) -> list[Candidate]:
        actives = [memory for memory in memories if memory.status is MemoryStatus.ACTIVE]
        if not actives:
            return []
        query_vector = self._embedding_model.embed_texts([query])[0].vector
        vectors = self._embedding_model.embed_texts([memory.content for memory in actives])
        return [
            Candidate(
                memory=memory,
                source=CandidateSource.DENSE,
                raw_score=max(0.0, _cosine_similarity(query_vector, vector.vector)),
                normalized_score=0.0,
                reason="dense-cosine-similarity",
            )
            for memory, vector in zip(actives, vectors, strict=True)
        ]

    def _temporal_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
    ) -> list[Candidate]:
        return [
            Candidate(
                memory=memory,
                source=CandidateSource.TEMPORAL,
                raw_score=_temporal_recency(memory, reference),
                normalized_score=0.0,
                reason="temporal-recency-to-query-reference",
            )
            for memory in memories
            if _temporal_anchor(memory) is not None
        ]

    def _graph_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
    ) -> list[Candidate]:
        query_tokens = _token_set(query)
        return [
            Candidate(
                memory=memory,
                source=CandidateSource.GRAPH,
                raw_score=_jaccard(query_tokens, _relation_tokens(memory)),
                normalized_score=0.0,
                reason="graph-entity-relation-overlap",
            )
            for memory in memories
            if memory.status is MemoryStatus.ACTIVE and _relation_tokens(memory)
        ]

    def _episodic_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
    ) -> list[Candidate]:
        query_tokens = _token_set(query)
        return [
            Candidate(
                memory=memory,
                source=CandidateSource.EPISODIC,
                raw_score=_episodic_score(memory, query_tokens, reference),
                normalized_score=0.0,
                reason="episodic-content-and-recency",
            )
            for memory in memories
            if memory.memory_kind is MemoryKind.EPISODE
        ]

    def _procedural_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
    ) -> list[Candidate]:
        query_tokens = _token_set(query)
        return [
            Candidate(
                memory=memory,
                source=CandidateSource.PROCEDURAL,
                raw_score=_jaccard(query_tokens, _token_set(memory.content)),
                normalized_score=0.0,
                reason="procedural-content-overlap",
            )
            for memory in memories
            if memory.memory_kind is MemoryKind.PROCEDURE
            and memory.status is MemoryStatus.ACTIVE
        ]

    def _normalize(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        max_by_source: dict[CandidateSource, float] = {}
        for candidate in candidates:
            max_by_source[candidate.source] = max(
                max_by_source.get(candidate.source, 0.0),
                candidate.raw_score,
            )
        normalized: list[Candidate] = []
        for candidate in candidates:
            source_max = max_by_source[candidate.source]
            normalized.append(
                candidate.model_copy(
                    update={
                        "normalized_score": (
                            candidate.raw_score / source_max if source_max > 0.0 else 0.0
                        )
                    }
                )
            )
        return normalized

    def _merge_candidates(
        self,
        candidates: Sequence[Candidate],
        weights: dict[CandidateSource, float],
        intent: QueryIntent,
    ) -> list[ScoredMemory]:
        by_memory: dict[UUID, dict[CandidateSource, Candidate]] = {}
        for candidate in candidates:
            if candidate.normalized_score <= 0.0:
                continue
            per_source = by_memory.setdefault(candidate.memory.memory_id, {})
            existing = per_source.get(candidate.source)
            if existing is None or _better_candidate(candidate, existing):
                per_source[candidate.source] = candidate
        merged: list[ScoredMemory] = []
        for memory_id in sorted(by_memory, key=str):
            per_source = by_memory[memory_id]
            memory = next(iter(per_source.values())).memory
            source_scores = [
                SourceScore(
                    source=candidate.source,
                    normalized_score=candidate.normalized_score,
                    weighted_score=weights[candidate.source] * candidate.normalized_score,
                    reason=candidate.reason,
                )
                for candidate in (
                    per_source[source]
                    for source in ALL_SOURCES
                    if source in per_source
                )
            ]
            weight_total = sum(weight for weight in weights.values() if weight > 0.0)
            weighted_sum = sum(score.weighted_score for score in source_scores)
            final_score = weighted_sum / weight_total if weight_total > 0.0 else 0.0
            if memory.status is MemoryStatus.SUPERSEDED and intent in HISTORICAL_INTENTS:
                final_score *= self.SUPERSEDED_HISTORICAL_PENALTY
            merged.append(
                ScoredMemory(
                    memory=memory,
                    source_scores=source_scores,
                    final_score=final_score,
                    historical=(
                        memory.status is MemoryStatus.SUPERSEDED
                        and intent in HISTORICAL_INTENTS
                    ),
                    token_count=_count_tokens(memory.content),
                    evidence_refs=_unique_evidence(memory.evidence_refs),
                )
            )
        return merged

    def _classify_memories(
        self,
        scored: Sequence[ScoredMemory],
        intent: QueryIntent,
    ) -> tuple[list[ScoredMemory], list[ExclusionRecord]]:
        eligible: list[ScoredMemory] = []
        exclusions: list[ExclusionRecord] = []
        for item in scored:
            memory_id = item.memory.memory_id
            if item.memory.status not in (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED):
                exclusions.append(
                    ExclusionRecord(
                        memory_id=memory_id,
                        reason="inactive_status_excluded",
                        details={"status": item.memory.status.value},
                    )
                )
            elif (
                item.memory.status is MemoryStatus.SUPERSEDED
                and intent not in HISTORICAL_INTENTS
            ):
                exclusions.append(
                    ExclusionRecord(
                        memory_id=memory_id,
                        reason="superseded_memory_not_current_fact",
                        details={"superseded_by": str(item.memory.superseded_by)},
                    )
                )
            elif not item.evidence_refs:
                exclusions.append(
                    ExclusionRecord(memory_id=memory_id, reason="missing_evidence_refs")
                )
            elif item.final_score <= 0.0:
                exclusions.append(
                    ExclusionRecord(
                        memory_id=memory_id,
                        reason="zero_final_score",
                        details={"final_score": item.final_score},
                    )
                )
            else:
                eligible.append(item)
        return eligible, exclusions

    def _pack(
        self,
        eligible: Sequence[ScoredMemory],
        budget: int,
    ) -> tuple[list[ScoredMemory], list[ExclusionRecord]]:
        remaining = budget
        covered_evidence: set[tuple[str, str, str | None]] = set()
        source_counts: dict[CandidateSource, int] = {source: 0 for source in ALL_SOURCES}
        selected: list[ScoredMemory] = []
        exclusions: list[ExclusionRecord] = []
        considered_fits: set[UUID] = set()
        pool = sorted(
            list(eligible),
            key=lambda item: (-item.final_score, str(item.memory.memory_id)),
        )
        while pool:
            fits = [
                item
                for item in pool
                if item.token_count <= remaining
                and source_counts[_packing_source(item)] < self._max_items_per_source
            ]
            if not fits:
                break
            considered_fits.update(item.memory.memory_id for item in fits)
            best = max(
                fits,
                key=lambda item: (
                    item.final_score + self._coverage_bonus(item, covered_evidence),
                    str(item.memory.memory_id),
                ),
            )
            pool.remove(best)
            selected.append(best)
            remaining -= best.token_count
            source_counts[_packing_source(best)] += 1
            covered_evidence.update(_evidence_keys(best.evidence_refs))
        for item in pool:
            if source_counts[_packing_source(item)] >= self._max_items_per_source:
                reason = "source_diversity_cap"
            elif item.memory.memory_id in considered_fits:
                reason = "not_selected_by_packing"
            else:
                reason = "budget_exceeded"
            exclusions.append(
                ExclusionRecord(
                    memory_id=item.memory.memory_id,
                    reason=reason,
                    details={"token_count": item.token_count, "remaining": remaining},
                )
            )
        return selected, exclusions

    def _coverage_bonus(
        self,
        item: ScoredMemory,
        covered_evidence: set[tuple[str, str, str | None]],
    ) -> float:
        if _evidence_keys(item.evidence_refs) - covered_evidence:
            return self.COVERAGE_BONUS
        return 0.0

    def _build_packed_items(
        self,
        selected: Sequence[ScoredMemory],
        intent: QueryIntent,
    ) -> list[PackedItem]:
        items: list[PackedItem] = []
        for item in selected:
            component_scores = {
                score.source.value: score.weighted_score for score in item.source_scores
            }
            reason = "packed under token budget"
            if item.historical:
                reason = "packed as historical memory with superseded penalty"
            items.append(
                PackedItem(
                    memory=item.memory,
                    component_scores=component_scores,
                    final_score=item.final_score,
                    token_count=item.token_count,
                    evidence_refs=item.evidence_refs,
                    historical=item.historical,
                    reason=reason,
                )
            )
        return items


class RetrievalService:
    """Retrieval facade that records every result for observability."""

    def __init__(self, harness: RetrievalHarness) -> None:
        self._harness = harness
        self._results: list[QEMRRetrievalResult] = []

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str | None = None,
        strategy: RetrievalStrategy = RetrievalStrategy.QEMR,
        budget_tokens: int | None = None,
        reference_time: datetime | None = None,
    ) -> QEMRRetrievalResult:
        result = self._harness.retrieve(
            query,
            user_id=user_id,
            tenant_id=tenant_id,
            strategy=strategy,
            budget_tokens=budget_tokens,
            reference_time=reference_time,
        )
        self._results.append(result)
        return result

    def list_results(self) -> list[QEMRRetrievalResult]:
        return list(self._results)

    def export_jsonl(self) -> list[dict[str, object]]:
        return [result.model_dump(mode="json") for result in self._results]


def _packing_source(item: ScoredMemory) -> CandidateSource:
    if not item.source_scores:
        return CandidateSource.DENSE
    return max(
        item.source_scores,
        key=lambda score: (score.weighted_score, score.source.value),
    ).source


def _better_candidate(candidate: Candidate, existing: Candidate) -> bool:
    if candidate.normalized_score != existing.normalized_score:
        return candidate.normalized_score > existing.normalized_score
    if candidate.raw_score != existing.raw_score:
        return candidate.raw_score > existing.raw_score
    return str(candidate.memory.memory_id) < str(existing.memory.memory_id)


def _query_reference_datetime(query: str, now: datetime) -> datetime:
    match = _YEAR_RE.search(query)
    if match is not None:
        return datetime(int(match.group(1)), 1, 1, tzinfo=UTC)
    return now


def _temporal_anchor(memory: MemoryRecord) -> datetime | None:
    return memory.valid_from or memory.event_time


def _temporal_recency(memory: MemoryRecord, reference: datetime) -> float:
    anchor = _temporal_anchor(memory)
    if anchor is None:
        return 0.0
    distance_days = abs((reference - anchor).days)
    return 1.0 / (1.0 + distance_days / 365.0)


def _episodic_score(
    memory: MemoryRecord,
    query_tokens: set[str],
    reference: datetime,
) -> float:
    overlap = _jaccard(query_tokens, _token_set(memory.content))
    return 0.5 * overlap + 0.5 * _temporal_recency(memory, reference)


def _relation_tokens(memory: MemoryRecord) -> set[str]:
    terms: list[str] = [entity.name for entity in memory.entities]
    for relation in memory.relations:
        terms.extend([relation.predicate, relation.target])
    return _token_set(" ".join(terms))


def _token_set(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    )
    return dot_product / (left_norm * right_norm)


def _evidence_keys(refs: Iterable[EvidenceRef]) -> set[tuple[str, str, str | None]]:
    return {(ref.source_type, ref.source_id, ref.locator) for ref in refs}


def _unique_evidence(refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[EvidenceRef] = []
    for ref in refs:
        key = (ref.source_type, ref.source_id, ref.locator)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _count_tokens(text: str) -> int:
    return len(text.split())


__all__ = [
    "ALL_SOURCES",
    "Candidate",
    "CandidateSource",
    "ExclusionRecord",
    "FIXED_HYBRID_WEIGHTS",
    "FIXED_VECTOR_WEIGHTS",
    "HISTORICAL_INTENTS",
    "PackedItem",
    "POLICY_NAME",
    "QEMRRetrievalResult",
    "QEMR_WEIGHT_PROFILES",
    "RetrievalHarness",
    "RetrievalService",
    "RetrievalStrategy",
    "ScoredMemory",
    "SourceScore",
    "resolve_weights",
]
