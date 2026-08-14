from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

import evoeventmem.consolidation as consolidation_module
from evoeventmem.consolidation import (
    ConsolidationAction,
    ETECConsolidator,
    ETECThresholds,
)
from evoeventmem.core.ports import EmbeddingResponse, MemoryRepository
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


def _evidence(source_id: str) -> EvidenceRef:
    return EvidenceRef(source_type="turn", source_id=source_id, locator="messages[0]")


def _memory(
    memory_id: str,
    content: str,
    *,
    fact_slot: str | None,
    fact_value: str,
    valid_from: datetime | None,
    evidence_id: str,
    valid_to: datetime | None = None,
    event_time: datetime | None = None,
    memory_kind: MemoryKind = MemoryKind.FACT,
    tenant_id: str | None = None,
    user_id: str = "u1",
    status: MemoryStatus = MemoryStatus.ACTIVE,
    supersedes: list[UUID] | None = None,
    superseded_by: UUID | None = None,
    derived_from: list[UUID] | None = None,
    multi_valued: bool = False,
    synthetic: bool = False,
) -> MemoryRecord:
    evidence_refs = [] if synthetic else [_evidence(evidence_id)]
    metadata: dict[str, object] = {
        "fact_value": fact_value,
        "multi_valued": multi_valued,
    }
    if fact_slot is not None:
        metadata["fact_slot"] = fact_slot
    return MemoryRecord(
        memory_id=UUID(memory_id),
        tenant_id=tenant_id,
        user_id=user_id,
        memory_kind=memory_kind,
        content=content,
        entities=[EntityRef(name="Caroline", role="subject")],
        roles={"Caroline": "subject"},
        evidence_refs=evidence_refs,
        event_time=event_time,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        supersedes=supersedes or [],
        superseded_by=superseded_by,
        derived_from=derived_from or [],
        metadata=metadata,
        synthetic=synthetic,
    )


def _consolidator() -> ETECConsolidator:
    return ETECConsolidator(embedding_model=DeterministicFakeEmbeddingModel())


