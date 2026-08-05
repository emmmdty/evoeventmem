from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord, MemoryStatus
from evoeventmem.retrieval import (
    ComponentScore,
    EvidencePolicy,
    PackedItem,
    RenderedMessageBudget,
    RetrievalRequest,
    RetrievalResult,
    SourceFailureEvent,
    TemporalConstraint,
)
from evoeventmem.router import QueryIntent


def _evidence() -> list[EvidenceRef]:
    return [EvidenceRef(source_type="turn", source_id="raw-1", locator="p3")]


def _memory() -> MemoryRecord:
    return MemoryRecord(
        memory_id=uuid4(),
        user_id="u1",
        memory_kind=MemoryKind.FACT,
        content="Caroline moved to Lisbon.",
        status=MemoryStatus.ACTIVE,
        evidence_refs=_evidence(),
    )


def _packed_item() -> PackedItem:
    return PackedItem(
        memory=_memory(),
        component_scores={"dense": 0.5},
        final_score=0.5,
        token_count=5,
        evidence_refs=_evidence(),
        reason="packed under token budget",
    )


def test_evidence_policy_values_frozen() -> None:
    assert EvidencePolicy.CONSTRAINED.value == "constrained"
    assert EvidencePolicy.PROVENANCE_ONLY.value == "provenance_only"


def test_temporal_constraint_carries_operator_and_utc_bounds() -> None:
    constraint = TemporalConstraint(
        operator="between",
        lower_bound_utc=datetime(2021, 1, 1, tzinfo=UTC),
        upper_bound_utc=datetime(2022, 1, 1, tzinfo=UTC),
    )
    assert constraint.operator == "between"
    assert constraint.lower_bound_utc.tzinfo is not None
    assert constraint.upper_bound_utc.tzinfo is not None


def test_component_score_carries_raw_score_rank_and_fusion_contribution() -> None:
    score = ComponentScore(
        source="dense",
        raw_score=0.9,
        rank=1,
        weight=0.5,
        fusion_contribution=0.4,
    )
    assert score.source.value == "dense"
    assert score.rank == 1
    assert score.fusion_contribution == 0.4


def test_source_failure_event_carries_degraded_policy_and_duration() -> None:
    event = SourceFailureEvent(
        source="graph",
        reason_code="embedding_timeout",
        degraded_policy=EvidencePolicy.PROVENANCE_ONLY,
        duration_ms=12.5,
    )
    assert event.source.value == "graph"
    assert event.reason_code == "embedding_timeout"
    assert event.degraded_policy is EvidencePolicy.PROVENANCE_ONLY
    assert event.duration_ms == 12.5


def test_rendered_message_budget_carries_token_breakdown() -> None:
    budget = RenderedMessageBudget(
        content_tokens=100,
        prompt_overhead_tokens=20,
        total_input_tokens_estimate=120,
    )
    assert budget.content_tokens == 100
    assert budget.prompt_overhead_tokens == 20
    assert budget.total_input_tokens_estimate == 120


def test_retrieval_request_carries_contract_fields() -> None:
    request = RetrievalRequest(
        query="When did Caroline move?",
        user_id="u1",
        intent=QueryIntent.TEMPORAL,
        temporal_constraint=TemporalConstraint(operator="none"),
        evidence_policy=EvidencePolicy.CONSTRAINED,
        exclusions=[uuid4()],
        budget_tokens=2048,
    )
    assert request.intent is QueryIntent.TEMPORAL
    assert request.evidence_policy is EvidencePolicy.CONSTRAINED
    assert request.temporal_constraint.operator == "none"


def test_retrieval_result_carries_contract_fields() -> None:
    result = RetrievalResult(
        query="When did Caroline move?",
        user_id="u1",
        intent=QueryIntent.TEMPORAL,
        evidence_policy=EvidencePolicy.CONSTRAINED,
        temporal_constraint=TemporalConstraint(operator="none"),
        component_scores=[
            ComponentScore(
                source="dense",
                raw_score=0.9,
                rank=1,
                weight=0.5,
                fusion_contribution=0.4,
            )
        ],
        source_failures=[
            SourceFailureEvent(
                source="graph",
                reason_code="embedding_timeout",
                degraded_policy=EvidencePolicy.PROVENANCE_ONLY,
                duration_ms=12.5,
            )
        ],
        exclusions=[uuid4()],
        packed_items=[_packed_item()],
        budget=RenderedMessageBudget(
            content_tokens=100,
            prompt_overhead_tokens=20,
            total_input_tokens_estimate=120,
        ),
    )
    assert result.evidence_policy is EvidencePolicy.CONSTRAINED
    assert result.source_failures[0].reason_code == "embedding_timeout"
    assert result.packed_items[0].evidence_refs


@pytest.mark.parametrize("policy", [EvidencePolicy.CONSTRAINED, EvidencePolicy.PROVENANCE_ONLY])
def test_packed_item_rejects_empty_provenance_under_both_policies(policy: EvidencePolicy) -> None:
    with pytest.raises(ValidationError):
        PackedItem(
            memory=_memory(),
            component_scores={"dense": 0.5},
            final_score=0.5,
            token_count=5,
            evidence_refs=[],
            reason="packed under token budget",
        )


def test_budget_total_must_exceed_content() -> None:
    with pytest.raises(ValidationError):
        RetrievalResult(
            query="q",
            user_id="u1",
            intent=QueryIntent.TEMPORAL,
            evidence_policy=EvidencePolicy.CONSTRAINED,
            temporal_constraint=TemporalConstraint(operator="none"),
            budget=RenderedMessageBudget(
                content_tokens=100,
                prompt_overhead_tokens=20,
                total_input_tokens_estimate=50,
            ),
        )