from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from evoeventmem.domain.models import (
    EntityRef,
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    RelationRef,
)
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.services.memory_service import (
    MemoryService,
    MemoryWriteCandidate,
    MemoryWriteDecisionStatus,
    MemoryWriteFailureCategory,
    MemoryWriteRequest,
    RawObservationLink,
)


def _event_memory(*, content: str = "Caroline joined a support group.") -> MemoryRecord:
    event_time = datetime(2023, 5, 7, tzinfo=UTC)
    return MemoryRecord(
        user_id="u1",
        session_id="D1",
        memory_kind=MemoryKind.EVENT,
        content=content,
        evidence_refs=[
            EvidenceRef(
                source_type="turn",
                source_id="D1:1",
                locator="chars=0:43",
                quote="I went to an LGBTQ support group yesterday.",
                metadata={"speaker": "Caroline"},
            )
        ],
        event_time=event_time,
        valid_from=event_time,
        metadata={
            "extractor_prompt_version": "rule.v1",
            "source_dataset": "locomo",
            "source_sample_id": "sample-1",
        },
    )


class _FailingSecondAddTransaction:
    def __init__(self, repository: InMemoryMemoryRepository) -> None:
        self._repository = repository
        self._add_count = 0

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        self._add_count += 1
        if self._add_count == 2:
            raise RuntimeError("injected storage failure")
        return self._repository.add(memory)

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        return self._repository.list_for_user(user_id)


class _FailingSecondAddRepository:
    def __init__(self) -> None:
        self._repository = InMemoryMemoryRepository()

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        return self._repository.list_for_user(user_id)

    @contextmanager
    def transaction(self) -> Iterator[_FailingSecondAddTransaction]:
        with self._repository.transaction() as transaction:
            assert isinstance(transaction, InMemoryMemoryRepository)
            yield _FailingSecondAddTransaction(transaction)


def test_write_pipeline_preserves_provenance_temporal_fields_and_decision_log() -> None:
    service = MemoryService(InMemoryMemoryRepository())
    observation = RawObservationLink(
        source_type="normalized_record",
        source_id="locomo:sample-1",
        locator="event_summaries[0]",
    )
    request = MemoryWriteRequest(
        request_id="req-1",
        raw_observations=[observation],
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory(),
                extractor_version="rule.v1",
            )
        ],
    )

    result = service.write_extracted_events(request)

    written = result.accepted_memories[0]
    evidence = written.evidence_refs[0]
    assert result.metrics.accepted == 1
    assert result.decisions[0].status is MemoryWriteDecisionStatus.ACCEPTED
    assert service.list_write_decisions("req-1") == result.decisions
    assert evidence.source_type == "turn"
    assert evidence.source_id == "D1:1"
    assert evidence.locator == "chars=0:43"
    assert evidence.quote == "I went to an LGBTQ support group yesterday."
    assert written.event_time == datetime(2023, 5, 7, tzinfo=UTC)
    assert written.valid_from == written.event_time
    assert written.metadata["extractor_prompt_version"] == "rule.v1"
    assert written.metadata["source_observations"] == [observation.model_dump(mode="json")]
    assert written.metadata["write_pipeline"]["idempotency_key"].startswith("memory-write.v1:")


def test_different_contents_from_same_evidence_are_both_accepted() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    request = MemoryWriteRequest(
        request_id="req-distinct-content",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory(content="Caroline joined a support group."),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory(content="Caroline invited a friend to the group."),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)

    assert len(repository.list_for_user("u1")) == 2
    assert result.metrics.accepted == 2
    assert result.metrics.duplicates == 0