class _RecordingCandidateGenerator:
    def __init__(self, targets: Sequence[MemoryRecord], *, duplicate: bool = False) -> None:
        self._targets = list(targets)
        self._duplicate = duplicate
        self.requests: list[CandidateGenerationRequest] = []

    def generate(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        self.requests.append(request)
        candidates = [
            LinkCandidate(
                candidate_id=f"test:{target.memory_id}",
                candidate_kind=LinkCandidateKind.EVENT,
                policy_name="test-event-policy",
                source_memory=request.source,
                target_memory=target,
                score=1.0,
                reasons=["test_candidate"],
            )
            for target in self._targets
        ]
        return CandidateGenerationResult(
            entity_candidates=list(candidates) if self._duplicate else [],
            event_candidates=candidates,
            latency_ms=0.0,
            embedding_model_id="test-candidate-model",
        )


class _CountingEmbeddingModel:
    model_id = "counting-embedding"

    def __init__(self) -> None:
        self._wrapped = DeterministicFakeEmbeddingModel(model_id=self.model_id)
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        self.calls.append(list(texts))
        return self._wrapped.embed_texts(texts)


class _MappedEmbeddingModel:
    model_id = "mapped-embedding"

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        vectors = {
            "Caroline lives in Boston.": (1.0, 0.0),
            "Caroline lives in Seattle.": (1.0, 0.0),
            "Caroline lives in Austin.": (0.0, 1.0),
        }
        return [
            EmbeddingResponse(vector=vectors.get(text, (0.5, 0.5)), model_id=self.model_id)
            for text in texts
        ]


class _FailingTransaction:
    def __init__(self, delegate: MemoryRepository) -> None:
        self._delegate = delegate
        self._writes = 0

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        self._writes += 1
        if self._writes == 2:
            raise RuntimeError("injected repository failure")
        return self._delegate.add(memory)

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        return self._delegate.get(memory_id)

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        return self._delegate.list_for_user(user_id)

    def transaction(self):  # type: ignore[no-untyped-def]
        return self._delegate.transaction()


class _FailingOnSecondWriteRepository:
    def __init__(self) -> None:
        self._delegate = InMemoryMemoryRepository()

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        return self._delegate.add(memory)

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        return self._delegate.get(memory_id)

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        return self._delegate.list_for_user(user_id)

    @contextmanager
    def transaction(self) -> Iterator[MemoryRepository]:
        with self._delegate.transaction() as transaction:
            yield _FailingTransaction(transaction)


def test_add_path_stores_new_memory_with_decision_features() -> None:
    repository = InMemoryMemoryRepository()
    incoming = _memory(
        "10000000-0000-0000-0000-000000000001",
        "Caroline prefers UTC timestamps.",
        fact_slot="preference.timezone",
        fact_value="UTC timestamps",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="add:1",
    )

    result = _consolidator().apply(repository, incoming)

    assert result.decision.action is ConsolidationAction.ADD
    stored = repository.get(incoming.memory_id)
    assert stored is not None
    assert stored.metadata["etec"]["decision"]["features"]["evidence_consistency"] == 1.0
    assert stored.status is MemoryStatus.ACTIVE


def test_merge_path_combines_evidence_and_persists_rule_hits() -> None:
    repository = InMemoryMemoryRepository()
    existing = _memory(
        "20000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="merge:1",
    )
    incoming = _memory(
        "20000000-0000-0000-0000-000000000002",
        "Carrie lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 2, tzinfo=UTC),
        evidence_id="merge:2",
    )
    repository.add(existing)

    result = _consolidator().apply(repository, incoming)

    assert result.decision.action is ConsolidationAction.MERGE
    merged = repository.get(existing.memory_id)
    assert merged is not None
    assert repository.get(incoming.memory_id) is None
    assert [evidence.source_id for evidence in merged.evidence_refs] == ["merge:1", "merge:2"]
    assert "duplicate_fact" in merged.metadata["etec"]["decision"]["rule_hits"]
    assert merged.metadata["etec"]["decision"]["thresholds"]["merge_score_min"] == 0.68


def test_forged_normalized_content_cannot_hide_a_fact_contradiction() -> None:
    repository = InMemoryMemoryRepository()
    existing = _memory(
        "20000000-0000-0000-0000-000000000011",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="normalized:1",
    ).model_copy(
        update={
            "normalized_content": "forged shared value",
            "metadata": {"fact_slot": "profile.city"},
        }
    )
    incoming = _memory(
        "20000000-0000-0000-0000-000000000012",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="normalized:2",
    ).model_copy(
        update={
            "normalized_content": "forged shared value",
            "metadata": {"fact_slot": "profile.city"},
        }
    )
    repository.add(existing)

    result = _consolidator().apply(repository, incoming)

    assert consolidation_module.fact_value_key(existing) == "caroline lives in seattle."
    assert consolidation_module.fact_value_key(incoming) == "caroline lives in boston."
    assert result.decision.action is ConsolidationAction.SUPERSEDE
    assert result.decision.features.contradiction_score >= 0.7


def test_supersede_path_closes_temporal_contradiction() -> None:
    repository = InMemoryMemoryRepository()
    old_city = _memory(
        "30000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="supersede:1",
    )
    new_city = _memory(
        "30000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="supersede:2",
    )
    repository.add(old_city)

    result = _consolidator().apply(repository, new_city)

    assert result.decision.action is ConsolidationAction.SUPERSEDE
    superseded = repository.get(old_city.memory_id)
    stored = repository.get(new_city.memory_id)
    assert superseded is not None
    assert stored is not None
    assert superseded.status is MemoryStatus.SUPERSEDED
    assert superseded.valid_to == datetime(2024, 3, 1, tzinfo=UTC)
    assert superseded.superseded_by == new_city.memory_id
    assert stored.supersedes == [old_city.memory_id]
    active_city_facts = [
        memory
        for memory in repository.list_for_user("u1")
        if memory.status is MemoryStatus.ACTIVE
        and memory.metadata.get("fact_slot") == "profile.city"
        and memory.valid_to is None
    ]
    assert active_city_facts == [stored]


def test_reject_path_does_not_store_evidence_free_synthetic_memory() -> None:
    repository = InMemoryMemoryRepository()
    incoming = _memory(
        "40000000-0000-0000-0000-000000000001",
        "Synthetic unsupported memory.",
        fact_slot="profile.unsupported",
        fact_value="unsupported",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="unused",
        synthetic=True,
    )

    result = _consolidator().apply(repository, incoming)

    assert result.decision.action is ConsolidationAction.REJECT
    assert result.updated_memories == []
    assert repository.get(incoming.memory_id) is None


def test_explicit_multi_valued_slot_can_keep_multiple_current_active_facts() -> None:
    repository = InMemoryMemoryRepository()
    old_number = _memory(
        "50000000-0000-0000-0000-000000000001",
        "Caroline uses phone number 111.",
        fact_slot="profile.phone",
        fact_value="111",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="multi:1",
        multi_valued=True,
    )
    new_number = _memory(
        "50000000-0000-0000-0000-000000000002",
        "Caroline uses phone number 222.",
        fact_slot="profile.phone",
        fact_value="222",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="multi:2",
        multi_valued=True,
    )
    repository.add(old_number)

    result = _consolidator().apply(repository, new_number)

    assert result.decision.action is ConsolidationAction.ADD
    active_phone_facts = [
        memory
        for memory in repository.list_for_user("u1")
        if memory.status is MemoryStatus.ACTIVE
        and memory.metadata.get("fact_slot") == "profile.phone"
        and memory.valid_to is None
    ]
    assert {memory.memory_id for memory in active_phone_facts} == {
        old_number.memory_id,
        new_number.memory_id,
    }


def test_positional_threshold_constructor_remains_compatible() -> None:
    thresholds = ETECThresholds(merge_score_min=0.91)
    incoming = _memory(
        "60000000-0000-0000-0000-000000000001",
        "Caroline uses UTC.",
        fact_slot="preference.timezone",
        fact_value="UTC",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="constructor:1",
    )

    decision = ETECConsolidator(DeterministicFakeEmbeddingModel(), thresholds).decide(incoming, [])

    assert decision.thresholds.merge_score_min == 0.91


def test_public_fact_keys_use_explicit_normalized_metadata() -> None:
    memory = _memory(
        "60000000-0000-0000-0000-000000000002",
        "Caroline lives in Seattle.",
        fact_slot=" Profile.City ",
        fact_value=" Seattle ",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="keys:1",
    )
    event_without_slot = _memory(
        "60000000-0000-0000-0000-000000000003",
        "Caroline presented a paper.",
        fact_slot=None,
        fact_value="presented",
        valid_from=None,
        event_time=datetime(2024, 1, 2, tzinfo=UTC),
        memory_kind=MemoryKind.EVENT,
        evidence_id="keys:2",
    )

    assert consolidation_module.fact_slot_key(memory) == "profile city"
    assert consolidation_module.fact_value_key(memory) == "seattle"
    assert consolidation_module.fact_slot_key(event_without_slot) is None


def test_apply_calls_m09_and_scores_only_deduplicated_returned_targets() -> None:
    repository = InMemoryMemoryRepository()
    returned = _memory(
        "61000000-0000-0000-0000-000000000001",
        "Caroline likes detailed logs.",
        fact_slot="preference.logs",
        fact_value="detailed",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="bounded:1",
    )
    unbounded_conflict = _memory(
        "61000000-0000-0000-0000-000000000002",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="bounded:2",
    )
    incoming = _memory(
        "61000000-0000-0000-0000-000000000003",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="bounded:3",
    )
    repository.add(returned)
    repository.add(unbounded_conflict)
    generator = _RecordingCandidateGenerator([returned], duplicate=True)
    embeddings = _CountingEmbeddingModel()
    consolidator = ETECConsolidator(
        embeddings,
        ETECThresholds(merge_semantic_min=1.0, merge_score_min=1.0),
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    result = consolidator.apply(repository, incoming)

    assert len(generator.requests) == 1
    assert {item.memory_id for item in generator.requests[0].existing} == {
        returned.memory_id,
        unbounded_conflict.memory_id,
    }
    assert embeddings.calls == [[incoming.content, returned.content]]
    assert result.decision.action is ConsolidationAction.ADD
    assert repository.get(unbounded_conflict.memory_id) == unbounded_conflict


def test_explicit_candidates_are_filtered_before_indexing_scoring_or_mutation() -> None:
    repository = InMemoryMemoryRepository()
    incoming = _memory(
        "62000000-0000-0000-0000-000000000001",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="filter:source",
        tenant_id="tenant-a",
    )
    safe = _memory(
        "62000000-0000-0000-0000-000000000002",
        "Caroline prefers detailed logs.",
        fact_slot="preference.logs",
        fact_value="detailed",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="filter:safe",
        tenant_id="tenant-a",
    )
    wrong_tenant = _memory(
        "62000000-0000-0000-0000-000000000003",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="filter:tenant",
        tenant_id="tenant-b",
    )
    wrong_user = _memory(
        "62000000-0000-0000-0000-000000000004",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="filter:user",
        tenant_id="tenant-a",
        user_id="u2",
    )
    deleted = _memory(
        "62000000-0000-0000-0000-000000000005",
        "Caroline lives in Paris.",
        fact_slot="profile.city",
        fact_value="Paris",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="filter:deleted",
        tenant_id="tenant-a",
        status=MemoryStatus.DELETED,
    )
    for target in (safe, wrong_tenant, wrong_user, deleted):
        repository.add(target)
    wrong_tenant_snapshot = wrong_tenant.model_copy(deep=True)
    generator = _RecordingCandidateGenerator([safe, wrong_tenant, wrong_user, deleted, incoming])
    embeddings = _CountingEmbeddingModel()
    consolidator = ETECConsolidator(
        embeddings,
        ETECThresholds(merge_semantic_min=1.0, merge_score_min=1.0),
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    result = consolidator.apply(
        repository,
        incoming,
        candidates=[safe, wrong_tenant, wrong_user, deleted, incoming],
    )

    assert [item.memory_id for item in generator.requests[0].existing] == [safe.memory_id]
    assert embeddings.calls == [[incoming.content, safe.content]]
    assert result.decision.action is ConsolidationAction.ADD
    assert repository.get(wrong_tenant.memory_id) == wrong_tenant_snapshot


def test_explicit_candidate_snapshot_cannot_spoof_durable_tenant_scope() -> None:
    repository = InMemoryMemoryRepository()
    source = _memory(
        "62500000-0000-0000-0000-000000000001",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="spoof:source",
        tenant_id="tenant-a",
    )
    durable_target = _memory(
        "62500000-0000-0000-0000-000000000002",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="spoof:durable",
        tenant_id="tenant-b",
    )
    spoofed_snapshot = durable_target.model_copy(update={"tenant_id": "tenant-a"})
    repository.add(durable_target)
    durable_snapshot = durable_target.model_copy(deep=True)
    generator = _RecordingCandidateGenerator([spoofed_snapshot])
    embeddings = _CountingEmbeddingModel()
    consolidator = ETECConsolidator(
        embeddings,
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    result = consolidator.apply(repository, source, candidates=[spoofed_snapshot])

    assert generator.requests[0].existing == []
    assert embeddings.calls == []
    assert result.decision.action is ConsolidationAction.ADD
    assert repository.get(durable_target.memory_id) == durable_snapshot


def test_add_point_event_preserves_event_time_without_validity_interval() -> None:
    repository = InMemoryMemoryRepository()
    event_time = datetime(2024, 4, 5, 12, tzinfo=UTC)
    incoming = _memory(
        "63000000-0000-0000-0000-000000000001",
        "Caroline presented the paper.",
        fact_slot=None,
        fact_value="presented",
        valid_from=None,
        event_time=event_time,
        memory_kind=MemoryKind.EVENT,
        evidence_id="point:1",
    )

    result = _consolidator().apply(repository, incoming)

    stored = repository.get(incoming.memory_id)
    assert result.decision.action is ConsolidationAction.ADD
    assert stored is not None
    assert stored.event_time == event_time
    assert stored.valid_from is None
    assert stored.valid_to is None


def test_entity_and_role_overlap_cannot_make_unrelated_events_conflict() -> None:
    repository = InMemoryMemoryRepository()
    earlier = _memory(
        "64000000-0000-0000-0000-000000000001",
        "Caroline presented a paper.",
        fact_slot=None,
        fact_value="presented",
        valid_from=None,
        event_time=datetime(2024, 1, 1, tzinfo=UTC),
        memory_kind=MemoryKind.EVENT,
        evidence_id="event:1",
    )
    later = _memory(
        "64000000-0000-0000-0000-000000000002",
        "Caroline attended a workshop.",
        fact_slot=None,
        fact_value="attended",
        valid_from=None,
        event_time=datetime(2024, 1, 1, tzinfo=UTC),
        memory_kind=MemoryKind.EVENT,
        evidence_id="event:2",
    )
    repository.add(earlier)
    generator = _RecordingCandidateGenerator([earlier])
    consolidator = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        ETECThresholds(merge_semantic_min=1.0, merge_score_min=1.0),
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    result = consolidator.apply(repository, later)

    assert result.decision.action is ConsolidationAction.ADD
    assert repository.get(earlier.memory_id).status is MemoryStatus.ACTIVE  # type: ignore[union-attr]
    assert repository.get(later.memory_id).status is MemoryStatus.ACTIVE  # type: ignore[union-attr]


def test_newer_fact_closes_older_target_with_reciprocal_links() -> None:
    repository = InMemoryMemoryRepository()
    older = _memory(
        "65000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="newer:1",
    )
    newer = _memory(
        "65000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="newer:2",
    )
    repository.add(older)
    generator = _RecordingCandidateGenerator([older])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, newer)

    stored_older = repository.get(older.memory_id)
    stored_newer = repository.get(newer.memory_id)
    assert result.decision.action is ConsolidationAction.SUPERSEDE
    assert "newer_source_supersedes_older_target" in result.decision.rule_hits
    assert stored_older is not None and stored_newer is not None
    assert stored_older.status is MemoryStatus.SUPERSEDED
    assert stored_older.valid_to == newer.valid_from
    assert stored_older.superseded_by == newer.memory_id
    assert stored_newer.status is MemoryStatus.ACTIVE
    assert stored_newer.supersedes == [older.memory_id]


def test_stale_fact_is_stored_closed_and_newer_target_gains_reciprocal_link() -> None:
    repository = InMemoryMemoryRepository()
    newer_target = _memory(
        "66000000-0000-0000-0000-000000000001",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="stale:1",
    )
    stale_source = _memory(
        "66000000-0000-0000-0000-000000000002",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="stale:2",
    )
    repository.add(newer_target)
    generator = _RecordingCandidateGenerator([newer_target])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, stale_source)

    stored_target = repository.get(newer_target.memory_id)
    stored_source = repository.get(stale_source.memory_id)
    assert result.decision.action is ConsolidationAction.SUPERSEDE
    assert "stale_source_superseded_by_newer_target" in result.decision.rule_hits
    assert stored_target is not None and stored_source is not None
    assert stored_target.status is MemoryStatus.ACTIVE
    assert stored_target.supersedes == [stale_source.memory_id]
    assert stored_source.status is MemoryStatus.SUPERSEDED
    assert stored_source.valid_to == newer_target.valid_from
    assert stored_source.superseded_by == newer_target.memory_id
    assert {memory.memory_id for memory in result.updated_memories} == {
        newer_target.memory_id,
        stale_source.memory_id,
    }


@pytest.mark.parametrize(
    ("source_time", "target_time", "rule_hit"),
    [
        (None, None, "missing_fact_effective_time"),
        (
            datetime(2024, 2, 1, tzinfo=UTC),
            datetime(2024, 2, 1, tzinfo=UTC),
            "equal_fact_effective_time",
        ),
    ],
)
def test_ambiguous_fact_order_rejects_without_mutation(
    source_time: datetime | None,
    target_time: datetime | None,
    rule_hit: str,
) -> None:
    repository = InMemoryMemoryRepository()
    target = _memory(
        "67000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=target_time,
        evidence_id="ambiguous:1",
    )
    source = _memory(
        "67000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=source_time,
        evidence_id="ambiguous:2",
    )
    repository.add(target)
    snapshot = target.model_copy(deep=True)
    generator = _RecordingCandidateGenerator([target])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, source)

    assert result.decision.action is ConsolidationAction.REJECT
    assert rule_hit in result.decision.rule_hits
    assert repository.get(target.memory_id) == snapshot
    assert repository.get(source.memory_id) is None
    assert result.updated_memories == []


def test_repository_failure_rolls_back_every_supersession_write() -> None:
    repository = _FailingOnSecondWriteRepository()
    target = _memory(
        "68000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="rollback:1",
    )
    source = _memory(
        "68000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="rollback:2",
    )
    repository.add(target)
    generator = _RecordingCandidateGenerator([target])
    consolidator = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="injected repository failure"):
        consolidator.apply(repository, source)

    assert repository.get(target.memory_id) == target
    assert repository.get(source.memory_id) is None


