from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest

from evoeventmem.core.ports import EmbeddingVector
from evoeventmem.infra.async_embedding import (
    AsyncOpenAICompatibleEmbeddingModel,
    DeterministicAsyncEmbeddingModel,
    EmbeddingModelError,
    build_embedding_model,
)
from evoeventmem.infra.config import Settings


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


def _fake_post(response: dict[str, Any], error: Exception | None = None) -> Any:
    def post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert base_url == "https://embed.example"
        assert api_key == "secret-key"
        assert payload["model"] == "embed-model"
        if error is not None:
            raise error
        return response

    return post


def _embed_model(post: Any) -> AsyncOpenAICompatibleEmbeddingModel:
    return AsyncOpenAICompatibleEmbeddingModel(
        base_url="https://embed.example",
        api_key="secret-key",
        model_id="embed-model",
        dimension=4,
        timeout_s=5.0,
        post=post,
    )


def test_openai_compatible_exposes_configuration() -> None:
    model = _embed_model(_fake_post({}))
    assert model.model_id == "embed-model"
    assert model.dimension() == 4


def test_openai_compatible_embed_document_sends_model_and_input() -> None:
    captured: list[dict[str, Any]] = []

    def post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append(payload)
        return {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]}]}

    async def scenario() -> None:
        model = _embed_model(post)
        vector = await model.embed_document("npmmirror registry switch")
        assert isinstance(vector, EmbeddingVector)
        assert vector.model_id == "embed-model"
        assert vector.dimension == 4
        assert vector.values == (1.0, 0.0, 0.0, 0.0)

    _run(scenario())
    assert captured[0]["model"] == "embed-model"
    assert captured[0]["input"] == "npmmirror registry switch"


def test_openai_compatible_distinguishes_query_and_document() -> None:
    captured: list[str] = []

    def post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append(str(payload["input"]))
        return {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]}]}

    async def scenario() -> None:
        model = _embed_model(post)
        await model.embed_query("npmmirror")
        await model.embed_document("npmmirror")

    _run(scenario())
    assert captured == ["npmmirror", "npmmirror"]


def test_openai_compatible_rejects_dimension_mismatch() -> None:
    def post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]}

    async def scenario() -> None:
        model = _embed_model(post)
        with pytest.raises(EmbeddingModelError, match="dimension_mismatch"):
            await model.embed_document("npmmirror")

    _run(scenario())


def test_openai_compatible_handles_timeout() -> None:
    def post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("slow provider")

    async def scenario() -> None:
        model = _embed_model(post)
        with pytest.raises(EmbeddingModelError, match="timeout"):
            await model.embed_document("npmmirror")

    _run(scenario())


def test_openai_compatible_redacts_failure() -> None:
    def post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("provider rejected secret-key")

    async def scenario() -> None:
        model = _embed_model(post)
        with pytest.raises(EmbeddingModelError) as excinfo:
            await model.embed_document("npmmirror")
        assert "secret-key" not in str(excinfo.value)

    _run(scenario())


def test_openai_compatible_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        AsyncOpenAICompatibleEmbeddingModel(
            base_url="", api_key="k", model_id="m", dimension=4
        )
    with pytest.raises(ValueError):
        AsyncOpenAICompatibleEmbeddingModel(
            base_url="https://x", api_key="", model_id="m", dimension=4
        )
    with pytest.raises(ValueError):
        AsyncOpenAICompatibleEmbeddingModel(
            base_url="https://x", api_key="k", model_id="", dimension=4
        )
    with pytest.raises(ValueError):
        AsyncOpenAICompatibleEmbeddingModel(
            base_url="https://x", api_key="k", model_id="m", dimension=0
        )


def test_build_embedding_model_explicit_deterministic_adapter() -> None:
    settings = Settings(
        embedding_provider="deterministic",
        embedding_model_id="dev-embed",
        embedding_dimension=4,
    )
    model = build_embedding_model(settings=settings)
    assert isinstance(model, DeterministicAsyncEmbeddingModel)
    assert model.model_id == "dev-embed"
    assert model.dimension() == 4


def test_build_embedding_model_requires_key_for_openai_compatible() -> None:
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_model_id="embed-model",
        embedding_dimension=4,
        embedding_base_url="https://embed.example",
        embedding_api_key_env="EEM_TEST_EMBEDDING_KEY",
    )
    with pytest.raises(ValueError, match="EEM_TEST_EMBEDDING_KEY"):
        build_embedding_model(settings=settings)


def test_build_embedding_model_requires_base_url_for_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EEM_TEST_EMBEDDING_KEY", "secret-key")
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_model_id="embed-model",
        embedding_dimension=4,
        embedding_api_key_env="EEM_TEST_EMBEDDING_KEY",
    )
    with pytest.raises(ValueError, match="EEM_EMBEDDING_BASE_URL"):
        build_embedding_model(settings=settings)


def test_build_embedding_model_rejects_token_overlap_policy() -> None:
    settings = Settings(
        embedding_provider="deterministic",
        embedding_policy="token_overlap",
        embedding_model_id="dev-embed",
        embedding_dimension=4,
    )
    with pytest.raises(ValueError, match="token-overlap"):
        build_embedding_model(settings=settings)


def test_build_embedding_model_rejects_unknown_provider() -> None:
    settings = Settings(
        embedding_provider="bogus",
        embedding_model_id="dev-embed",
        embedding_dimension=4,
    )
    with pytest.raises(ValueError, match="unsupported embedding provider"):
        build_embedding_model(settings=settings)