def test_different_extractor_versions_remain_distinct() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    request = MemoryWriteRequest(
        request_id="req-distinct-extractor-version",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory(),
                extractor_version="Rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory(),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)

    assert len(repository.list_for_user("u1")) == 2
    assert result.metrics.accepted == 2
    assert result.metrics.duplicates == 0


@pytest.mark.parametrize(
    ("first_updates", "second_updates"),
    [
        (
            {"memory_kind": MemoryKind.EVENT},
            {"memory_kind": MemoryKind.FACT},
        ),
        (
            {"event_time": datetime(2023, 5, 7, tzinfo=UTC)},
            {"event_time": datetime(2023, 5, 8, tzinfo=UTC)},
        ),
        (
            {"valid_from": datetime(2023, 5, 7, tzinfo=UTC)},
            {"valid_from": datetime(2023, 5, 8, tzinfo=UTC)},
        ),
        (
            {"valid_to": datetime(2023, 5, 8, tzinfo=UTC)},
            {"valid_to": datetime(2023, 5, 9, tzinfo=UTC)},
        ),
        (
            {"entities": [EntityRef(name="Caroline", role="participant")]},
            {"entities": [EntityRef(name="Maya", role="participant")]},
        ),
        (
            {"roles": {"Caroline": "member"}},
            {"roles": {"Caroline": "facilitator"}},
        ),
        (
            {
                "relations": [
                    RelationRef(
                        source="Caroline",
                        predicate="joined",
                        target="support-group-a",
                    )
                ]
            },
            {
                "relations": [
                    RelationRef(
                        source="Caroline",
                        predicate="joined",
                        target="support-group-b",
                    )
                ]
            },
        ),
        (
            {
                "metadata": {
                    "fact_slot": "profile.city",
                    "fact_value": "Taipei",
                    "multi_valued": False,
                }
            },
            {
                "metadata": {
                    "fact_slot": "work.city",
                    "fact_value": "Taipei",
                    "multi_valued": False,
                }
            },
        ),
        (
            {
                "metadata": {
                    "fact_slot": "profile.city",
                    "fact_value": "Taipei",
                    "multi_valued": False,
                }
            },
            {
                "metadata": {
                    "fact_slot": "profile.city",
                    "fact_value": "Paris",
                    "multi_valued": False,
                }
            },
        ),
        (
            {
                "metadata": {
                    "fact_slot": "profile.phone",
                    "fact_value": "+886-555-0100",
                    "multi_valued": False,
                }
            },
            {
                "metadata": {
                    "fact_slot": "profile.phone",
                    "fact_value": "+886-555-0100",
                    "multi_valued": True,
                }
            },
        ),
    ],
)
def test_candidate_identity_distinguishes_memory_semantics(
    first_updates: dict[str, object],
    second_updates: dict[str, object],
) -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    request = MemoryWriteRequest(
        request_id="req-distinct-semantics",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory().model_copy(update=first_updates),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory().model_copy(update=second_updates),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)

    assert len(repository.list_for_user("u1")) == 2
    assert result.metrics.accepted == 2
    assert result.metrics.duplicates == 0


@pytest.mark.parametrize(
    ("first_scope", "second_scope"),
    [
        (
            {"tenant_id": "tenant-1", "user_id": "user-1"},
            {"tenant_id": "tenant-1", "user_id": "user-2"},
        ),
        (
            {"tenant_id": "tenant-1", "user_id": "user-1"},
            {"tenant_id": "tenant-2", "user_id": "user-1"},
        ),
    ],
    ids=["cross-user", "cross-tenant"],
)
def test_same_request_exact_candidates_in_different_scopes_are_both_stored(
    first_scope: dict[str, object],
    second_scope: dict[str, object],
) -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    request = MemoryWriteRequest(
        request_id="req-cross-scope",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory().model_copy(update=first_scope),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory().model_copy(update=second_scope),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)
    stored = [
        memory
        for user_id in {str(first_scope["user_id"]), str(second_scope["user_id"])}
        for memory in repository.list_for_user(user_id)
    ]

    assert len(stored) == 2
    assert {(memory.tenant_id, memory.user_id) for memory in stored} == {
        (first_scope["tenant_id"], first_scope["user_id"]),
        (second_scope["tenant_id"], second_scope["user_id"]),
    }
    assert result.metrics.accepted == 2
    assert result.metrics.duplicates == 0
    assert all(
        decision.status is MemoryWriteDecisionStatus.ACCEPTED
        for decision in result.decisions
    )


def test_retrying_same_evidence_and_extractor_version_creates_no_duplicate_memory() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    first = MemoryWriteRequest(
        request_id="req-1",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory(content="Caroline joined a support group."),
                extractor_version="rule.v1",
            )
        ],
    )
    retry = MemoryWriteRequest(
        request_id="req-2",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory(content="Caroline joined a support group."),
                extractor_version="rule.v1",
            )
        ],
    )

    first_result = service.write_extracted_events(first)
    retry_result = service.write_extracted_events(retry)

    assert len(repository.list_for_user("u1")) == 1
    assert retry_result.accepted_memories == []
    assert retry_result.metrics.duplicates == 1
    assert retry_result.decisions[0].status is MemoryWriteDecisionStatus.DUPLICATE
    assert retry_result.decisions[0].memory_id == first_result.accepted_memories[0].memory_id


