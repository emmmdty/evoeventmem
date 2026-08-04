from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from evoeventmem.domain.models import MemoryRecord


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChatResponse:
    text: str
    model_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_key: str | None = None


@dataclass(frozen=True)
class EmbeddingResponse:
    vector: tuple[float, ...]
    model_id: str
    cache_key: str | None = None


class ChatModel(Protocol):
    model_id: str

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse: ...


class EmbeddingModel(Protocol):
    model_id: str

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]: ...


class Reranker(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class EntityLexicon(Protocol):
    """Known-entity vocabulary; names are compared case-insensitively."""

    def contains(self, name: str) -> bool: ...


class MemoryRepository(Protocol):
    def add(self, memory: MemoryRecord) -> MemoryRecord: ...

    def get(self, memory_id: UUID) -> MemoryRecord | None: ...

    def list_for_user(self, user_id: str) -> list[MemoryRecord]: ...

    def transaction(self) -> AbstractContextManager[MemoryRepository]: ...
