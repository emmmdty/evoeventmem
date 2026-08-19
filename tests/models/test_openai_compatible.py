from __future__ import annotations

import http.server
import json
import threading

import pytest

from evoeventmem.core.ports import ChatMessage
from evoeventmem.infra.openai_compatible import (
    OpenAICompatibleChatClient,
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingClient,
)


@pytest.fixture(autouse=True)
def _zero_retry_backoff(monkeypatch):
    """S4b: zero out the OpenAI-compatible retry backoff so transient-5xx /
    progressive-shrink tests run in seconds rather than minutes."""
    monkeypatch.setenv("EEM_OPENAI_RETRY_BACKOFF", "0.0")
    yield


def test_openai_compatible_live_clients_require_explicit_config_and_credentials() -> None:
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleConfig(base_url="", api_key="sk-test", model="reader")

    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatibleConfig(base_url="http://localhost:8000/v1", api_key="", model="reader")

    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleConfig(base_url="http://localhost:8000/v1", api_key="sk-test", model="")


def test_openai_compatible_clients_do_not_have_default_model_names() -> None:
    config = OpenAICompatibleConfig(
        base_url="http://localhost:8000/v1",
        api_key="sk-test",
        model="configured-model",
    )

    assert OpenAICompatibleChatClient(config).model_id == "configured-model"
    assert OpenAICompatibleEmbeddingClient(config).model_id == "configured-model"


def test_openai_compatible_chat_retries_transient_errors_then_succeeds() -> None:
    attempts: list[int] = []

    class FlakyHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            attempts.append(1)
            if len(attempts) < 3:
                self.send_error(502, "Bad Gateway")
                return
            payload = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = OpenAICompatibleConfig(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="sk-test",
            model="flaky",
            timeout_s=10.0,
        )
        response = OpenAICompatibleChatClient(config).generate(
            [ChatMessage(role="user", content="hi")]
        )
    finally:
        server.shutdown()

    assert response.text == "ok"
    assert len(attempts) == 3


def test_openai_compatible_chat_retries_non_transient_errors_immediately() -> None:
    attempts: list[int] = []

    class BadRequestHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            attempts.append(1)
            self.send_error(400, "Bad Request")

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), BadRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = OpenAICompatibleConfig(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="sk-test",
            model="flaky",
            timeout_s=10.0,
        )
        client = OpenAICompatibleChatClient(config)
        with pytest.raises(RuntimeError, match="request failed"):
            client.generate([ChatMessage(role="user", content="hi")])
    finally:
        server.shutdown()

    assert len(attempts) == 1


# --- S4b: progressive batch shrinking for embedding calls ---
#
# ``OpenAICompatibleEmbeddingClient.embed_texts`` now starts with the full
# text list as one batch (fast path when the server is healthy) and only
# splits into smaller sub-batches on transient (5xx / timeout / connection)
# failures. This handles both server-side batch limits and transient
# instability (qwen3-embedding over an SSH tunnel exhibits both). 4xx
# errors are non-transient and never trigger a split.


def _start_embedding_server(handler_cls):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _embedding_config(port: int) -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="sk-test",
        model="embedder",
        timeout_s=10.0,
    )


def test_openai_compatible_embedding_single_call_for_healthy_server() -> None:
    """When the server accepts the full batch, the client makes exactly one
    HTTP call regardless of input size — no sub-batching overhead on the
    fast path."""
    request_count: list[int] = []

    class HealthyHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            request_count.append(1)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            input_texts = payload.get("input", [])
            data = [
                {"index": i, "embedding": [0.0]}
                for i, _ in enumerate(input_texts)
            ]
            response_body = json.dumps({"data": data}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server, thread = _start_embedding_server(HealthyHandler)
    try:
        client = OpenAICompatibleEmbeddingClient(_embedding_config(server.server_port))
        # 100 texts — much larger than any reasonable batch limit. A healthy
        # server handles them in one call.
        responses = client.embed_texts([f"chunk-{i}" for i in range(100)])
    finally:
        server.shutdown()

    assert request_count == [1]
    assert len(responses) == 100


def test_openai_compatible_embedding_splits_on_transient_failure() -> None:
    """When the server rejects a large batch with a 5xx, the client must
    split it into smaller sub-batches and retry each independently. This
    isolates which half failed and lets the healthy half succeed."""
    from evoeventmem.infra.openai_compatible import EMBEDDING_MAX_BATCH_SIZE

    request_sizes: list[int] = []

    class ThresholdHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            input_texts = payload.get("input", [])
            request_sizes.append(len(input_texts))
            if len(input_texts) > EMBEDDING_MAX_BATCH_SIZE:
                # Reject any batch above the threshold.
                self.send_error(500, "batch too large")
                return
            data = [
                {"index": i, "embedding": [float(i)]}
                for i, _ in enumerate(input_texts)
            ]
            response_body = json.dumps({"data": data}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server, thread = _start_embedding_server(ThresholdHandler)
    try:
        client = OpenAICompatibleEmbeddingClient(_embedding_config(server.server_port))
        # 100 texts. The first call (100 texts) fails. The client splits into
        # 50/50, each of those fails too. Splits again into 25/25/25/25.
        # Each 25-text batch is under the threshold (EMBEDDING_MAX_BATCH_SIZE=32)
        # and succeeds.
        responses = client.embed_texts([f"chunk-{i}" for i in range(100)])
    finally:
        server.shutdown()

    assert len(responses) == 100
    # No successful request exceeded the threshold.
    successful_sizes = [n for n in request_sizes if n <= EMBEDDING_MAX_BATCH_SIZE]
    assert successful_sizes  # at least one batch succeeded
    # The first request must have been the full 100-text batch (rejected).
    assert request_sizes[0] == 100
    # The client must have split at least once (otherwise everything would
    # have failed). The presence of any request < 100 proves the split
    # happened.
    assert any(n < 100 for n in request_sizes)


def test_openai_compatible_embedding_propagates_4xx_errors() -> None:
    """4xx (non-transient) errors must NOT trigger a split — they raise
    immediately so callers can distinguish real request bugs from server
    instability."""

    class BadRequestHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_error(400, "Bad Request")

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server, thread = _start_embedding_server(BadRequestHandler)
    try:
        client = OpenAICompatibleEmbeddingClient(_embedding_config(server.server_port))
        with pytest.raises(RuntimeError, match="request failed"):
            client.embed_texts(["a", "b", "c"])
    finally:
        server.shutdown()


def test_openai_compatible_embedding_propagates_persistent_5xx() -> None:
    """If the server returns 500 for every request (including single-text
    calls), progressive shrink bottoms out and the error propagates."""

    class AlwaysFailHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_error(500, "Always fail")

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server, thread = _start_embedding_server(AlwaysFailHandler)
    try:
        client = OpenAICompatibleEmbeddingClient(_embedding_config(server.server_port))
        with pytest.raises(RuntimeError, match="request failed"):
            client.embed_texts(["a", "b", "c"])
    finally:
        server.shutdown()


def test_openai_compatible_embedding_empty_input_returns_empty_list() -> None:
    """``embed_texts([])`` must not make any HTTP request."""

    class FailHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_error(500, "should not be called")

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server, thread = _start_embedding_server(FailHandler)
    try:
        client = OpenAICompatibleEmbeddingClient(_embedding_config(server.server_port))
        responses = client.embed_texts([])
    finally:
        server.shutdown()

    assert responses == []