def test_exact_retry_canonicalizes_entity_role_and_relation_order() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    first_memory = _event_memory().model_copy(
        update={
            "entities": [
                EntityRef(entity_id="group", name="Support Group", role="location"),
                EntityRef(entity_id="person", name="Caroline", role="participant"),
            ],
            "roles": {"person": "participant", "group": "location"},
            "relations": [
                RelationRef(source="person", predicate="joined", target="group"),
                RelationRef(source="group", predicate="includes", target="person"),
            ],
        }
    )
    retry_memory = _event_memory().model_copy(
        update={
            "entities": list(reversed(first_memory.entities)),
            "roles": {"group": "location", "person": "participant"},
            "relations": list(reversed(first_memory.relations)),
            "evidence_refs": [
                EvidenceRef(
                    source_type=" TURN ",
                    source_id=" d1:1 ",
                    locator=" CHARS=0:43 ",
                    quote="  i WENT to an lgbtq support group yesterday. ",
                    metadata={"speaker": " caroline "},
                )
            ],
        }
    )

    first_result = service.write_extracted_events(
        MemoryWriteRequest(
            request_id="req-canonical-first",
            candidates=[
                MemoryWriteCandidate(
                    candidate_id="cand-1",
                    memory=first_memory,
                    extractor_version="rule.v1",
                )
            ],
        )
    )
    retry_result = service.write_extracted_events(
        MemoryWriteRequest(
            request_id="req-canonical-retry",
            candidates=[
                MemoryWriteCandidate(
                    candidate_id="cand-2",
                    memory=retry_memory,
                    extractor_version="rule.v1",
                )
            ],
        )
    )

    assert len(repository.list_for_user("u1")) == 1
    assert retry_result.metrics.duplicates == 1
    assert retry_result.decisions[0].memory_id == first_result.accepted_memories[0].memory_id


def test_invalid_candidate_is_rejected_and_does_not_write_memory() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    invalid_memory = MemoryRecord.model_construct(
        user_id="u1",
        content="Unsupported durable memory without evidence.",
        evidence_refs=[],
    )
    request = MemoryWriteRequest.model_construct(
        request_id="req-invalid",
        candidates=[
            MemoryWriteCandidate.model_construct(
                candidate_id="cand-invalid",
                memory=invalid_memory,
                extractor_version="rule.v1",
                raw_observations=[],
                raw_output=None,
                metadata={},
            )
        ],
        raw_observations=[],
        metadata={},
    )

    result = service.write_extracted_events(request)

    assert repository.list_for_user("u1") == []
    assert result.accepted_memories == []
    assert result.metrics.rejected == 1
    assert result.metrics.failure_categories == {
        MemoryWriteFailureCategory.MISSING_EVIDENCE.value: 1
    }
    assert result.decisions[0].status is MemoryWriteDecisionStatus.REJECTED
    assert result.decisions[0].candidate_snapshot["candidate_id"] == "cand-invalid"
    assert service.list_write_decisions("req-invalid") == result.decisions


def test_request_with_invalid_candidate_does_not_partially_commit_valid_candidate() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    invalid_memory = MemoryRecord.model_construct(
        user_id="u1",
        content="Unsupported durable memory without evidence.",
        evidence_refs=[],
    )
    request = MemoryWriteRequest.model_construct(
        request_id="req-mixed",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-valid-1",
                memory=_event_memory(),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-valid-2",
                memory=_event_memory(),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate.model_construct(
                candidate_id="cand-invalid",
                memory=invalid_memory,
                extractor_version="rule.v1",
                raw_observations=[],
                raw_output=None,
                metadata={},
            ),
        ],
        raw_observations=[],
        metadata={},
    )

    result = service.write_extracted_events(request)

    assert repository.list_for_user("u1") == []
    assert result.accepted_memories == []
    assert result.metrics.accepted == 0
    assert result.metrics.duplicates == 0
    assert result.metrics.rejected == 3
    assert result.metrics.failure_categories == {
        MemoryWriteFailureCategory.MISSING_EVIDENCE.value: 1,
        MemoryWriteFailureCategory.REQUEST_VALIDATION_FAILED.value: 2,
    }
    assert all(
        decision.status is MemoryWriteDecisionStatus.REJECTED
        and decision.memory_id is None
        for decision in result.decisions
    )
    assert {decision.candidate_id for decision in result.decisions} == {
        "cand-valid-1",
        "cand-valid-2",
        "cand-invalid",
    }


