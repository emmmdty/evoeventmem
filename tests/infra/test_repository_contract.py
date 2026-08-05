from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from evoeventmem.core.ports import MemoryRepository
from evoeventmem.domain.models import (
    EvidenceRef,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    RelationRef,
)
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.infra.postgres_repository import PostgresMemoryRepository

_REPOSITORIES = ("in-memory", "postgres")


def _record(
    *,
    content: str = "registry switched to npmmirror",
    user_id: str = "contract-user",
    tenant_id: str = "tenant-1",
) -> MemoryRecord:
    event_time = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    return MemoryRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id="session-1",
        memory_kind=MemoryKind.EVENT,
        content=content,
        entities=[{"name": "registry"}, {"name": "npmmirror"}],
        roles={"subject": "project"},
        relations=[RelationRef(source="project", predicate="uses", target="registry")],
        evidence_refs=[
            EvidenceRef(
                source_type="turn",
                source_id="session-1:1",
                locator="chars=0:20",
                quote="we switched the registry",
                metadata={"speaker": "caroline", "tags": ["infra"]},
            )
        ],
        event_time=event_time,
        valid_from=event_time,
        valid_to=datetime(2024, 6, 1, tzinfo=UTC),
        confidence=0.9,
        utility=0.4,
        embedding_version="none",
        metadata={"source_dataset": "fixture", "nested": {"deep": [1, 2, 3]}},
    )


def _memory_with(memory: MemoryRecord, **updates: object) -> MemoryRecord:
    payload = {**memory.model_dump(mode="python"), **updates}
    return MemoryRecord.model_validate(payload)


@pytest.fixture(params=_REPOSITORIES)
def repository(request: pytest.FixtureRequest) -> Iterator[MemoryRepository]:
    if request.param == "in-memory":
        yield InMemoryMemoryRepository()
        return
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set; PostgreSQL contract tests are skipped")
    postgres = PostgresMemoryRepository(dsn, connect_timeout=5.0, operation_timeout=15.0)
    postgres.connect(apply_migrations=True)
    yield postgres
    postgres.close()


