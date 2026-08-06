from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from evoeventmem.core.ports import ChatMessage, ChatResponse, EmbeddingResponse

MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 30.0
    thinking: str | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required for live OpenAI-compatible providers")
        if not self.api_key.strip():
            raise ValueError("api_key is required for live OpenAI-compatible providers")
        if not self.model.strip():
            raise ValueError("model is required for live OpenAI-compatible providers")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.thinking not in (None, "enabled", "disabled"):
            raise ValueError("thinking must be 'enabled', 'disabled', or None")


class OpenAICompatibleChatClient:
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self._config = config
        self.model_id = config.model

    def generate(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        if self._config.max_tokens is not None:
            payload["max_tokens"] = self._config.max_tokens
        if self._config.thinking is not None:
            payload["thinking"] = {"type": self._config.thinking}
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
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
                decoded = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"OpenAI-compatible provider request failed: {exc}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
    else:
        raise RuntimeError(
            f"OpenAI-compatible provider request failed after {MAX_RETRY_ATTEMPTS} "
            f"attempts: {last_error}"
        ) from last_error
    if not isinstance(decoded, dict):
        raise RuntimeError("OpenAI-compatible provider returned a non-object JSON response")
    return cast(dict[str, Any], decoded)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        raise TypeError("token usage must be numeric")
    return int(value)
