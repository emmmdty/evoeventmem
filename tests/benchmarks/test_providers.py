from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.common.providers import (
    ModelBundle,
    ProviderConfig,
    ProviderKind,
    build_model_bundle,
    cache_for_run,
    resolve_provider_config,
)


def _fake_config() -> ProviderConfig:
    return ProviderConfig(
        provider="deterministic_fake",
        reader={
            "role": "reader",
            "kind": "deterministic_fake",
            "provider": "deterministic_fake",
            "model_id": "reader-fake",
        },
        extractor={
            "role": "extractor",
            "kind": "deterministic_fake",
            "provider": "deterministic_fake",
            "model_id": "extractor-fake",
        },
        embedding={
            "role": "embedding",
            "kind": "deterministic_fake",
            "provider": "deterministic_fake",
            "model_id": "embedding-fake",
        },
    )


def test_resolved_bundle_has_distinct_role_identities() -> None:
    config = _fake_config()

    assert config.reader.model_id == "reader-fake"
    assert config.extractor.model_id == "extractor-fake"
    assert config.embedding.model_id == "embedding-fake"
    assert config.reader.kind is ProviderKind.DETERMINISTIC_FAKE
    assert config.extractor.kind is ProviderKind.DETERMINISTIC_FAKE
    assert config.embedding.kind is ProviderKind.DETERMINISTIC_FAKE


def test_bundle_construction_makes_no_network_calls(tmp_path: Path) -> None:
    bundle = build_model_bundle(_fake_config(), cache_for_run(tmp_path / "run"))

    assert isinstance(bundle, ModelBundle)
    assert bundle.reader.model_id == "reader-fake"
    assert bundle.extractor.model_id == "extractor-fake"
    assert bundle.embedding.model_id == "embedding-fake"
    assert bundle.resolved.reader.kind is ProviderKind.DETERMINISTIC_FAKE


def test_embedding_never_falls_back_to_reader_model() -> None:
    payload = {
        "provider": "openai_compatible",
        "reader": {"model_id": "reader-model", "base_url": "https://x", "api_key_env": "K1"},
        "extractor": {"model_id": "extractor-model", "base_url": "https://x", "api_key_env": "K1"},
    }

    with pytest.raises(ValueError, match="embedding"):
        resolve_provider_config(payload)


def test_embedding_role_requires_explicit_live_fields() -> None:
    payload = {
        "provider": "openai_compatible",
        "reader": {"model_id": "reader-model", "base_url": "https://x", "api_key_env": "K1"},
        "extractor": {"model_id": "extractor-model", "base_url": "https://x", "api_key_env": "K1"},
        "embedding": {"model_id": "embed-model"},
    }

    with pytest.raises(ValueError, match="base_url|api_key_env"):
        resolve_provider_config(payload)


def test_reader_and_embedding_must_be_distinguishable() -> None:
    payload = {
        "provider": "openai_compatible",
        "reader": {"model_id": "same", "base_url": "https://x", "api_key_env": "K1"},
        "extractor": {"model_id": "extractor-model", "base_url": "https://x", "api_key_env": "K1"},
        "embedding": {"model_id": "embed-model", "base_url": "https://x", "api_key_env": "K1"},
    }
    resolved = resolve_provider_config(payload)

    assert resolved.reader.model_id == "same"
    assert resolved.embedding.model_id == "embed-model"
    assert resolved.reader.model_id != resolved.embedding.model_id


def test_secrets_are_never_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "provider": "openai_compatible",
        "reader": {
            "model_id": "reader-model",
            "base_url": "https://x",
            "api_key_env": "TEST_READER_KEY",
        },
        "extractor": {
            "model_id": "extractor-model",
            "base_url": "https://x",
            "api_key_env": "TEST_EXTRACTOR_KEY",
        },
        "embedding": {
            "model_id": "embed-model",
            "base_url": "https://y",
            "api_key_env": "TEST_EMBED_KEY",
        },
    }
    monkeypatch.setenv("TEST_READER_KEY", "super-secret-reader")
    monkeypatch.setenv("TEST_EXTRACTOR_KEY", "super-secret-extractor")
    monkeypatch.setenv("TEST_EMBED_KEY", "super-secret-embed")
    resolved = resolve_provider_config(payload)

    serialized = json.dumps(resolved.redacted())
    assert "super-secret-reader" not in serialized
    assert "super-secret-extractor" not in serialized
    assert "super-secret-embed" not in serialized

    assert resolved.reader.redacted()["api_key_set"] is True
    assert resolved.embedding.redacted()["api_key_set"] is True


def test_resolve_provider_config_requires_explicit_error_sections() -> None:
    with pytest.raises(ValueError, match="reader"):
        resolve_provider_config({"provider": "deterministic_fake"})



def _live_config_payload() -> dict:
    reader = {
        "model_id": "toml-model",
        "base_url": "https://toml.example.com",
        "api_key_env": "K",
    }
    extractor = {
        "model_id": "toml-model",
        "base_url": "https://toml.example.com",
        "api_key_env": "K",
    }
    embedding = {
        "model_id": "emb",
        "base_url": "https://emb.example.com",
        "api_key_env": "EK",
    }
    return {
        "provider": "openai_compatible",
        "reader": reader,
        "extractor": extractor,
        "embedding": embedding,
    }

def test_env_overrides_reader_and_extractor_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EEM_LLM_BASE_URL", "https://env-llm.example.com/v1")
    monkeypatch.setenv("EEM_LLM_MODEL", "env-model")
    config = resolve_provider_config(
        _live_config_payload()
    )
    assert config.reader.base_url == "https://env-llm.example.com/v1"
    assert config.reader.model_id == "env-model"
    assert config.extractor.base_url == "https://env-llm.example.com/v1"
    assert config.embedding.base_url == "https://emb.example.com"


def test_env_role_specific_overrides_general(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EEM_LLM_BASE_URL", "https://general.example.com")
    monkeypatch.setenv("EEM_READER_BASE_URL", "https://reader.example.com")
    config = resolve_provider_config(
        _live_config_payload()
    )
    assert config.reader.base_url == "https://reader.example.com"
    assert config.extractor.base_url == "https://general.example.com"


def test_env_overrides_api_key_env_and_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EEM_LLM_API_KEY_ENV", "MY_KEY")
    monkeypatch.setenv("EEM_LLM_THINKING", "disabled")
    monkeypatch.setenv("EEM_LLM_MAX_TOKENS", "4096")
    config = resolve_provider_config(
        _live_config_payload()
    )
    assert config.reader.api_key_env == "MY_KEY"
    assert config.reader.thinking == "disabled"
    assert config.reader.max_tokens == 4096


def test_env_overrides_embedding_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EEM_EMBEDDING_BASE_URL", "http://127.0.0.1:9999/v1")
    config = resolve_provider_config(
        _live_config_payload()
    )
    assert config.embedding.base_url == "http://127.0.0.1:9999/v1"