def _unique_user(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def test_contract_add_get_roundtrip_preserves_all_fields(repository: MemoryRepository) -> None:
    memory = _record(user_id=_unique_user("roundtrip"))

    stored = repository.add(memory)
    fetched = repository.get(memory.memory_id)

    assert fetched is not None
    assert fetched == stored
    assert fetched.memory_id == memory.memory_id
    assert fetched.tenant_id == "tenant-1"
    assert fetched.user_id == memory.user_id
    assert fetched.session_id == "session-1"
    assert fetched.memory_kind is MemoryKind.EVENT
    assert fetched.content == memory.content
    assert fetched.normalized_content == memory.normalized_content
    assert fetched.entities == memory.entities
    assert fetched.roles == memory.roles
    assert fetched.relations == memory.relations
    assert fetched.evidence_refs == memory.evidence_refs
    assert fetched.event_time == datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    assert fetched.valid_from == datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    assert fetched.valid_to == datetime(2024, 6, 1, tzinfo=UTC)
    assert fetched.status is MemoryStatus.ACTIVE
    assert fetched.confidence == 0.9
    assert fetched.utility == 0.4
    assert fetched.metadata == memory.metadata
    assert fetched.created_at == memory.created_at
    assert fetched.updated_at == memory.updated_at


def test_contract_add_overwrites_existing_memory_id(repository: MemoryRepository) -> None:
    original = _record(user_id=_unique_user("overwrite"))
    repository.add(original)
    replacement = _memory_with(
        original,
        content="a completely different replacement memory",
        status=MemoryStatus.SUPERSEDED,
        superseded_by=uuid4(),
    )

    stored = repository.add(replacement)

    assert repository.get(original.memory_id) == replacement
    assert stored == replacement
    assert repository.list_for_user(original.user_id) == [replacement]


def test_contract_update_changes_record_and_is_visible(repository: MemoryRepository) -> None:
    original = _record(user_id=_unique_user("update"))
    repository.add(original)
    updated = _memory_with(
        original,
        content="updated content after feedback",
        status=MemoryStatus.DELETED,
        metadata={**original.metadata, "forgotten_at": "2024-07-01T00:00:00+00:00"},
    )

    returned = repository.update(updated)

    assert returned == updated
    assert repository.get(original.memory_id) == updated
    assert repository.list_for_user(original.user_id) == [updated]


def test_contract_update_missing_memory_raises_key_error(repository: MemoryRepository) -> None:
    with pytest.raises(KeyError):
        repository.update(_record(user_id=_unique_user("missing-update")))


def test_contract_get_missing_returns_none(repository: MemoryRepository) -> None:
    assert repository.get(uuid4()) is None


def test_contract_list_for_user_filters_by_user(repository: MemoryRepository) -> None:
    user = _unique_user("list")
    other_user = _unique_user("other")
    expected = [
        _record(user_id=user, content="first memory for the user"),
        _record(user_id=user, content="second memory for the user"),
    ]
    repository.add(expected[0])
    repository.add(_record(user_id=other_user, content="memory for another user"))
    repository.add(expected[1])

    listed = repository.list_for_user(user)

    assert {item.memory_id for item in listed} == {item.memory_id for item in expected}


def test_contract_list_for_user_keeps_tenant_scoped_rows(repository: MemoryRepository) -> None:
    user = _unique_user("tenant")
    tenant_a = _record(tenant_id="tenant-a", user_id=user, content="tenant-a memory")
    tenant_b = _record(tenant_id="tenant-b", user_id=user, content="tenant-b memory")
    repository.add(tenant_a)
    repository.add(tenant_b)

    listed = repository.list_for_user(user)

    assert {item.tenant_id for item in listed} == {"tenant-a", "tenant-b"}


def test_contract_added_memory_is_detached_from_reader(repository: MemoryRepository) -> None:
    memory = _record(user_id=_unique_user("detach"))
    repository.add(memory)

    fetched = repository.get(memory.memory_id)
    assert fetched is not None
    fetched.metadata["mutated"] = True
    fetched.roles["subject"] = "mutated"

    again = repository.get(memory.memory_id)
    assert again is not None
    assert "mutated" not in again.metadata
    assert again.roles == memory.roles


def test_contract_transaction_rolls_back_all_writes_on_error(repository: MemoryRepository) -> None:
    first = _record(user_id=_unique_user("rollback"))
    second = _record(user_id=first.user_id, content="second rolled-back memory")

    with (
        pytest.raises(RuntimeError, match="boom"),
        repository.transaction() as transaction,
    ):
        transaction.add(first)
        transaction.add(second)
        raise RuntimeError("boom")

    assert repository.get(first.memory_id) is None
    assert repository.get(second.memory_id) is None
    assert repository.list_for_user(first.user_id) == []


def test_contract_transaction_publishes_all_writes_on_success(repository: MemoryRepository) -> None:
    first = _record(user_id=_unique_user("commit"))
    second = _record(user_id=first.user_id, content="second committed memory")

    with repository.transaction() as transaction:
        transaction.add(first)
        transaction.add(second)

    assert repository.get(first.memory_id) == first
    assert repository.get(second.memory_id) == second


def test_contract_transaction_rollback_keeps_existing_memory_unchanged(
    repository: MemoryRepository,
) -> None:
    original = _record(user_id=_unique_user("keep-existing"))
    repository.add(original)

    with (
        pytest.raises(RuntimeError, match="boom"),
        repository.transaction() as transaction,
    ):
        stored = transaction.get(original.memory_id)
        assert stored is not None
        transaction.update(_memory_with(stored, content="mutated inside transaction"))
        raise RuntimeError("boom")

    assert repository.get(original.memory_id) == original


def test_contract_transaction_view_isolates_uncommitted_writes(
    repository: MemoryRepository,
) -> None:
    user = _unique_user("isolate")
    base = _record(user_id=user)
    repository.add(base)

    with repository.transaction() as transaction:
        in_flight = _record(user_id=user, content="not yet visible outside transaction")
        transaction.add(in_flight)
        assert repository.get(in_flight.memory_id) is None

    assert repository.get(in_flight.memory_id) == in_flight
