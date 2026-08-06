from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from evoeventmem.core.ports import ChatMessage, EmbeddingModel, MemoryRepository
from evoeventmem.domain.models import (
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
)
from evoeventmem.router import (
    QueryIntent,
    QueryRouter,
    QueryRoutingDecision,
    TemporalOperator,
)
from evoeventmem.router import (
    TemporalConstraint as RoutedTemporalConstraint,
)
from evoeventmem.tokenization import (
    DEFAULT_TOKEN_ESTIMATOR,
    DeterministicTokenEstimator,
    TokenEstimate,
)

POLICY_NAME = "qemr-weight-profiles.v2"

# Fixed reciprocal-rank fusion constant: contribution = weight / (k + rank).
# Declared before any benchmark run; never tuned on reported outcomes.
RRF_K = 60.0

# Single source of truth for the complete reader input: the system directive,
# the question, and one labeled, metadata-bearing block per packed item.
READER_SYSTEM_DIRECTIVE = (
    "Use the cited evidence below to answer the question. "
    "Cite evidence labels in your answer."
)
QUESTION_PREFIX = "Question: "

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

# Fixed policy cap: unconstrained ``when`` queries treat temporal presence as a
# small feature, never equal to the dense relevance weight.
UNCONSTRAINED_TEMPORAL_WEIGHT_CAP = 0.2


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
    raw_score: float = Field(default=0.0, ge=0.0)
    rank: int | None = Field(default=None, ge=1)
    weight: float = Field(default=0.0, ge=0.0)
    fusion_contribution: float = Field(default=0.0, ge=0.0, le=1.0)
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


class EvidencePolicy(StrEnum):
    CONSTRAINED = "constrained"
    PROVENANCE_ONLY = "provenance_only"


class WeightProfile(StrEnum):
    INTENT = "intent"
    FIXED_VECTOR = "fixed_vector"
    FIXED_HYBRID = "fixed_hybrid"


class RoutingMode(StrEnum):
    RULE = "rule"
    FORCED = "forced"


class RetrievalControls(BaseModel):
    """One public control surface for retrieval method ablations.

    B executes ablations through these switches and never reimplements
    retrieval internals. Each pair of settings must change at least one
    selection, exclusion, ranking, or packing decision on a controlled
    fixture while all other inputs stay equal.
    """

    strategy: RetrievalStrategy = RetrievalStrategy.QEMR
    routing_mode: RoutingMode = RoutingMode.RULE
    forced_intent: QueryIntent | None = None
    weight_profile: WeightProfile = WeightProfile.INTENT
    evidence_policy: EvidencePolicy = EvidencePolicy.CONSTRAINED
    enable_temporal_source: bool = True
    enable_graph_source: bool = True
    budget_tokens: int | None = Field(default=None, ge=1)
    reference_time: datetime | None = None

    @model_validator(mode="after")
    def forced_intent_requires_forced_mode(self) -> RetrievalControls:
        if self.forced_intent is not None and self.routing_mode is not RoutingMode.FORCED:
            raise ValueError("forced_intent requires routing_mode=forced")
        return self


class ComponentScore(BaseModel):
    source: CandidateSource
    raw_score: float = Field(ge=0.0)
    rank: int = Field(ge=1)
    weight: float = Field(ge=0.0)
    fusion_contribution: float = Field(ge=0.0, le=1.0)


class TemporalConstraint(BaseModel):
    operator: Literal[
        "none",
        "at",
        "before",
        "after",
        "between",
        "earliest",
        "latest",
        "sequence",
        "duration",
    ]
    lower_bound_utc: datetime | None = None
    upper_bound_utc: datetime | None = None


class SourceFailureEvent(BaseModel):
    source: CandidateSource
    reason_code: str = Field(min_length=1)
    degraded_policy: EvidencePolicy
    duration_ms: float = Field(ge=0.0)


class RenderedMessageBudget(BaseModel):
    content_tokens: int = Field(default=0, ge=0)
    prompt_overhead_tokens: int = Field(default=0, ge=0)
    total_input_tokens_estimate: int = Field(default=0, ge=0)


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    tenant_id: str | None = None
    intent: QueryIntent
    temporal_constraint: TemporalConstraint
    evidence_policy: EvidencePolicy = EvidencePolicy.CONSTRAINED
    exclusions: list[UUID] = Field(default_factory=list)
    budget_tokens: int = Field(ge=1)


