from __future__ import annotations

from typing import Protocol
from uuid import UUID

from evoeventmem.domain.models import MemoryRecord


class MemoryRepository(Protocol):
    def add(self, memory: MemoryRecord) -> MemoryRecord: ...

    def get(self, memory_id: UUID) -> MemoryRecord | None: ...

    def list_for_user(self, user_id: str) -> list[MemoryRecord]: ...