def test_concurrent_contradictory_writes_leave_newest_fact_current() -> None:
    repository = InMemoryMemoryRepository()
    original = _memory(
        "69000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="concurrent:1",
    )
    middle = _memory(
        "69000000-0000-0000-0000-000000000002",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="concurrent:2",
    )
    newest = _memory(
        "69000000-0000-0000-0000-000000000003",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="concurrent:3",
    )
    repository.add(original)
    consolidator = ETECConsolidator(DeterministicFakeEmbeddingModel())

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda memory: consolidator.apply(repository, memory), [middle, newest]))

    current = [
        memory
        for memory in repository.list_for_user("u1")
        if memory.status is MemoryStatus.ACTIVE
        and consolidation_module.fact_slot_key(memory) == "profile city"
        and memory.valid_to is None
    ]
    assert [memory.memory_id for memory in current] == [newest.memory_id]


def test_each_superseded_target_records_its_own_decision_features() -> None:
    repository = InMemoryMemoryRepository()
    seattle = _memory(
        "6a000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="metadata:1",
    )
    austin = _memory(
        "6a000000-0000-0000-0000-000000000002",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="metadata:2",
    )
    boston = _memory(
        "6a000000-0000-0000-0000-000000000003",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="metadata:3",
    )
    repository.add(seattle)
    repository.add(austin)
    generator = _RecordingCandidateGenerator([seattle, austin], duplicate=True)

    ETECConsolidator(
        _MappedEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, boston)

    stored_seattle = repository.get(seattle.memory_id)
    stored_austin = repository.get(austin.memory_id)
    assert stored_seattle is not None and stored_austin is not None
    seattle_decision = stored_seattle.metadata["etec"]["decision"]
    austin_decision = stored_austin.metadata["etec"]["decision"]
    assert seattle_decision["target_memory_id"] == str(seattle.memory_id)
    assert austin_decision["target_memory_id"] == str(austin.memory_id)
    assert seattle_decision["features"]["semantic_similarity"] == 1.0
    assert austin_decision["features"]["semantic_similarity"] == 0.0


