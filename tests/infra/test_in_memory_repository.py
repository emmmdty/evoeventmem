from __future__ import annotations

from collections.abc import Callable
from queue import Queue
from threading import Event, Thread
from uuid import UUID

import pytest

from evoeventmem.domain.models import MemoryRecord
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository


def _memory(content: str) -> MemoryRecord:
    return MemoryRecord(user_id="u1", content=content, synthetic=True)


def test_transaction_rolls_back_all_writes_on_error() -> None:
    repository = InMemoryMemoryRepository()
    first = _memory("first")
    second = _memory("second")

    with (
        pytest.raises(RuntimeError, match="fail"),
        repository.transaction() as transaction,
    ):
        transaction.add(first)
        transaction.add(second)
        raise RuntimeError("fail")

    assert repository.list_for_user("u1") == []


def test_transaction_publishes_all_writes_on_success() -> None:
    repository = InMemoryMemoryRepository()
    first = _memory("first")
    second = _memory("second")

    with repository.transaction() as transaction:
        transaction.add(first)
        transaction.add(second)

    assert {item.memory_id for item in repository.list_for_user("u1")} == {
        first.memory_id,
        second.memory_id,
    }


def test_concurrent_transactions_observe_only_published_snapshots() -> None:
    repository = InMemoryMemoryRepository()
    assert callable(repository.transaction)

    first = _memory("first")
    second = _memory("second")
    first_write_done = Event()
    second_attempted = Event()
    second_entered = Event()
    release_first = Event()
    observed_memory_ids: list[set[UUID]] = []
    errors: Queue[BaseException] = Queue()

    def capture_errors(operation: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            try:
                operation()
            except BaseException as exc:
                errors.put(exc)

        return wrapped

    def run_first_transaction() -> None:
        with repository.transaction() as transaction:
            transaction.add(first)
            first_write_done.set()
            if not release_first.wait(timeout=2):
                raise TimeoutError("first transaction was not released")
            transaction.add(second)

    def run_second_transaction() -> None:
        second_attempted.set()
        with repository.transaction() as transaction:
            second_entered.set()
            observed_memory_ids.append(
                {
                    item.memory_id
                    for item in transaction.list_for_user(first.user_id)
                }
            )

    first_thread = Thread(target=capture_errors(run_first_transaction))
    second_thread = Thread(target=capture_errors(run_second_transaction))
    first_thread.start()
    try:
        assert first_write_done.wait(timeout=1)
        second_thread.start()
        assert second_attempted.wait(timeout=1)
        assert not second_entered.wait(timeout=0.1)
    finally:
        release_first.set()
        first_thread.join(timeout=2)
        if second_thread.ident is not None:
            second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors.empty()
    assert observed_memory_ids == [{first.memory_id, second.memory_id}]
