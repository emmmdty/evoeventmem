from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from evoeventmem.core.ports import (
    ChatMessage,
    ChatModel,
    ChatResponse,
    EmbeddingModel,
    EmbeddingResponse,
)


class FileModelCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def key_for(self, namespace: str, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"sha256-{digest}"

    def get(self, namespace: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        path = self._path(namespace, self.key_for(namespace, payload))
        if not path.exists():
            return None
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError(f"cache entry is not a JSON object: {path}")
        return cast(dict[str, Any], decoded)

    def set(self, namespace: str, payload: dict[str, Any], value: dict[str, Any]) -> str:
        key = self.key_for(namespace, payload)
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            entry = {"input": payload, "output": value}
            path.write_text(json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return key

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"


class CachedEmbeddingModel:
    def __init__(self, wrapped: EmbeddingModel, cache: FileModelCache) -> None:
        self._wrapped = wrapped
        self._cache = cache
        self.model_id = wrapped.model_id

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        # S4b: batch all cache misses into a single ``wrapped.embed_texts``
        # call. The previous implementation iterated per-text and made one
        # HTTP request per text; for ``vector_rag`` that meant ~200 sequential
        # tunneled HTTP calls per query, producing the observed ~437s p50
        # search latency on the v1 test50-mimo run. Batched calls collapse
        # that to a single request per query for the unique uncached texts.
        #
        # Contract preserved:
        # - Per-text content-addressed cache file (``{"model_id", "text"}``),
        #   so cache entries remain reusable across queries regardless of
        #   which texts travel together in any one batch.
        # - One ``EmbeddingResponse`` per input position, in input order.
        # - ``cache_key`` on every returned response points at the per-text
        #   cache file, so downstream artifacts (retrieval records) can still
        #   cite a single cache entry per memory chunk.
        if not texts:
            return []

        results: list[EmbeddingResponse | None] = [None] * len(texts)
        unique_miss_texts: list[str] = []
        seen_miss: set[str] = set()

        for index, text in enumerate(texts):
            payload = {"model_id": self.model_id, "text": text}
            cached = self._cache.get("embeddings", payload)
            if cached is None:
                if text not in seen_miss:
                    seen_miss.add(text)
                    unique_miss_texts.append(text)
                continue
            output = cached.get("output", cached)
            results[index] = EmbeddingResponse(
                vector=tuple(float(value) for value in output["vector"]),
                model_id=str(output["model_id"]),
                cache_key=self._cache.key_for("embeddings", payload),
            )

        if unique_miss_texts:
            batched = self._wrapped.embed_texts(unique_miss_texts)
            if len(batched) != len(unique_miss_texts):
                raise ValueError(
                    f"batched embed returned {len(batched)} responses for "
                    f"{len(unique_miss_texts)} unique uncached texts; wrapped "
                    f"model {self._wrapped.model_id!r} violates the "
                    "EmbeddingModel port contract (input length must equal "
                    "output length)"
                )
            text_to_response: dict[str, EmbeddingResponse] = {}
            for text, response in zip(unique_miss_texts, batched, strict=True):
                payload = {"model_id": self.model_id, "text": text}
                key = self._cache.set(
                    "embeddings",
                    payload,
                    {"model_id": response.model_id, "vector": list(response.vector)},
                )
                text_to_response[text] = EmbeddingResponse(
                    vector=response.vector,
                    model_id=response.model_id,
                    cache_key=key,
                )
            for index, text in enumerate(texts):
                if results[index] is None:
                    results[index] = text_to_response[text]

        return [response for response in results if response is not None]


class CachedChatModel:
    def __init__(self, wrapped: ChatModel, cache: FileModelCache) -> None:
        self._wrapped = wrapped
        self._cache = cache
        self.model_id = wrapped.model_id

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload = {
            "model_id": self.model_id,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        cached = self._cache.get("chat", payload)
        if cached is None:
            response = self._wrapped.generate(messages)
            key = self._cache.set(
                "chat",
                payload,
                {
                    "model_id": response.model_id,
                    "text": response.text,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                },
            )
            return ChatResponse(
                text=response.text,
                model_id=response.model_id,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cache_key=key,
            )
        output = cached.get("output", cached)
        return ChatResponse(
            text=str(output["text"]),
            model_id=str(output["model_id"]),
            input_tokens=_optional_int(output.get("input_tokens")),
            output_tokens=_optional_int(output.get("output_tokens")),
            cache_key=self._cache.key_for("chat", payload),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        raise TypeError("token usage must be numeric")
    return int(value)
