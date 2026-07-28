from __future__ import annotations

import re

from evoeventmem.core.ports import MemoryRepository
from evoeventmem.domain.models import MemoryRecord, MemorySearchHit

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


class MemoryService:
    """Minimal vertical slice; later tasks replace the retrieval and consolidation logic."""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    def write(self, memory: MemoryRecord) -> MemoryRecord:
        normalized = " ".join(memory.content.split()).casefold()
        for existing in self._repository.list_for_user(memory.user_id):
            if " ".join(existing.content.split()).casefold() == normalized:
                return existing
        return self._repository.add(memory)

    def search(self, user_id: str, query: str, limit: int = 5) -> list[MemorySearchHit]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query_tokens = _tokens(query)
        hits: list[MemorySearchHit] = []
        for memory in self._repository.list_for_user(user_id):
            memory_tokens = _tokens(memory.content + " " + " ".join(memory.entities))
            union = query_tokens | memory_tokens
            score = len(query_tokens & memory_tokens) / len(union) if union else 0.0
            if score > 0:
                hits.append(
                    MemorySearchHit(
                        memory=memory,
                        score=score,
                        reason="starter token-overlap baseline",
                    )
                )
        return sorted(hits, key=lambda hit: (-hit.score, str(hit.memory.memory_id)))[:limit]