def test_scored_pair_uses_one_embedding_batch_for_both_vectors() -> None:
    repository = InMemoryMemoryRepository()
    target = _memory(
        "6b000000-0000-0000-0000-000000000001",
        "Caroline likes Seattle.",
        fact_slot="preference.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="embedding:1",
    )
    source = _memory(
        "6b000000-0000-0000-0000-000000000002",
        "Caroline likes Boston.",
        fact_slot="preference.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="embedding:2",
    )
    repository.add(target)
    generator = _RecordingCandidateGenerator([target], duplicate=True)
    embeddings = _CountingEmbeddingModel()

    ETECConsolidator(
        embeddings,
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, source)

    assert embeddings.calls == [[source.content, target.content]]


def test_inactive_source_rejects_before_candidate_generation_or_write() -> None:
    repository = InMemoryMemoryRepository()
    source = _memory(
        "6c000000-0000-0000-0000-000000000001",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="inactive:1",
        status=MemoryStatus.DELETED,
    )
    generator = _RecordingCandidateGenerator([])
    embeddings = _CountingEmbeddingModel()
    consolidator = ETECConsolidator(
        embeddings,
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    result = consolidator.apply(repository, source)

    assert result.decision.action is ConsolidationAction.REJECT
    assert result.decision.rule_hits == ["inactive_source"]
    assert "active" in result.decision.reason.casefold()
    assert generator.requests == []
    assert embeddings.calls == []
    assert repository.get(source.memory_id) is None
    assert result.updated_memories == []


@pytest.mark.parametrize(
    ("durable_tenant", "durable_user", "durable_status"),
    [
        ("tenant-a", "u1", MemoryStatus.ACTIVE),
        ("tenant-b", "u2", MemoryStatus.DELETED),
    ],
)
def test_existing_source_id_rejects_without_trusting_caller_record(
    durable_tenant: str,
    durable_user: str,
    durable_status: MemoryStatus,
) -> None:
    repository = InMemoryMemoryRepository()
    memory_id = "6d000000-0000-0000-0000-000000000001"
    durable = _memory(
        memory_id,
        "Durable repository content.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="collision:durable",
        tenant_id=durable_tenant,
        user_id=durable_user,
        status=durable_status,
    )
    source = _memory(
        memory_id,
        "Untrusted caller replacement.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 2, 1, tzinfo=UTC),
        evidence_id="collision:source",
        tenant_id="tenant-a",
    )
    repository.add(durable)
    durable_snapshot = durable.model_copy(deep=True)
    generator = _RecordingCandidateGenerator([])
    embeddings = _CountingEmbeddingModel()
    consolidator = ETECConsolidator(
        embeddings,
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    result = consolidator.apply(repository, source)

    assert result.decision.action is ConsolidationAction.REJECT
    assert result.decision.rule_hits == ["source_memory_id_collision"]
    assert "already exists" in result.decision.reason.casefold()
    assert generator.requests == []
    assert embeddings.calls == []
    assert repository.get(source.memory_id) == durable_snapshot
    assert result.updated_memories == []


def test_same_value_disjoint_closed_intervals_add_separate_history() -> None:
    repository = InMemoryMemoryRepository()
    historical = _memory(
        "6e000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2020, 12, 31, tzinfo=UTC),
        evidence_id="disjoint:1",
    )
    later = _memory(
        "6e000000-0000-0000-0000-000000000002",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=datetime(2024, 12, 31, tzinfo=UTC),
        evidence_id="disjoint:2",
    )
    repository.add(historical)
    generator = _RecordingCandidateGenerator([historical])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, later)

    assert result.decision.action is ConsolidationAction.ADD
    assert "disjoint_temporal_intervals" in result.decision.rule_hits
    assert repository.get(historical.memory_id) == historical
    assert repository.get(later.memory_id) is not None


def test_merge_requires_same_fact_value_preserving_distinct_facts() -> None:
    repository = InMemoryMemoryRepository()
    packed = _memory(
        "7d000000-0000-0000-0000-000000000001",
        "On the trip, the user packed 7 shirts.",
        fact_slot="trip.packed",
        fact_value="7 shirts",
        valid_from=datetime(2024, 3, 1, tzinfo=UTC),
        evidence_id="packed:1",
    )
    worn = _memory(
        "7d000000-0000-0000-0000-000000000002",
        "On the trip, the user wore 3 shirts.",
        fact_slot="trip.worn",
        fact_value="3 shirts",
        valid_from=datetime(2024, 3, 2, tzinfo=UTC),
        evidence_id="worn:2",
    )
    repository.add(packed)

    result = _consolidator().apply(repository, worn)

    assert result.decision.action is ConsolidationAction.ADD
    assert "distinct_fact_value" in result.decision.rule_hits
    assert repository.get(packed.memory_id) is not None
    assert repository.get(worn.memory_id) is not None
    assert packed.content in repository.get(packed.memory_id).content  # type: ignore[arg-type]
    assert worn.content in repository.get(worn.memory_id).content  # type: ignore[arg-type]


def test_merge_preserves_transitive_derived_from_provenance() -> None:
    repository = InMemoryMemoryRepository()
    target_parent = UUID("6f000000-0000-0000-0000-000000000010")
    source_parent = UUID("6f000000-0000-0000-0000-000000000020")
    target = _memory(
        "6f000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="provenance:1",
        derived_from=[target_parent],
    )
    source = _memory(
        "6f000000-0000-0000-0000-000000000002",
        "Carrie lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 2, tzinfo=UTC),
        evidence_id="provenance:2",
        derived_from=[source_parent],
    )
    repository.add(target)
    generator = _RecordingCandidateGenerator([target])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, source)

    merged = repository.get(target.memory_id)
    expected = [target_parent, source_parent, source.memory_id]
    assert result.decision.action is ConsolidationAction.MERGE
    assert merged is not None
    assert merged.derived_from == expected
    assert merged.metadata["merged_source_memory_ids"] == [str(item) for item in expected]


