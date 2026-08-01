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


def test_write_normalized_content_dedupe_is_tenant_scoped() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    first = service.write(
        MemoryRecord(
            tenant_id="tenant-1",
            user_id="shared-user",
            content="Registry changed to npmmirror",
            evidence=[EvidenceRef(source_type="turn", source_id="tenant-1:1")],
        )
    )
    second = service.write(
        MemoryRecord(
            tenant_id="tenant-2",
            user_id="shared-user",
            content="  registry CHANGED to npmmirror  ",
            evidence=[EvidenceRef(source_type="turn", source_id="tenant-2:1")],
        )
    )

    assert first.memory_id != second.memory_id
    assert repository.get(first.memory_id) == first
    assert repository.get(second.memory_id) == second


def test_write_does_not_trust_caller_normalized_content_for_dedupe() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    first = service.write(
        MemoryRecord(
            user_id="normalized-boundary-user",
            content="First distinct durable memory",
            normalized_content="forged-shared-value",
            evidence=[EvidenceRef(source_type="turn", source_id="first:1")],
        )
    )
    second = service.write(
        MemoryRecord(
            user_id="normalized-boundary-user",
            content="Second distinct durable memory",
            normalized_content="forged-shared-value",
            evidence=[EvidenceRef(source_type="turn", source_id="second:1")],
        )
    )
    duplicate = service.write(
        MemoryRecord(
            user_id="normalized-boundary-user",
            content="  FIRST distinct DURABLE memory  ",
            normalized_content="forged-different-value",
            evidence=[EvidenceRef(source_type="turn", source_id="duplicate:1")],
        )
    )

    assert first.memory_id != second.memory_id
    assert duplicate.memory_id == first.memory_id
    assert len(repository.list_for_user("normalized-boundary-user")) == 2


def test_write_rejects_cross_scope_memory_id_collision_without_overwrite() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    original = service.write(
        MemoryRecord(
            tenant_id="tenant-1",
            user_id="shared-user",
            content="Original durable memory",
            evidence=[EvidenceRef(source_type="turn", source_id="tenant-1:1")],
        )
    )
    colliding = MemoryRecord(
        memory_id=original.memory_id,
        tenant_id="tenant-2",
        user_id="shared-user",
        content="Replacement durable memory",
        evidence=[EvidenceRef(source_type="turn", source_id="tenant-2:1")],
    )

    with pytest.raises(ValueError, match="memory_id_collision"):
        service.write(colliding)

    assert repository.get(original.memory_id) == original


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


def test_search_is_strictly_scoped_to_requested_tenant() -> None:
    repository = InMemoryMemoryRepository()
    service = MemoryService(repository)
    memories = {
        tenant_id: service.write(
            MemoryRecord(
                tenant_id=tenant_id,
                user_id="shared-search-user",
                content=f"Shared search marker {label}",
                evidence=[EvidenceRef(source_type="turn", source_id=f"{label}:1")],
            )
        )
        for tenant_id, label in (
            ("tenant-a", "alpha"),
            ("tenant-b", "beta"),
            (None, "legacy"),
        )
    }

    tenant_a_hits = service.search(
        "shared-search-user",
        "shared search marker",
        tenant_id="tenant-a",
    )
    tenant_b_hits = service.search(
        "shared-search-user",
        "shared search marker",
        tenant_id="tenant-b",
    )
    unscoped_hits = service.search("shared-search-user", "shared search marker")

    assert [hit.memory.memory_id for hit in tenant_a_hits] == [
        memories["tenant-a"].memory_id
    ]
    assert [hit.memory.memory_id for hit in tenant_b_hits] == [
        memories["tenant-b"].memory_id
    ]
    assert [hit.memory.memory_id for hit in unscoped_hits] == [memories[None].memory_id]


def test_memory_record_rejects_naive_temporal_fields() -> None:
    with pytest.raises(ValueError, match="event_time must be timezone-aware"):
        MemoryRecord(
            user_id="u1",
            content="The user moved.",
            event_time=datetime(2024, 1, 1),
            synthetic=True,
        )