def test_duplicate_candidates_in_one_request_are_duplicate_safe() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    request = MemoryWriteRequest(
        request_id="req-duplicates",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory(content="Caroline joined a support group."),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory(content="Caroline joined a support group."),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)

    assert len(repository.list_for_user("u1")) == 1
    assert result.metrics.accepted == 1
    assert result.metrics.duplicates == 1
    assert [decision.status for decision in result.decisions] == [
        MemoryWriteDecisionStatus.DUPLICATE,
        MemoryWriteDecisionStatus.ACCEPTED,
    ]


def test_storage_failure_rolls_back_and_rejects_all_writable_candidates() -> None:
    repository = _FailingSecondAddRepository()
    service = MemoryService(repository)
    request = MemoryWriteRequest(
        request_id="req-storage-failure",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-1",
                memory=_event_memory(content="Caroline joined a support group."),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-2",
                memory=_event_memory(content="Caroline invited a friend to the group."),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)

    assert repository.list_for_user("u1") == []
    assert result.accepted_memories == []
    assert result.metrics.accepted == 0
    assert result.metrics.duplicates == 0
    assert result.metrics.rejected == 2
    assert result.metrics.failure_categories == {
        MemoryWriteFailureCategory.STORAGE_FAILED.value: 2
    }
    assert all(
        decision.status is MemoryWriteDecisionStatus.REJECTED
        and decision.failure_category is MemoryWriteFailureCategory.STORAGE_FAILED
        and decision.memory_id is None
        for decision in result.decisions
    )


def test_storage_failure_rejects_persistent_and_batch_duplicates_after_rollback() -> None:
    repository = _FailingSecondAddRepository()
    service = MemoryService(repository)
    initial = service.write_extracted_events(
        MemoryWriteRequest(
            request_id="req-existing",
            candidates=[
                MemoryWriteCandidate(
                    candidate_id="cand-existing",
                    memory=_event_memory(),
                    extractor_version="rule.v1",
                )
            ],
        )
    )
    existing_id = initial.accepted_memories[0].memory_id
    request = MemoryWriteRequest(
        request_id="req-storage-failure-with-duplicates",
        candidates=[
            MemoryWriteCandidate(
                candidate_id="cand-persistent-duplicate",
                memory=_event_memory(),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-new-1",
                memory=_event_memory(content="Caroline invited a friend to the group."),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-batch-duplicate",
                memory=_event_memory(content="Caroline invited a friend to the group."),
                extractor_version="rule.v1",
            ),
            MemoryWriteCandidate(
                candidate_id="cand-new-2",
                memory=_event_memory(content="Caroline scheduled the next group meeting."),
                extractor_version="rule.v1",
            ),
        ],
    )

    result = service.write_extracted_events(request)

    stored = repository.list_for_user("u1")
    assert [memory.memory_id for memory in stored] == [existing_id]
    assert result.accepted_memories == []
    assert result.metrics.accepted == 0
    assert result.metrics.duplicates == 0
    assert result.metrics.rejected == 4
    assert result.metrics.failure_categories == {
        MemoryWriteFailureCategory.STORAGE_FAILED.value: 4
    }
    assert {decision.candidate_id for decision in result.decisions} == {
        "cand-persistent-duplicate",
        "cand-new-1",
        "cand-batch-duplicate",
        "cand-new-2",
    }
    assert all(
        decision.status is MemoryWriteDecisionStatus.REJECTED
        and decision.failure_category is MemoryWriteFailureCategory.STORAGE_FAILED
        and decision.memory_id is None
        for decision in result.decisions
    )