def test_stale_multi_target_uses_actual_winner_target_decision() -> None:
    repository = InMemoryMemoryRepository()
    target_2024 = _memory(
        "70000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="actual-pair:2024",
    )
    target_2025 = _memory(
        "70000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="actual-pair:2025",
    )
    source_2023 = _memory(
        "70000000-0000-0000-0000-000000000003",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2023, 1, 1, tzinfo=UTC),
        evidence_id="actual-pair:2023",
    )
    repository.add(target_2024)
    repository.add(target_2025)
    generator = _RecordingCandidateGenerator([target_2024, target_2025])

    ETECConsolidator(
        _MappedEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, source_2023)

    stored_2024 = repository.get(target_2024.memory_id)
    stored_2025 = repository.get(target_2025.memory_id)
    stored_source = repository.get(source_2023.memory_id)
    assert stored_2024 is not None and stored_2025 is not None and stored_source is not None
    assert stored_2024.status is MemoryStatus.SUPERSEDED
    assert stored_2024.superseded_by == target_2025.memory_id
    assert stored_2025.status is MemoryStatus.ACTIVE
    assert stored_source.superseded_by == target_2025.memory_id
    decision = stored_2024.metadata["etec"]["decision"]
    assert decision["source_memory_id"] == str(target_2025.memory_id)
    assert decision["target_memory_id"] == str(target_2024.memory_id)
    assert decision["features"]["semantic_similarity"] == 1.0
    assert "newer_source_supersedes_older_target" in decision["rule_hits"]


