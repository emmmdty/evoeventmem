"""Centralized benchmark model construction.

Reader, extractor, and embedding are resolved as independent provider roles
with separate provider, model ID, base URL, API-key environment name, timeout,
and optional thinking mode. The embedding role never falls back to the chat
(reader) model. Secrets (API keys) are never reachable from the resolved
config's serialized form; live clients read keys from the environment lazily at
construction time.

Deterministic fake construction builds in-memory models only and makes zero
network calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evoeventmem.core.ports import ChatModel, EmbeddingModel
from evoeventmem.infra.openai_compatible import (
    OpenAICompatibleChatClient,
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingClient,
)
from evoeventmem.models.cache import CachedChatModel, CachedEmbeddingModel, FileModelCache
from evoeventmem.models.fakes import DeterministicFakeChatModel, DeterministicFakeEmbeddingModel

PROVIDER_SCHEMA_VERSION = 1


class ProviderKind(StrEnum):
    DETERMINISTIC_FAKE = "deterministic_fake"
    OPENAI_COMPATIBLE = "openai_compatible"


RoleName = Literal["reader", "extractor", "embedding"]


class ResolvedModelConfig(BaseModel):
    """One resolved model role. Never carries an API key value."""

    schema_version: int = PROVIDER_SCHEMA_VERSION
    role: RoleName
    kind: ProviderKind
    provider: str = Field(default="", min_length=0)
    model_id: str = Field(min_length=1)
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_s: float = Field(default=60.0, gt=0)
    thinking: Literal["enabled", "disabled", None] = None
    max_tokens: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_live_fields(self) -> ResolvedModelConfig:
        if not self.provider:
            self.provider = self.kind.value
        if self.kind is ProviderKind.OPENAI_COMPATIBLE:
            if not self.base_url:
                raise ValueError(f"{self.role} live provider requires base_url")
            if not self.api_key_env:
                raise ValueError(f"{self.role} live provider requires api_key_env")
        return self

    def redacted(self) -> dict[str, object]:
        """Serialized form with no secret material (no API key value)."""
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "kind": self.kind.value,
            "provider": self.provider,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "timeout_s": self.timeout_s,
            "thinking": self.thinking,
            "api_key_set": self.api_key_env is not None
            and bool(os.environ.get(self.api_key_env or "")),
        }


class ProviderConfig(BaseModel):
    """Top-level provider configuration with explicit independent roles."""

    schema_version: int = PROVIDER_SCHEMA_VERSION
    provider: ProviderKind
    reader: ResolvedModelConfig
    extractor: ResolvedModelConfig
    embedding: ResolvedModelConfig

    @model_validator(mode="after")
    def require_embedding_never_falls_back(self) -> ProviderConfig:
        embedding = self.embedding
        if embedding.kind is ProviderKind.OPENAI_COMPATIBLE and not embedding.model_id:
            raise ValueError("embedding model must be configured; it never falls back to chat")
        return self

    def redacted(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider.value,
            "reader": self.reader.redacted(),
            "extractor": self.extractor.redacted(),
            "embedding": self.embedding.redacted(),
        }


@dataclass(frozen=True)
class ModelBundle:
    """Resolved identity plus the concrete cached model clients."""

    resolved: ProviderConfig
    reader: ChatModel
    extractor: ChatModel
    embedding: EmbeddingModel


def resolve_provider_config(payload: dict[str, object]) -> ProviderConfig:
    """Build a ProviderConfig from a raw config dict (TOML-decoded)."""
    provider = payload.get("provider", "deterministic_fake")
    if provider not in ("deterministic_fake", "openai_compatible"):
        raise ValueError(f"unsupported provider: {provider}")

    def role(name: RoleName) -> dict[str, object]:
        section = payload.get(name)
        if not isinstance(section, dict):
            raise ValueError(f"missing explicit [{name}] provider section")
        return {"role": name, "kind": provider, **section}

    return ProviderConfig(
        provider=provider,
        reader=ResolvedModelConfig.model_validate(role("reader")),
        extractor=ResolvedModelConfig.model_validate(role("extractor")),
        embedding=ResolvedModelConfig.model_validate(role("embedding")),
    )


def build_model_bundle(
    config: ProviderConfig,
    cache: FileModelCache,
) -> ModelBundle:
    """Construct the concrete model clients for a resolved provider config.

    Deterministic fake construction creates in-memory models only and performs
    no network calls. Live OpenAI-compatible construction reads the API key from
    the environment but does not contact the endpoint until a request is made.
    """
    reader = _build_chat(config.reader, cache)
    extractor = _build_chat(config.extractor, cache)
    embedding = _build_embedding(config.embedding, cache)
    return ModelBundle(resolved=config, reader=reader, extractor=extractor, embedding=embedding)


def _build_chat(resolved: ResolvedModelConfig, cache: FileModelCache) -> ChatModel:
    if resolved.kind is ProviderKind.DETERMINISTIC_FAKE:
        return CachedChatModel(DeterministicFakeChatModel(resolved.model_id), cache)
    return CachedChatModel(
        OpenAICompatibleChatClient(_live_config(resolved)), cache
    )


def _build_embedding(resolved: ResolvedModelConfig, cache: FileModelCache) -> EmbeddingModel:
    if resolved.kind is ProviderKind.DETERMINISTIC_FAKE:
        return CachedEmbeddingModel(
            DeterministicFakeEmbeddingModel(resolved.model_id), cache
        )
    return CachedEmbeddingModel(
        OpenAICompatibleEmbeddingClient(_live_config(resolved)), cache
    )


def _live_config(resolved: ResolvedModelConfig) -> OpenAICompatibleConfig:
    assert resolved.api_key_env is not None
    api_key = os.environ.get(resolved.api_key_env)
    if not api_key:
        raise RuntimeError(f"missing environment variable {resolved.api_key_env}")
    return OpenAICompatibleConfig(
        base_url=resolved.base_url or "",
        api_key=api_key,
        model=resolved.model_id,
        timeout_s=resolved.timeout_s,
        thinking=resolved.thinking,
        max_tokens=resolved.max_tokens,
    )


def cache_for_run(run_dir: Path) -> FileModelCache:
    return FileModelCache(run_dir / "model_cache")


__all__ = [
    "ModelBundle",
    "ProviderConfig",
    "ProviderKind",
    "PROVIDER_SCHEMA_VERSION",
    "ResolvedModelConfig",
    "build_model_bundle",
    "cache_for_run",
    "resolve_provider_config",
]