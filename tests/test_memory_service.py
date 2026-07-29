from datetime import datetime

import pytest

from evoeventmem.domain.models import EvidenceRef, MemoryRecord
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.services.memory_service import MemoryService


def test_write_is_idempotent_for_exact_normalized_content() -> None:
    service = MemoryService(InMemoryMemoryRepository())
    first = service.write(
        MemoryRecord(
            user_id="u1",
            content="Registry changed to npmmirror",
            evidence=[EvidenceRef(source_type="turn", source_id="1")],
        )
    )
    second = service.write(
        MemoryRecord(
            user_id="u1",
            content="  registry CHANGED to npmmirror  ",
            evidence=[EvidenceRef(source_type="turn", source_id="2")],
        )
    )
    assert first.memory_id == second.memory_id


def test_search_is_user_scoped_and_ranked() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    service.write(
        MemoryRecord(user_id="u1", content="pnpm uses npmmirror registry", synthetic=True)
    )
    service.write(MemoryRecord(user_id="u1", content="the project uses PostgreSQL", synthetic=True))
    service.write(MemoryRecord(user_id="u2", content="pnpm uses another registry", synthetic=True))

    hits = service.search("u1", "pnpm registry")

    assert len(hits) == 1
    assert hits[0].memory.user_id == "u1"
    assert "npmmirror" in hits[0].memory.content


def test_memory_record_rejects_naive_temporal_fields() -> None:
    with pytest.raises(ValueError, match="event_time must be timezone-aware"):
        MemoryRecord(
            user_id="u1",
            content="The user moved.",
            event_time=datetime(2024, 1, 1),
            synthetic=True,
        )