def test_stale_duplicate_target_records_explicit_cleanup_supersession() -> None:
    repository = InMemoryMemoryRepository()
    intermediate = _memory(
        "71000000-0000-0000-0000-000000000001",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="cleanup:2024",
    )
    winner = _memory(
        "71000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="cleanup:2025",
    )
    stale_source = _memory(
        "71000000-0000-0000-0000-000000000003",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2023, 1, 1, tzinfo=UTC),
        evidence_id="cleanup:2023",
    )
    repository.add(intermediate)
    repository.add(winner)
    generator = _RecordingCandidateGenerator([intermediate, winner])
    consolidator = ETECConsolidator(
        _MappedEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    )
    actual_pair = consolidator.decide(winner, [intermediate])
    assert actual_pair.action is ConsolidationAction.MERGE

    consolidator.apply(repository, stale_source)

    stored_intermediate = repository.get(intermediate.memory_id)
    stored_winner = repository.get(winner.memory_id)
    assert stored_intermediate is not None and stored_winner is not None
    assert stored_intermediate.status is MemoryStatus.SUPERSEDED
    assert stored_intermediate.superseded_by == winner.memory_id
    assert stored_winner.status is MemoryStatus.ACTIVE
    decision = stored_intermediate.metadata["etec"]["decision"]
    assert decision["action"] == ConsolidationAction.SUPERSEDE.value
    assert decision["source_memory_id"] == str(winner.memory_id)
    assert decision["target_memory_id"] == str(intermediate.memory_id)
    assert decision["features"] == actual_pair.features.model_dump(mode="json")
    assert decision["thresholds"] == actual_pair.thresholds.model_dump(mode="json")
    assert "duplicate_fact" in decision["rule_hits"]
    assert "duplicate_current_fact_cleanup" in decision["rule_hits"]
    assert "cleanup" in decision["reason"].casefold()


