from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from uuid import UUID

from evoeventmem.core.ports import MemoryRepository
from evoeventmem.domain.models import MemoryRecord


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, MemoryRecord] = {}
        self._lock = RLock()
        self._transaction_active = False

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        with self._lock:
            stored_memory = memory.model_copy(deep=True)
            self._items[stored_memory.memory_id] = stored_memory
            return stored_memory.model_copy(deep=True)

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        with self._lock:
            memory = self._items.get(memory_id)
            return memory.model_copy(deep=True) if memory is not None else None

    def update(self, memory: MemoryRecord) -> MemoryRecord:
        with self._lock:
            existing = self._items.get(memory.memory_id)
            if existing is None:
                raise KeyError(f"no memory with id {memory.memory_id}")
            stored_memory = memory.model_copy(deep=True)
            self._items[stored_memory.memory_id] = stored_memory
            return stored_memory.model_copy(deep=True)

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in self._items.values()
                if item.user_id == user_id
            ]

    @contextmanager
    def transaction(self) -> Iterator[MemoryRepository]:
        with self._lock:
            if self._transaction_active:
                raise RuntimeError("nested transactions are not supported")
            self._transaction_active = True
            try:
                working_repository = InMemoryMemoryRepository()
                working_repository._items = {
                    memory_id: memory.model_copy(deep=True)
                    for memory_id, memory in self._items.items()
                }
                yield working_repository
                self._items = {
                    memory_id: memory.model_copy(deep=True)
                    for memory_id, memory in working_repository._items.items()
                }
            finally:
                self._transaction_active = False
