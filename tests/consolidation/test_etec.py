from datetime import UTC, datetime
from uuid import UUID

from evoeventmem.consolidation import ConsolidationAction, ETECConsolidator
from evoeventmem.domain.models import EntityRef, EvidenceRef, MemoryRecord, MemoryStatus
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel


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
    multi_valued: bool = False,
    synthetic: bool = False,
) -> MemoryRecord:
    evidence_refs = [] if synthetic else [_evidence(evidence_id)]
    return MemoryRecord(
        memory_id=UUID(memory_id),
        user_id="u1",
        content=content,
        entities=[EntityRef(name="Caroline", role="subject")],
        roles={"Caroline": "subject"},
        evidence_refs=evidence_refs,
        valid_from=valid_from,
        metadata={
            "fact_slot": fact_slot,
            "fact_value": fact_value,
            "multi_valued": multi_valued,
        },
        synthetic=synthetic,
    )


def _consolidator() -> ETECConsolidator:
    return ETECConsolidator(embedding_model=DeterministicFakeEmbeddingModel())


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
