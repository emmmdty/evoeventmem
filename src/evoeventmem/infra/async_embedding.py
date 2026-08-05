from __future__ import annotations

import asyncio
import hashlib

from evoeventmem.core.ports import EmbeddingVector


class DeterministicAsyncEmbeddingModel:
    """Deterministic async embedding adapter for service/contract tests.

    This is a test/development adapter, not an automatic production fallback.
    It is never selected implicitly in the production path.
    """

    def __init__(self, *, model_id: str, dimension: int) -> None:
        if not model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._model_id = model_id
        self._dimension = dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    def dimension(self) -> int:
        return self._dimension

    async def embed_query(self, text: str) -> EmbeddingVector:
        return await self._embed(text, role="query")

    async def embed_document(self, text: str) -> EmbeddingVector:
        return await self._embed(text, role="document")

    async def _embed(self, text: str, *, role: str) -> EmbeddingVector:
        await asyncio.sleep(0)
        digest = hashlib.sha256(f"{role}:{text}".encode()).hexdigest()
        values = tuple(
            (int(digest[i : i + 2], 16) - 128) / 128.0
            for i in range(0, self._dimension * 2, 2)
        )
        return EmbeddingVector(values=values, model_id=self._model_id, dimension=self._dimension)