def test_equal_time_current_winners_reject_stale_source_without_mutation() -> None:
    target_a = _memory(
        "72000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="ambiguous-winner:a",
    )
    target_b = _memory(
        "72000000-0000-0000-0000-000000000002",
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="ambiguous-winner:b",
    )
    stale_source = _memory(
        "72000000-0000-0000-0000-000000000003",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2023, 1, 1, tzinfo=UTC),
        evidence_id="ambiguous-winner:source",
    )
    decisions: list[dict[str, object]] = []

    for ordered_targets in ([target_a, target_b], [target_b, target_a]):
        repository = InMemoryMemoryRepository()
        repository.add(target_a)
        repository.add(target_b)
        target_a_snapshot = target_a.model_copy(deep=True)
        target_b_snapshot = target_b.model_copy(deep=True)
        generator = _RecordingCandidateGenerator(ordered_targets)
        result = ETECConsolidator(
            _MappedEmbeddingModel(),
            candidate_generator=generator,  # type: ignore[arg-type]
        ).apply(repository, stale_source)

        assert result.decision.action is ConsolidationAction.REJECT
        assert result.decision.source_memory_id == stale_source.memory_id
        assert result.decision.target_memory_id == target_b.memory_id
        assert "equal_fact_effective_time" in result.decision.rule_hits
        assert "ambiguous_current_fact_winners" in result.decision.rule_hits
        assert "ambiguous" in result.decision.reason.casefold()
        assert "equal effective time" in result.decision.reason.casefold()
        assert result.stored_memory is None
        assert result.updated_memories == []
        assert repository.get(stale_source.memory_id) is None
        assert repository.get(target_a.memory_id) == target_a_snapshot
        assert repository.get(target_b.memory_id) == target_b_snapshot
        decisions.append(result.decision.model_dump(mode="json"))

    assert decisions[0] == decisions[1]


