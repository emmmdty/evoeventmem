from __future__ import annotations

import asyncio
import math

import pytest

from evoeventmem.core.ports import EmbeddingVector
from evoeventmem.infra.async_embedding import DeterministicAsyncEmbeddingModel


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_async_embedding_exposes_declared_model_and_dimension() -> None:
    model = DeterministicAsyncEmbeddingModel(model_id="test-embed", dimension=4)
    assert model.model_id == "test-embed"
    assert model.dimension() == 4


def test_async_embedding_query_returns_typed_vector() -> None:
    async def scenario() -> None:
        model = DeterministicAsyncEmbeddingModel(model_id="test-embed", dimension=4)
        vector = await model.embed_query("npmmirror registry switch")
        assert isinstance(vector, EmbeddingVector)
        assert vector.model_id == "test-embed"
        assert vector.dimension == 4
        assert len(vector.values) == 4
        assert all(math.isfinite(value) for value in vector.values)

    _run(scenario())


def test_async_embedding_document_returns_typed_vector() -> None:
    async def scenario() -> None:
        model = DeterministicAsyncEmbeddingModel(model_id="test-embed", dimension=4)
        vector = await model.embed_document("we switched the registry to npmmirror")
        assert isinstance(vector, EmbeddingVector)
        assert vector.model_id == "test-embed"
        assert vector.dimension == 4

    _run(scenario())


def test_async_embedding_is_deterministic() -> None:
    async def scenario() -> None:
        model = DeterministicAsyncEmbeddingModel(model_id="test-embed", dimension=4)
        first = await model.embed_query("npmmirror")
        second = await model.embed_query("npmmirror")
        assert first == second

    _run(scenario())


def test_async_embedding_distinguishes_query_and_document() -> None:
    async def scenario() -> None:
        model = DeterministicAsyncEmbeddingModel(model_id="test-embed", dimension=4)
        query = await model.embed_query("npmmirror")
        document = await model.embed_document("npmmirror")
        assert query != document

    _run(scenario())


def test_async_embedding_rejects_nonpositive_dimension() -> None:
    with pytest.raises(ValueError):
        DeterministicAsyncEmbeddingModel(model_id="bad", dimension=0)
    with pytest.raises(ValueError):
        DeterministicAsyncEmbeddingModel(model_id="bad", dimension=-1)


def test_async_embedding_rejects_blank_model_id() -> None:
    with pytest.raises(ValueError):
        DeterministicAsyncEmbeddingModel(model_id="", dimension=4)