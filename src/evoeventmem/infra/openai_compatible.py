from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from evoeventmem.core.ports import ChatMessage, ChatResponse, EmbeddingResponse

MAX_RETRY_ATTEMPTS = 3
# S4b: backoff is overridable via env var so tests can run sub-batch retry
# scenarios in seconds rather than minutes. Read lazily inside ``_post_json``
# so test fixtures that monkeypatch the env var take effect per-call.
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
# S4b: qwen3-embedding (and similar small OpenAI-compatible embedding
# servers) intermittently reject large ``input`` arrays with HTTP 500 after
# the per-request work exceeds a server-side threshold. Used as the
# progressive-shrink split threshold in tests; the production embedder
# splits only on actual transient failures, not on a fixed size cap.
EMBEDDING_MAX_BATCH_SIZE = 32


def _retry_backoff_seconds() -> float:
    raw = os.environ.get("EEM_OPENAI_RETRY_BACKOFF")
    if raw is None:
        return DEFAULT_RETRY_BACKOFF_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_RETRY_BACKOFF_SECONDS


class _TransientEmbeddingError(RuntimeError):
    """Internal sentinel for transient (5xx / timeout / connection) failures.

    Distinguishes "split-and-retry" failures from non-transient 4xx errors
    inside ``OpenAICompatibleEmbeddingClient._embed_with_progressive_shrink``
    without changing the public surface of ``_post_json``.
    """


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
        text_list = list(texts)
        if not text_list:
            return []
        # S4b: progressive batch shrinking. Start with the full text list as
        # one batch (the fast path when the server is healthy — observed
        # ~70s for 550 texts). If the server rejects the batch with a
        # transient 5xx / timeout / connection error, split into two
        # sub-batches and retry each independently. Continue splitting until
        # each sub-batch is a single text (the most robust fallback). This
        # handles both server-side batch limits and transient instability
        # (qwen3-embedding over an SSH tunnel exhibits both). 4xx errors are
        # non-transient and never trigger a split — they raise immediately.
        return self._embed_with_progressive_shrink(text_list)

    def _embed_with_progressive_shrink(
        self, texts: list[str]
    ) -> list[EmbeddingResponse]:
        if len(texts) <= 1:
            # Single-text (or empty) base case: one HTTP call, surface any
            # failure to the caller. The retry logic in ``_post_json`` still
            # applies, so transient 5xx errors get 5 attempts with backoff
            # before this raises.
            return self._post_embedding_batch(texts)
        try:
            return self._post_embedding_batch(texts)
        except _TransientEmbeddingError:
            # Split and recurse. This isolates which half failed and lets
            # the healthy half succeed without re-paying its embedding cost.
            mid = len(texts) // 2
            left = self._embed_with_progressive_shrink(texts[:mid])
            right = self._embed_with_progressive_shrink(texts[mid:])
            return left + right

    def _post_embedding_batch(self, texts: list[str]) -> list[EmbeddingResponse]:
        payload = {"model": self._config.model, "input": texts}
        try:
            response = _post_json(self._config, "embeddings", payload)
        except RuntimeError as exc:
            # ``_post_json`` raises RuntimeError on retry exhaustion. Distinguish
            # transient (5xx/timeout/conn) from non-transient (4xx) by message
            # content: only the former triggers a split-and-retry.
            message = str(exc)
            if "HTTP Error 4" in message and "HTTP Error 429" not in message:
                raise
            raise _TransientEmbeddingError(message) from exc
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
            "User-Agent": "opencode/1.0",
        },
        method="POST",
    )
    last_error: Exception | None = None
    backoff = _retry_backoff_seconds()
    for attempt in range(MAX_RETRY_ATTEMPTS):
        if attempt:
            time.sleep(backoff * (2 ** (attempt - 1)))
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
                decoded = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"OpenAI-compatible provider request failed: {exc}") from exc
            last_error = exc
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
        ) as exc:
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
