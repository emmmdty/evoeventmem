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
            path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return key

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"


class CachedEmbeddingModel:
    def __init__(self, wrapped: EmbeddingModel, cache: FileModelCache) -> None:
        self._wrapped = wrapped
        self._cache = cache
        self.model_id = wrapped.model_id

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        responses: list[EmbeddingResponse] = []
        for text in texts:
            payload = {"model_id": self.model_id, "text": text}
            cached = self._cache.get("embeddings", payload)
            if cached is None:
                response = self._wrapped.embed_texts([text])[0]
                key = self._cache.set(
                    "embeddings",
                    payload,
                    {"model_id": response.model_id, "vector": list(response.vector)},
                )
                responses.append(
                    EmbeddingResponse(
                        vector=response.vector,
                        model_id=response.model_id,
                        cache_key=key,
                    )
                )
            else:
                responses.append(
                    EmbeddingResponse(
                        vector=tuple(float(value) for value in cached["vector"]),
                        model_id=str(cached["model_id"]),
                        cache_key=self._cache.key_for("embeddings", payload),
                    )
                )
        return responses


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
        return ChatResponse(
            text=str(cached["text"]),
            model_id=str(cached["model_id"]),
            input_tokens=_optional_int(cached.get("input_tokens")),
            output_tokens=_optional_int(cached.get("output_tokens")),
            cache_key=self._cache.key_for("chat", payload),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        raise TypeError("token usage must be numeric")
    return int(value)
