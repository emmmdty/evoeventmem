from __future__ import annotations

import pytest

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
