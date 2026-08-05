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