def test_add_sanitizes_caller_supersedes_to_verified_reciprocal_history() -> None:
    repository = InMemoryMemoryRepository()
    source_id = UUID("73000000-0000-0000-0000-000000000001")
    other_source_id = UUID("73000000-0000-0000-0000-000000000099")
    valid_history = _memory(
        "73000000-0000-0000-0000-000000000010",
        "Verified historical memory.",
        fact_slot="history.verified",
        fact_value="verified",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-add:valid",
        tenant_id="tenant-a",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    cross_tenant = _memory(
        "73000000-0000-0000-0000-000000000011",
        "Foreign historical memory.",
        fact_slot="history.foreign",
        fact_value="foreign",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-add:tenant",
        tenant_id="tenant-b",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    wrong_user = _memory(
        "73000000-0000-0000-0000-000000000012",
        "Another user's historical memory.",
        fact_slot="history.user",
        fact_value="other-user",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-add:user",
        tenant_id="tenant-a",
        user_id="u2",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    active_target = _memory(
        "73000000-0000-0000-0000-000000000013",
        "Still active memory.",
        fact_slot="history.active",
        fact_value="active",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-add:active",
        tenant_id="tenant-a",
    )
    nonreciprocal = _memory(
        "73000000-0000-0000-0000-000000000014",
        "Superseded by a different memory.",
        fact_slot="history.nonreciprocal",
        fact_value="nonreciprocal",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-add:nonreciprocal",
        tenant_id="tenant-a",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=other_source_id,
    )
    missing_id = UUID("73000000-0000-0000-0000-000000000015")
    source = _memory(
        str(source_id),
        "Caroline prefers UTC timestamps.",
        fact_slot="preference.timezone",
        fact_value="UTC",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-add:source",
        tenant_id="tenant-a",
        supersedes=[
            cross_tenant.memory_id,
            wrong_user.memory_id,
            missing_id,
            active_target.memory_id,
            nonreciprocal.memory_id,
            valid_history.memory_id,
        ],
    )
    for memory in (valid_history, cross_tenant, wrong_user, active_target, nonreciprocal):
        repository.add(memory)
    generator = _RecordingCandidateGenerator([])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, source)

    stored = repository.get(source.memory_id)
    assert result.decision.action is ConsolidationAction.ADD
    assert stored is not None
    assert stored.supersedes == [valid_history.memory_id]


def test_supersede_sanitizes_caller_links_but_keeps_etec_generated_target() -> None:
    repository = InMemoryMemoryRepository()
    source_id = UUID("74000000-0000-0000-0000-000000000003")
    other_source_id = UUID("74000000-0000-0000-0000-000000000099")
    older_target = _memory(
        "74000000-0000-0000-0000-000000000001",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-super:target",
        tenant_id="tenant-a",
    )
    unrelated_active = _memory(
        "74000000-0000-0000-0000-000000000002",
        "Caroline prefers detailed logs.",
        fact_slot="preference.logs",
        fact_value="detailed",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-super:active",
        tenant_id="tenant-a",
    )
    cross_tenant = _memory(
        "74000000-0000-0000-0000-000000000004",
        "Foreign historical memory.",
        fact_slot="history.foreign",
        fact_value="foreign",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-super:tenant",
        tenant_id="tenant-b",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    nonreciprocal = _memory(
        "74000000-0000-0000-0000-000000000005",
        "Superseded by another memory.",
        fact_slot="history.nonreciprocal",
        fact_value="nonreciprocal",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-super:nonreciprocal",
        tenant_id="tenant-a",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=other_source_id,
    )
    valid_history = _memory(
        "74000000-0000-0000-0000-000000000006",
        "Verified historical memory.",
        fact_slot="history.verified",
        fact_value="verified",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-super:valid",
        tenant_id="tenant-a",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    missing_id = UUID("74000000-0000-0000-0000-000000000009")
    source = _memory(
        str(source_id),
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="sanitize-super:source",
        tenant_id="tenant-a",
        supersedes=[
            older_target.memory_id,
            unrelated_active.memory_id,
            cross_tenant.memory_id,
            nonreciprocal.memory_id,
            missing_id,
            valid_history.memory_id,
        ],
    )
    for memory in (
        older_target,
        unrelated_active,
        cross_tenant,
        nonreciprocal,
        valid_history,
    ):
        repository.add(memory)
    generator = _RecordingCandidateGenerator([older_target])

    result = ETECConsolidator(
        DeterministicFakeEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    ).apply(repository, source)

    stored_source = repository.get(source.memory_id)
    stored_target = repository.get(older_target.memory_id)
    assert result.decision.action is ConsolidationAction.SUPERSEDE
    assert stored_source is not None and stored_target is not None
    assert stored_source.supersedes == [valid_history.memory_id, older_target.memory_id]
    assert stored_target.status is MemoryStatus.SUPERSEDED
    assert stored_target.superseded_by == source.memory_id


def test_merge_atomically_relinks_verified_source_histories_to_target() -> None:
    repository = InMemoryMemoryRepository()
    target_id = UUID("77000000-0000-0000-0000-000000000001")
    source_id = UUID("77000000-0000-0000-0000-000000000002")
    target_parent = UUID("77000000-0000-0000-0000-000000000090")
    source_parent = UUID("77000000-0000-0000-0000-000000000091")
    existing_history = _memory(
        "77000000-0000-0000-0000-000000000010",
        "Existing target history.",
        fact_slot="profile.city",
        fact_value="Paris",
        valid_from=datetime(2022, 1, 1, tzinfo=UTC),
        valid_to=datetime(2023, 1, 1, tzinfo=UTC),
        evidence_id="merge-relink:existing",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=target_id,
    )
    history_2023 = _memory(
        "77000000-0000-0000-0000-000000000011",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2023, 1, 1, tzinfo=UTC),
        valid_to=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="merge-relink:2023",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    history_2024 = _memory(
        "77000000-0000-0000-0000-000000000012",
        "Caroline lives in Austin.",
        fact_slot="profile.city",
        fact_value="Austin",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="merge-relink:2024",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    target = _memory(
        str(target_id),
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="merge-relink:target",
        supersedes=[existing_history.memory_id],
        derived_from=[target_parent],
    )
    source = _memory(
        str(source_id),
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 2, tzinfo=UTC),
        evidence_id="merge-relink:source",
        supersedes=[history_2023.memory_id, history_2024.memory_id],
        derived_from=[source_parent],
    )
    for memory in (existing_history, history_2023, history_2024, target):
        repository.add(memory)
    existing_snapshot = existing_history.model_copy(deep=True)
    generator = _RecordingCandidateGenerator([target])
    consolidator = ETECConsolidator(
        _MappedEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    )
    expected_2023 = consolidator.decide(target, [history_2023])
    expected_2024 = consolidator.decide(target, [history_2024])
    assert expected_2023.action is ConsolidationAction.SUPERSEDE
    assert expected_2024.action is ConsolidationAction.SUPERSEDE

    result = consolidator.apply(repository, source)

    merged = repository.get(target.memory_id)
    stored_2023 = repository.get(history_2023.memory_id)
    stored_2024 = repository.get(history_2024.memory_id)
    assert result.decision.action is ConsolidationAction.MERGE
    assert repository.get(source.memory_id) is None
    assert merged is not None and stored_2023 is not None and stored_2024 is not None
    assert merged.supersedes == [
        existing_history.memory_id,
        history_2023.memory_id,
        history_2024.memory_id,
    ]
    assert merged.derived_from == [target_parent, source_parent, source.memory_id]
    assert repository.get(existing_history.memory_id) == existing_snapshot
    assert stored_2023.superseded_by == target.memory_id
    assert stored_2024.superseded_by == target.memory_id
    for stored, expected in (
        (stored_2023, expected_2023),
        (stored_2024, expected_2024),
    ):
        decision = stored.metadata["etec"]["decision"]
        assert decision["action"] == ConsolidationAction.SUPERSEDE.value
        assert decision["source_memory_id"] == str(target.memory_id)
        assert decision["target_memory_id"] == str(stored.memory_id)
        assert decision["features"] == expected.features.model_dump(mode="json")
        assert decision["thresholds"] == expected.thresholds.model_dump(mode="json")
        assert "merged_source_history_relink" in decision["rule_hits"]
    assert {memory.memory_id for memory in result.updated_memories} == {
        history_2023.memory_id,
        history_2024.memory_id,
        target.memory_id,
    }


def test_merge_history_relink_failure_rolls_back_every_write() -> None:
    repository = _FailingOnSecondWriteRepository()
    target_id = UUID("78000000-0000-0000-0000-000000000001")
    source_id = UUID("78000000-0000-0000-0000-000000000002")
    history = _memory(
        "78000000-0000-0000-0000-000000000010",
        "Caroline lives in Seattle.",
        fact_slot="profile.city",
        fact_value="Seattle",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="merge-rollback:history",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=source_id,
    )
    target = _memory(
        str(target_id),
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        evidence_id="merge-rollback:target",
    )
    source = _memory(
        str(source_id),
        "Caroline lives in Boston.",
        fact_slot="profile.city",
        fact_value="Boston",
        valid_from=datetime(2025, 1, 2, tzinfo=UTC),
        evidence_id="merge-rollback:source",
        supersedes=[history.memory_id],
    )
    repository.add(history)
    repository.add(target)
    history_snapshot = history.model_copy(deep=True)
    target_snapshot = target.model_copy(deep=True)
    generator = _RecordingCandidateGenerator([target])
    consolidator = ETECConsolidator(
        _MappedEmbeddingModel(),
        candidate_generator=generator,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="injected repository failure"):
        consolidator.apply(repository, source)

    assert repository.get(history.memory_id) == history_snapshot
    assert repository.get(target.memory_id) == target_snapshot
    assert repository.get(source.memory_id) is None
