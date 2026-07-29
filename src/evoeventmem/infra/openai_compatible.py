from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from evoeventmem.core.ports import ChatMessage, ChatResponse, EmbeddingResponse


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required for live OpenAI-compatible providers")
        if not self.api_key.strip():
            raise ValueError("api_key is required for live OpenAI-compatible providers")
        if not self.model.strip():
            raise ValueError("model is required for live OpenAI-compatible providers")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")


class OpenAICompatibleChatClient:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self._config = config
        self.model_id = config.model

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        response = _post_json(self._config, "chat/completions", payload)
        choice = response["choices"][0]
        text = str(choice["message"]["content"])
        usage = response.get("usage", {})
        return ChatResponse(
            text=text,
            model_id=self.model_id,
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
        )


class OpenAICompatibleEmbeddingClient:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self._config = config
        self.model_id = config.model

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        payload = {"model": self._config.model, "input": list(texts)}
        response = _post_json(self._config, "embeddings", payload)
        data = sorted(response["data"], key=lambda item: int(item["index"]))
        return [
            EmbeddingResponse(
                vector=tuple(float(value) for value in item["embedding"]),
                model_id=self.model_id,
            )
            for item in data
        ]


def _post_json(
    config: OpenAICompatibleConfig,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{config.base_url.rstrip('/')}/{path}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI-compatible provider request failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("OpenAI-compatible provider returned a non-object JSON response")
    return cast(dict[str, Any], decoded)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        raise TypeError("token usage must be numeric")
    return int(value)
