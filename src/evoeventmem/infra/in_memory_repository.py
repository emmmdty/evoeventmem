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

    def add(self, memory: MemoryRecord) -> MemoryRecord:
        with self._lock:
            self._items[memory.memory_id] = memory
        return memory

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        with self._lock:
            return self._items.get(memory_id)

    def list_for_user(self, user_id: str) -> list[MemoryRecord]:
        with self._lock:
            return [item for item in self._items.values() if item.user_id == user_id]

    @contextmanager
    def transaction(self) -> Iterator[MemoryRepository]:
        with self._lock:
            working_repository = InMemoryMemoryRepository()
            working_repository._items = self._items.copy()
            yield working_repository
            self._items = working_repository._items.copy()
