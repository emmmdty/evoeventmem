from datetime import UTC, datetime

from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
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
                memory=_event_memory(content="Caroline attended a support group."),
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
                candidate_id="cand-valid",
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
    assert result.metrics.rejected == 2
    assert result.metrics.failure_categories == {
        MemoryWriteFailureCategory.MISSING_EVIDENCE.value: 1,
        MemoryWriteFailureCategory.REQUEST_VALIDATION_FAILED.value: 1,
    }
    assert {decision.candidate_id for decision in result.decisions} == {
        "cand-valid",
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
                memory=_event_memory(content="Caroline joined a support group again."),
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
