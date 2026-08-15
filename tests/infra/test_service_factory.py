from __future__ import annotations

from dataclasses import replace

import pytest

from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.infra.config import Settings
from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    RepositoryUnavailableError,
)
from evoeventmem.infra.service_factory import (
    build_async_embedding,
    build_async_repository,
    build_async_service,
)

_UNREACHABLE_DSN = "postgresql://user:swordfish@127.0.0.1:1/evoeventmem"


def _settings(**overrides: object) -> Settings:
    return replace(Settings(), **overrides)  # type: ignore[arg-type]


def test_memory_store_builds_in_memory_repository_and_service() -> None:
    settings = _settings()
    repository = build_async_repository(settings)
    assert isinstance(repository, AsyncInMemoryRepository)
    service = build_async_service(
        settings,
        repository=repository,
        embedding=build_async_embedding(settings),
    )
    assert service.token_overlap_policy is False


def test_postgres_store_requires_database_url() -> None:
    with pytest.raises(RepositoryUnavailableError):
        build_async_repository(_settings(store="postgres"))


def test_postgres_store_builds_postgres_repository_without_connecting() -> None:
    repository = build_async_repository(
        _settings(store="postgres", database_url=_UNREACHABLE_DSN)
    )
    assert isinstance(repository, AsyncPostgresMemoryRepository)


def test_token_overlap_policy_requires_deterministic_provider() -> None:
    with pytest.raises(ValueError, match="token-overlap"):
        build_async_embedding(
            _settings(embedding_policy="token_overlap", embedding_provider="openai_compatible")
        )


def test_token_overlap_policy_enables_service_degraded_search() -> None:
    settings = _settings(embedding_policy="token_overlap")
    service = build_async_service(
        settings,
        repository=build_async_repository(settings),
        embedding=build_async_embedding(settings),
    )
    assert service.token_overlap_policy is True
