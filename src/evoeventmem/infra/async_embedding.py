from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

from evoeventmem.core.ports import EmbeddingVector


class EmbeddingModelError(RuntimeError):
    """Represents a stable, redacted embedding failure."""


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


class AsyncOpenAICompatibleEmbeddingModel:
    """Production async embedding adapter for an OpenAI-compatible provider.

    Uses an injectable ``post`` callable so transport behavior (endpoint, model,
    dimension mismatch, timeout) is testable without network access. The default
    ``post`` performs a real HTTP call; tests inject a fake.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        dimension: int,
        timeout_s: float = 30.0,
        post: Callable[..., Any] | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required for live OpenAI-compatible providers")
        if not api_key.strip():
            raise ValueError("api_key is required for live OpenAI-compatible providers")
        if not model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_id = model_id
        self._dimension = dimension
        self._timeout_s = timeout_s
        self._post = post if post is not None else _http_post

    @property
    def model_id(self) -> str:
        return self._model_id

    def dimension(self) -> int:
        return self._dimension

    async def embed_query(self, text: str) -> EmbeddingVector:
        return await self._embed(text, kind="text-query")

    async def embed_document(self, text: str) -> EmbeddingVector:
        return await self._embed(text, kind="text-document")

    async def _embed(self, text: str, *, kind: str) -> EmbeddingVector:
        payload = {
            "model": self._model_id,
            "input": text,
            "encoding_format": "float",
        }
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._post, self._base_url, self._api_key, payload),
                timeout=self._timeout_s,
            )
        except TimeoutError as exc:
            raise _embedding_failure("timeout") from exc
        except RuntimeError as exc:
            raise _embedding_failure("provider_request_failed") from exc
        except OSError as exc:
            raise _embedding_failure("transport_error") from exc

        data = response.get("data")
        if not isinstance(data, list) or not data:
            raise _embedding_failure("malformed_response")
        item = data[0]
        raw = item.get("embedding")
        if not raw:
            raise _embedding_failure("missing_embedding")
        values = tuple(float(value) for value in raw)
        if len(values) != self._dimension:
            raise _embedding_failure(
                f"dimension_mismatch: configured {self._dimension}, got {len(values)}"
            )
        return EmbeddingVector(
            values=values, model_id=self._model_id, dimension=self._dimension
        )


def _embedding_failure(reason: str) -> EmbeddingModelError:
    return EmbeddingModelError(reason)


def _http_post(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    import urllib.request

    url = f"{base_url}/embeddings"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        decoded: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return decoded


def build_embedding_model(*, settings: Any) -> object:
    """Resolve the configured production/development embedding adapter.

    ``deterministic`` returns the explicit development adapter (only used for
    Compose smoke or explicit configuration, never selected implicitly).
    ``openai_compatible`` returns the production HTTP adapter.
    """
    from evoeventmem.infra.config import Settings

    if not isinstance(settings, Settings):
        settings = Settings.from_env()
    if settings.embedding_policy == "token_overlap":
        raise ValueError(
            "token-overlap embedding policy is a development baseline and is not "
            "selectable as a production embedding adapter"
        )
    if settings.embedding_provider == "deterministic":
        return DeterministicAsyncEmbeddingModel(
            model_id=settings.embedding_model_id,
            dimension=settings.embedding_dimension,
        )
    if settings.embedding_provider == "openai_compatible":
        api_key = settings.embedding_api_key
        if not api_key:
            raise ValueError(
                f"embedding provider 'openai_compatible' requires "
                f"{settings.embedding_api_key_env} to be set"
            )
        if not settings.embedding_base_url:
            raise ValueError(
                "embedding provider 'openai_compatible' requires EEM_EMBEDDING_BASE_URL"
            )
        return AsyncOpenAICompatibleEmbeddingModel(
            base_url=settings.embedding_base_url,
            api_key=api_key,
            model_id=settings.embedding_model_id,
            dimension=settings.embedding_dimension,
            timeout_s=settings.embedding_timeout_s,
        )
    raise ValueError(f"unsupported embedding provider: {settings.embedding_provider!r}")