class RetrievalResult(BaseModel):
    query: str
    user_id: str = Field(min_length=1)
    tenant_id: str | None = None
    intent: QueryIntent
    evidence_policy: EvidencePolicy
    temporal_constraint: TemporalConstraint
    component_scores: list[ComponentScore] = Field(default_factory=list)
    source_failures: list[SourceFailureEvent] = Field(default_factory=list)
    exclusions: list[UUID] = Field(default_factory=list)
    packed_items: list[PackedItem] = Field(default_factory=list)
    budget: RenderedMessageBudget

    @model_validator(mode="after")
    def enforce_budget(self) -> RetrievalResult:
        if self.budget.total_input_tokens_estimate < self.budget.content_tokens:
            raise ValueError("total_input_tokens_estimate must be >= content_tokens")
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
    evidence_policy: EvidencePolicy = EvidencePolicy.CONSTRAINED
    budget_tokens: int = Field(ge=1)
    selected_context: list[PackedItem] = Field(default_factory=list)
    total_tokens: int = Field(ge=0)
    candidates: list[ScoredMemory] = Field(default_factory=list)
    exclusions: list[ExclusionRecord] = Field(default_factory=list)
    source_failures: list[SourceFailureEvent] = Field(default_factory=list)
    budget: RenderedMessageBudget = Field(
        default_factory=lambda: RenderedMessageBudget()
    )
    reader_messages: list[ChatMessage] = Field(default_factory=list)
    estimator_name: str = ""
    estimator_version: str = ""
    controls: RetrievalControls | None = None
    routing: QueryRoutingDecision | None = None

    @model_validator(mode="after")
    def enforce_budget(self) -> QEMRRetrievalResult:
        computed = sum(item.token_count for item in self.selected_context)
        if computed != self.total_tokens:
            raise ValueError("total_tokens must equal the sum of selected item tokens")
        if self.budget.total_input_tokens_estimate > self.budget_tokens:
            raise ValueError("selected context exceeds the configured token budget")
        if self.budget.total_input_tokens_estimate != (
            self.budget.content_tokens + self.budget.prompt_overhead_tokens
        ):
            raise ValueError(
                "total_input_tokens_estimate must equal content plus overhead tokens"
            )
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
        estimator: DeterministicTokenEstimator | None = None,
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
        self._estimator = estimator or DEFAULT_TOKEN_ESTIMATOR

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str | None = None,
        strategy: RetrievalStrategy = RetrievalStrategy.QEMR,
        budget_tokens: int | None = None,
        reference_time: datetime | None = None,
        controls: RetrievalControls | None = None,
    ) -> QEMRRetrievalResult:
        if controls is not None:
            strategy = controls.strategy
            if controls.budget_tokens is not None:
                budget_tokens = controls.budget_tokens
            if controls.reference_time is not None:
                reference_time = controls.reference_time
        budget = self._default_budget_tokens if budget_tokens is None else budget_tokens
        if budget < 1:
            raise ValueError("budget_tokens must be at least 1")
        fixed_overhead = self._estimate_reader_input(query, []).total_tokens
        if budget < fixed_overhead:
            raise ValueError(
                f"budget_tokens must fit the fixed reader overhead "
                f"({fixed_overhead} tokens)"
            )
        routing = self._router.route(query, reference_time=reference_time)
        if controls is not None and controls.forced_intent is not None:
            routing = routing.model_copy(
                update={
                    "intent": controls.forced_intent,
                    "reason": "intent forced by retrieval controls",
                }
            )
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
                evidence_policy=(
                    controls.evidence_policy
                    if controls is not None
                    else EvidencePolicy.CONSTRAINED
                ),
                controls=controls,
            )
        weights = resolve_weights(strategy, routing.intent)
        if controls is not None and controls.weight_profile is not WeightProfile.INTENT:
            if controls.weight_profile is WeightProfile.FIXED_HYBRID:
                weights = dict(FIXED_HYBRID_WEIGHTS)
            else:
                weights = dict(FIXED_VECTOR_WEIGHTS)
        reference = _query_reference_datetime(
            query,
            reference_time if reference_time is not None else self._clock(),
        )
        candidates, source_failures = self._collect_candidates(
            query,
            routing,
            memories,
            reference,
            controls,
        )
        candidates, capped_memory_ids = self._cap_candidates(candidates)
        temporal_exclusions: list[ExclusionRecord] = []
        temporal_enabled = controls is None or controls.enable_temporal_source
        if strategy is not RetrievalStrategy.FIXED_VECTOR and temporal_enabled:
            candidates, temporal_exclusions = self._apply_temporal_constraint(
                candidates,
                routing,
                reference,
            )
        weights = self._effective_weights(weights, routing)
        if strategy is RetrievalStrategy.FIXED_VECTOR:
            normalized = self._normalize(candidates)
            scored = self._merge_candidates(normalized, weights, routing.intent)
        else:
            scored = self._merge_candidates(candidates, weights, routing.intent, wrrf=True)
        eligible, exclusions = self._classify_memories(scored, routing.intent)
        exclusions = [*temporal_exclusions, *exclusions]
        exclusions.extend(self._capped_memory_exclusions(scored, capped_memory_ids))
        evidence_policy = (
            controls.evidence_policy if controls is not None else EvidencePolicy.CONSTRAINED
        )
        selected, marginal_tokens, final_estimate, packing_exclusions = self._pack(
            eligible,
            budget,
            evidence_policy,
            routing.query,
        )
        exclusions.extend(packing_exclusions)
        selected_context = self._build_packed_items(selected, routing.intent, marginal_tokens)
        return QEMRRetrievalResult(
            query=query,
            user_id=user_id,
            tenant_id=tenant_id,
            intent=routing.intent,
            strategy=strategy,
            policy_name=self.POLICY_NAME,
            evidence_policy=evidence_policy,
            budget_tokens=budget,
            selected_context=selected_context,
            total_tokens=sum(
                marginal_tokens[item.memory.memory_id] for item in selected
            ),
            candidates=scored,
            exclusions=exclusions,
            source_failures=source_failures,
            budget=RenderedMessageBudget(
                content_tokens=final_estimate.content_tokens,
                prompt_overhead_tokens=final_estimate.message_overhead_tokens,
                total_input_tokens_estimate=final_estimate.total_tokens,
            ),
            reader_messages=_reader_messages(
                routing.query,
                _reader_entries(selected),
            ),
            estimator_name=self._estimator.name,
            estimator_version=self._estimator.version,
            controls=controls,
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
        evidence_policy: EvidencePolicy = EvidencePolicy.CONSTRAINED,
        controls: RetrievalControls | None = None,
    ) -> QEMRRetrievalResult:
        return QEMRRetrievalResult(
            query=query,
            user_id=user_id,
            tenant_id=tenant_id,
            intent=routing.intent,
            strategy=strategy,
            evidence_policy=evidence_policy,
            controls=controls,
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
            budget=RenderedMessageBudget(),
            reader_messages=[],
            estimator_name=self._estimator.name,
            estimator_version=self._estimator.version,
            routing=routing,
        )

    def _collect_candidates(
        self,
        query: str,
        routing: QueryRoutingDecision,
        memories: Sequence[MemoryRecord],
        reference: datetime,
        controls: RetrievalControls | None,
    ) -> tuple[list[Candidate], list[SourceFailureEvent]]:
        """Collect candidates per source with observable failure handling.

        Every source is isolated: an exception records a source failure event
        (stable reason code, degraded policy, duration) and the remaining
        sources continue. A disabled source is skipped, not failed. Failures
        are never silent and never change the reported strategy.
        """
        candidates: list[Candidate] = []
        failures: list[SourceFailureEvent] = []
        source_type = Callable[
            [str, QueryRoutingDecision, Sequence[MemoryRecord], datetime],
            list[Candidate],
        ]
        sources: list[tuple[CandidateSource, source_type]] = [
            (CandidateSource.DENSE, self._dense_candidates),
            (CandidateSource.TEMPORAL, self._temporal_candidates),
            (CandidateSource.GRAPH, self._graph_candidates),
            (CandidateSource.EPISODIC, self._episodic_candidates),
            (CandidateSource.PROCEDURAL, self._procedural_candidates),
        ]
        for source, source_fn in sources:
            if controls is not None:
                if source is CandidateSource.TEMPORAL and not controls.enable_temporal_source:
                    continue
                if source is CandidateSource.GRAPH and not controls.enable_graph_source:
                    continue
            started = time.perf_counter()
            try:
                candidates.extend(source_fn(query, routing, memories, reference))
            except Exception:
                failures.append(
                    SourceFailureEvent(
                        source=source,
                        reason_code=f"{source.value}_source_error",
                        degraded_policy=(
                            controls.evidence_policy
                            if controls is not None
                            else EvidencePolicy.CONSTRAINED
                        ),
                        duration_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
                    )
                )
        return candidates, failures

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

    def _effective_weights(
        self,
        weights: dict[CandidateSource, float],
        routing: QueryRoutingDecision,
    ) -> dict[CandidateSource, float]:
        """Return the effective per-source weights for a routing decision.

        For unconstrained ``when`` queries (operator ``NONE``), temporal
        presence is a small feature, so the temporal weight is capped low to
        keep dense/entity relevance dominant. This is a fixed policy constant,
        not a tuned value.
        """
        if (
            routing.intent in HISTORICAL_INTENTS
            and not routing.temporal_constraint.is_constrained
        ):
            adjusted = dict(weights)
            adjusted[CandidateSource.TEMPORAL] = min(
                weights[CandidateSource.TEMPORAL],
                UNCONSTRAINED_TEMPORAL_WEIGHT_CAP,
            )
            return adjusted
        return weights

    def _apply_temporal_constraint(
        self,
        candidates: Sequence[Candidate],
        routing: QueryRoutingDecision,
        reference: datetime,
    ) -> tuple[list[Candidate], list[ExclusionRecord]]:
        """Apply the parsed temporal constraint to the candidate pool.

        Temporal behavior is relevance-first: dense/entity/relation sources
        establish the relevance pool, and this stage only re-ranks or filters
        that pool by temporal anchors. A memory that is not in the relevance
        pool (no non-temporal candidate) is never rescued by recency alone.
        For ``NONE``, time presence is a small additive feature and cannot let
        an unrelated newest memory dominate semantic relevance.

        Returns the constrained candidates plus observable exclusion records
        for memories dropped by an explicit interval constraint.
        """
        constraint = routing.temporal_constraint
        operator = constraint.operator
        if not any(
            candidate.source is CandidateSource.TEMPORAL for candidate in candidates
        ):
            return list(candidates), []
        relevant_ids = {
            candidate.memory.memory_id
            for candidate in candidates
            if candidate.source is not CandidateSource.TEMPORAL
            and candidate.raw_score > 0.0
        }
        query_tokens = _token_set(routing.query)
        historical = routing.intent in HISTORICAL_INTENTS
        pool: list[Candidate] = []
        outside_pool: list[Candidate] = []
        for candidate in candidates:
            if candidate.source is not CandidateSource.TEMPORAL:
                continue
            memory_id = candidate.memory.memory_id
            in_pool = memory_id in relevant_ids
            rescued = (
                historical
                and candidate.memory.status is MemoryStatus.SUPERSEDED
                and bool(_token_set(candidate.memory.content) & query_tokens)
            )
            if in_pool or rescued:
                pool.append(candidate)
            else:
                outside_pool.append(
                    candidate.model_copy(
                        update={
                            "raw_score": 0.0,
                            "reason": "temporal-candidate-outside-relevance-pool",
                        }
                    )
                )
        non_temporal = [
            candidate
            for candidate in candidates
            if candidate.source is not CandidateSource.TEMPORAL
        ]
        if operator is TemporalOperator.NONE:
            upgraded = self._apply_unconstrained_temporal(pool, reference)
            return non_temporal + upgraded + outside_pool, []
        if operator is TemporalOperator.LATEST or operator is TemporalOperator.EARLIEST:
            processed = self._apply_extremal_temporal(
                pool,
                earliest=operator is TemporalOperator.EARLIEST,
            )
            return non_temporal + processed + outside_pool, []
        kept = self._apply_interval_temporal(pool, constraint)
        kept_ids = {candidate.memory.memory_id for candidate in kept}
        dropped_pool_ids = {
            candidate.memory.memory_id for candidate in pool
        } - kept_ids
        exclusions = [
            ExclusionRecord(
                memory_id=memory_id,
                reason="temporal_interval_excluded",
                details={
                    "operator": operator.value,
                    "lower_bound_utc": (
                        constraint.lower_bound_utc.isoformat()
                        if constraint.lower_bound_utc is not None
                        else None
                    ),
                    "upper_bound_utc": (
                        constraint.upper_bound_utc.isoformat()
                        if constraint.upper_bound_utc is not None
                        else None
                    ),
                },
            )
            for memory_id in sorted(dropped_pool_ids, key=str)
        ]
        kept_non_temporal = [
            candidate
            for candidate in non_temporal
            if candidate.memory.memory_id in kept_ids
        ]
        return kept_non_temporal + kept + outside_pool, exclusions

    def _apply_unconstrained_temporal(
        self,
        temporal: Sequence[Candidate],
        reference: datetime,
    ) -> list[Candidate]:
        upgraded: list[Candidate] = []
        for candidate in temporal:
            anchor = _temporal_anchor(candidate.memory)
            if anchor is None:
                continue
            upgraded.append(
                candidate.model_copy(
                    update={
                        "reason": "temporal-presence-small-feature",
                        "raw_score": _temporal_recency_presence(candidate.memory, reference),
                    }
                )
            )
        return upgraded

    def _apply_extremal_temporal(
        self,
        temporal: Sequence[Candidate],
        *,
        earliest: bool,
    ) -> list[Candidate]:
        anchored = [
            candidate
            for candidate in temporal
            if _temporal_anchor(candidate.memory) is not None
        ]
        if not anchored:
            return list(temporal)
        anchored.sort(
            key=lambda candidate: (
                _temporal_anchor(candidate.memory) or datetime.max.replace(tzinfo=UTC),
                str(candidate.memory.memory_id),
            ),
            reverse=not earliest,
        )
        winner = anchored[0]
        winning = winner.model_copy(
            update={
                "raw_score": 1.0,
                "reason": "extremal-temporal-best-in-relevant-pool",
            }
        )
        losers = [
            candidate.model_copy(
                update={"raw_score": 0.0, "normalized_score": 0.0},
            )
            for candidate in anchored[1:]
        ]
        return [winning, *losers]

    def _apply_interval_temporal(
        self,
        temporal: Sequence[Candidate],
        constraint: RoutedTemporalConstraint,
    ) -> list[Candidate]:
        operator = constraint.operator
        if operator is TemporalOperator.AT:
            lower = constraint.lower_bound_utc or constraint.upper_bound_utc
            upper = constraint.upper_bound_utc or lower
        elif operator is TemporalOperator.BEFORE:
            lower = None
            upper = constraint.upper_bound_utc
        elif operator is TemporalOperator.AFTER:
            lower = constraint.lower_bound_utc
            upper = None
        elif operator is TemporalOperator.BETWEEN:
            lower = constraint.lower_bound_utc
            upper = constraint.upper_bound_utc
        else:
            return list(temporal)
        if lower is None and upper is None:
            return list(temporal)
        ranked: list[Candidate] = []
        for candidate in temporal:
            anchor = _temporal_anchor(candidate.memory)
            if anchor is None:
                continue
            agreement = _interval_agreement(anchor, lower, upper)
            if agreement <= 0.0:
                continue
            ranked.append(
                candidate.model_copy(
                    update={
                        "raw_score": agreement,
                        "reason": "temporal-interval-agreement",
                    }
                )
            )
        ranked.sort(key=lambda candidate: (-candidate.raw_score, str(candidate.memory.memory_id)))
        return ranked

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
        *,
        wrrf: bool = False,
    ) -> list[ScoredMemory]:
        """Merge per-source candidates into scored memories.

        The hybrid/QEMR path uses deterministic weighted reciprocal-rank
        fusion (``wrrf=True``): each candidate contributes
        ``weight / (RRF_K + rank)`` where ``rank`` is its position within its
        source by raw score (ties broken by memory id). Per-source max
        normalization is retained only for the fixed-vector baseline so its
        ordering is unchanged.

        Zero-score candidates are kept per memory so classification can record
        observable exclusions, but they contribute nothing to fusion.
        """
        by_memory: dict[UUID, dict[CandidateSource, Candidate]] = {}
        for candidate in candidates:
            per_source = by_memory.setdefault(candidate.memory.memory_id, {})
            existing = per_source.get(candidate.source)
            if existing is None or _better_candidate(candidate, existing):
                per_source[candidate.source] = candidate
        ranks = self._source_ranks(candidates)
        weight_total = sum(weight for weight in weights.values() if weight > 0.0)
        merged: list[ScoredMemory] = []
        for memory_id in sorted(by_memory, key=str):
            per_source = by_memory[memory_id]
            memory = next(iter(per_source.values())).memory
            source_scores: list[SourceScore] = []
            for source in ALL_SOURCES:
                per_source_candidate = per_source.get(source)
                if per_source_candidate is None:
                    continue
                candidate = per_source_candidate
                rank = ranks[source].get(memory_id)
                if wrrf:
                    if rank is None:
                        contribution = 0.0
                        normalized = 0.0
                    else:
                        contribution = weights[source] / (RRF_K + rank)
                        normalized = contribution
                    weighted = contribution
                else:
                    normalized = candidate.normalized_score
                    weighted = weights[source] * normalized
                    contribution = weighted
                source_scores.append(
                    SourceScore(
                        source=source,
                        normalized_score=normalized,
                        weighted_score=weighted,
                        raw_score=candidate.raw_score,
                        rank=rank,
                        weight=weights[source],
                        fusion_contribution=contribution,
                        reason=candidate.reason,
                    )
                )
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

    def _source_ranks(
        self,
        candidates: Sequence[Candidate],
    ) -> dict[CandidateSource, dict[UUID, int]]:
        """Per-source rank of every candidate with a positive raw score.

        Ranks are 1-based by raw score descending with a stable memory-id
        tie-break, so identical scores still produce a deterministic order.
        Zero-raw candidates (e.g. temporal candidates outside the relevance
        pool) receive no rank and therefore no fusion contribution.
        """
        ranks: dict[CandidateSource, dict[UUID, int]] = {}
        for source in ALL_SOURCES:
            source_candidates = sorted(
                [
                    candidate
                    for candidate in candidates
                    if candidate.source is source and candidate.raw_score > 0.0
                ],
                key=lambda candidate: (
                    -candidate.raw_score,
                    str(candidate.memory.memory_id),
                ),
            )
            ranks[source] = {
                candidate.memory.memory_id: position
                for position, candidate in enumerate(source_candidates, start=1)
            }
        return ranks

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
        evidence_policy: EvidencePolicy,
        query: str,
    ) -> tuple[
        list[ScoredMemory],
        dict[UUID, int],
        TokenEstimate,
        list[ExclusionRecord],
    ]:
        """Pack items under the complete reader-input token budget.

        The fixed overhead (system directive, question, chat-message overhead)
        is reserved before any item is selected: an item fits only when the
        fully rendered reader input still fits the budget. Each packed item
        records its marginal token cost in the rendered input.
        """
        selected: list[ScoredMemory] = []
        covered_evidence: set[tuple[str, str, str | None]] = set()
        source_counts: dict[CandidateSource, int] = {source: 0 for source in ALL_SOURCES}
        exclusions: list[ExclusionRecord] = []
        coverage_active = evidence_policy is EvidencePolicy.CONSTRAINED
        pool = sorted(
            list(eligible),
            key=lambda item: (-item.final_score, str(item.memory.memory_id)),
        )
        while pool:
            fits = [
                item
                for item in pool
                if self._estimate_reader_input(
                    query,
                    _reader_entries([*selected, item]),
                ).total_tokens
                <= budget
                and source_counts[_packing_source(item)] < self._max_items_per_source
            ]
            if not fits:
                break
            best = max(
                fits,
                key=lambda item: (
                    item.final_score
                    + (
                        self._coverage_bonus(item, covered_evidence)
                        if coverage_active
                        else 0.0
                    ),
                    str(item.memory.memory_id),
                ),
            )
            pool.remove(best)
            selected.append(best)
            source_counts[_packing_source(best)] += 1
            covered_evidence.update(_evidence_keys(best.evidence_refs))
        marginal_tokens: dict[UUID, int] = {}
        running: list[ScoredMemory] = []
        for item in selected:
            before = self._estimate_reader_input(
                query,
                _reader_entries(running),
            ).total_tokens
            running.append(item)
            after = self._estimate_reader_input(
                query,
                _reader_entries(running),
            ).total_tokens
            marginal_tokens[item.memory.memory_id] = after - before
        final_estimate = self._estimate_reader_input(query, _reader_entries(selected))
        selected_ids = {item.memory.memory_id for item in selected}
        for item in pool:
            fits_now = (
                item.memory.memory_id not in selected_ids
                and self._estimate_reader_input(
                    query,
                    _reader_entries([*selected, item]),
                ).total_tokens
                <= budget
            )
            if source_counts[_packing_source(item)] >= self._max_items_per_source:
                reason = "source_diversity_cap"
            elif fits_now:
                reason = "not_selected_by_packing"
            else:
                reason = "budget_exceeded"
            exclusions.append(
                ExclusionRecord(
                    memory_id=item.memory.memory_id,
                    reason=reason,
                    details={"token_count": item.token_count, "remaining": budget},
                )
            )
        return selected, marginal_tokens, final_estimate, exclusions

    def _estimate_reader_input(
        self,
        query: str,
        entries: Sequence[tuple[MemoryRecord, list[EvidenceRef]]],
    ) -> TokenEstimate:
        return self._estimator.count_messages(_reader_messages(query, entries))

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
        marginal_tokens: dict[UUID, int],
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
                    token_count=marginal_tokens[item.memory.memory_id],
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
        controls: RetrievalControls | None = None,
    ) -> QEMRRetrievalResult:
        result = self._harness.retrieve(
            query,
            user_id=user_id,
            tenant_id=tenant_id,
            strategy=strategy,
            budget_tokens=budget_tokens,
            reference_time=reference_time,
            controls=controls,
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


def _temporal_recency_presence(memory: MemoryRecord, reference: datetime) -> float:
    """Small, bounded temporal feature for unconstrained ``when`` queries.

    Time presence is a minor ordinal signal that can nudge ties but cannot
    dominate semantic/dense relevance.
    """
    return 0.05 * _temporal_recency(memory, reference)


def _interval_agreement(
    anchor: datetime,
    lower: datetime | None,
    upper: datetime | None,
) -> float:
    """Score how well an event anchor lies within the query's interval.

    Closed-interval membership: 1.0 inside the interval, 0.0 outside it.
    ``None`` bounds are open on that side. Out-of-interval candidates never
    receive partial credit, so an explicit temporal filter cannot promote an
    out-of-range memory above an in-range one.
    """
    if lower is not None and anchor < lower:
        return 0.0
    if upper is not None and anchor > upper:
        return 0.0
    return 1.0


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


def _reader_entries(
    memories: Sequence[ScoredMemory],
) -> list[tuple[MemoryRecord, list[EvidenceRef]]]:
    return [(item.memory, item.evidence_refs) for item in memories]


def _metadata_line(memory: MemoryRecord) -> str:
    parts = [f"kind={memory.memory_kind.value}", f"status={memory.status.value}"]
    anchor = _temporal_anchor(memory)
    if anchor is not None:
        parts.append(f"anchor={anchor.isoformat()}")
    return " ".join(parts)


def _reader_messages(
    query: str,
    entries: Sequence[tuple[MemoryRecord, list[EvidenceRef]]],
) -> list[ChatMessage]:
    """Render the complete reader input from one source of truth.

    The system message carries the reader directive, the user message carries
    the question followed by one labeled block per packed item with its
    content, evidence labels, and metadata. B consumes these rendered messages
    directly instead of assembling its own strings.
    """
    system = ChatMessage(role="system", content=READER_SYSTEM_DIRECTIVE)
    parts = [f"{QUESTION_PREFIX}{query}"]
    for index, (memory, refs) in enumerate(entries, start=1):
        parts.append(f"[{index}] {memory.content}")
        evidence = ",".join(ref.source_id for ref in refs)
        parts.append(f"evidence={evidence} {_metadata_line(memory)}")
    return [system, ChatMessage(role="user", content="\n".join(parts))]


__all__ = [
    "ALL_SOURCES",
    "Candidate",
    "CandidateSource",
    "ComponentScore",
    "EvidencePolicy",
    "ExclusionRecord",
    "FIXED_HYBRID_WEIGHTS",
    "FIXED_VECTOR_WEIGHTS",
    "HISTORICAL_INTENTS",
    "PackedItem",
    "POLICY_NAME",
    "QEMRRetrievalResult",
    "QEMR_WEIGHT_PROFILES",
    "RenderedMessageBudget",
    "RetrievalControls",
    "RetrievalHarness",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalStrategy",
    "RoutingMode",
    "ScoredMemory",
    "SourceFailureEvent",
    "SourceScore",
    "TemporalConstraint",
    "WeightProfile",
    "resolve_weights",
]
