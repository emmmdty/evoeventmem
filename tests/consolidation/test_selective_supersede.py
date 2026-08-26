from datetime import UTC, datetime

from evoeventmem.consolidation import (
    ConsolidationAction,
    ETECConsolidator,
)
from evoeventmem.domain.models import (
    EntityRef,
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
)
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.linking import (
    CandidateGenerationRequest,
    CandidateGenerationResult,
    LinkCandidate,
    LinkCandidateKind,
)
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel
from evoeventmem.router import QueryIntent


def _evidence(source_id: str) -> EvidenceRef:
    return EvidenceRef(source_type="turn", source_id=source_id, locator="messages[0]")


def _memory(
    memory_id: str,
    content: str,
    *,
    fact_slot: str,
    fact_value: str,
    valid_from: datetime,
    evidence_id: str,
    valid_to: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="u1",
        memory_kind=MemoryKind.FACT,
        content=content,
        entities=[EntityRef(name="Caroline", role="subject")],
        roles={"Caroline": "subject"},
        evidence_refs=[_evidence(evidence_id)],
        valid_from=valid_from,
        valid_to=valid_to,
        status=MemoryStatus.ACTIVE,
        metadata={
            "fact_slot": fact_slot,
            "fact_value": fact_value,
        },
    )


class _SingleCandidateGenerator:
    def __init__(self, target: MemoryRecord) -> None:
        self._target = target

    def generate(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        candidate = LinkCandidate(
            candidate_id=f"test:{self._target.memory_id}",
            candidate_kind=LinkCandidateKind.EVENT,
            policy_name="test-event-policy",
            source_memory=request.source,
            target_memory=self._target,
            score=1.0,
            reasons=["test_candidate"],
        )
        return CandidateGenerationResult(
            entity_candidates=[],
            event_candidates=[candidate],
            latency_ms=0.0,
            embedding_model_id="test-candidate-model",
        )


def _consolidator(
    routing_intent: QueryIntent | None = None,
) -> ETECConsolidator:
    return ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        routing_intent=routing_intent,
    )


def test_temporal_intent_downgrades_supersede_to_merge() -> None:
    repository = InMemoryMemoryRepository()
    old_city = _memory(
        "a0000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="temporal:1",
    )
    new_city = _memory(
        "a0000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="temporal:2",
    )
    repository.add(old_city)
    generator = _SingleCandidateGenerator(old_city)

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,
        routing_intent=QueryIntent.TEMPORAL,
    ).apply(repository, new_city)

    assert result.decision.action is ConsolidationAction.MERGE
    assert "temporal_intent_supersede_downgraded_to_merge" in result.decision.rule_hits
    merged = repository.get(old_city.memory_id)
    assert merged is not None
    assert merged.status is MemoryStatus.ACTIVE
    assert repository.get(new_city.memory_id) is None


def test_non_temporal_intent_allows_supersede() -> None:
    repository = InMemoryMemoryRepository()
    old_city = _memory(
        "b0000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="semantic:1",
    )
    new_city = _memory(
        "b0000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="semantic:2",
    )
    repository.add(old_city)
    generator = _SingleCandidateGenerator(old_city)

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,
        routing_intent=QueryIntent.SEMANTIC,
    ).apply(repository, new_city)

    assert result.decision.action is ConsolidationAction.SUPERSEDE
    assert "temporal_intent_supersede_downgraded_to_merge" not in result.decision.rule_hits
    superseded = repository.get(old_city.memory_id)
    stored = repository.get(new_city.memory_id)
    assert superseded is not None
    assert stored is not None
    assert superseded.status is MemoryStatus.SUPERSEDED
    assert stored.status is MemoryStatus.ACTIVE


def test_no_routing_intent_allows_supersede() -> None:
    repository = InMemoryMemoryRepository()
    old_city = _memory(
        "c0000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="none:1",
    )
    new_city = _memory(
        "c0000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="none:2",
    )
    repository.add(old_city)
    generator = _SingleCandidateGenerator(old_city)

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,
    ).apply(repository, new_city)

    assert result.decision.action is ConsolidationAction.SUPERSEDE
    assert "temporal_intent_supersede_downgraded_to_merge" not in result.decision.rule_hits


def test_temporal_intent_does_not_affect_add_path() -> None:
    repository = InMemoryMemoryRepository()
    incoming = _memory(
        "d0000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="add:1",
    )

    result = _consolidator(routing_intent=QueryIntent.TEMPORAL).apply(repository, incoming)

    assert result.decision.action is ConsolidationAction.ADD
    assert "temporal_intent_supersede_downgraded_to_merge" not in result.decision.rule_hits
    stored = repository.get(incoming.memory_id)
    assert stored is not None
    assert stored.status is MemoryStatus.ACTIVE


def test_temporal_intent_does_not_affect_merge_path() -> None:
    repository = InMemoryMemoryRepository()
    existing = _memory(
        "e0000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="merge:1",
    )
    incoming = _memory(
        "e0000000-0000-0000-0000-000000000002",
        "Carrie lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 2, tzinfo=UTC),
        evidence_id="merge:2",
    )
    repository.add(existing)
    generator = _SingleCandidateGenerator(existing)

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,
        routing_intent=QueryIntent.TEMPORAL,
    ).apply(repository, incoming)

    assert result.decision.action is ConsolidationAction.MERGE
    assert "temporal_intent_supersede_downgraded_to_merge" not in result.decision.rule_hits
    merged = repository.get(existing.memory_id)
    assert merged is not None
    assert repository.get(incoming.memory_id) is None
