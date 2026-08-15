from __future__ import annotations

from typing import Any

from evoeventmem.core.ports import AsyncEmbeddingModel
from evoeventmem.infra.async_embedding import (
    DeterministicAsyncEmbeddingModel,
    build_embedding_model,
)
from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.infra.config import Settings
from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    RepositoryUnavailableError,
)
from evoeventmem.services.async_memory_service import AsyncMemoryService


def build_async_embedding(settings: Settings) -> AsyncEmbeddingModel:
    """Build the embedding adapter from settings. The token-overlap policy is
    development-only and requires the deterministic provider."""
    if settings.embedding_policy == "token_overlap":
        if settings.embedding_provider != "deterministic":
            raise ValueError(
                "token-overlap is a development-only policy and requires "
                "EEM_EMBEDDING_PROVIDER=deterministic"
            )
        return DeterministicAsyncEmbeddingModel(
            model_id=settings.embedding_model_id,
            dimension=settings.embedding_dimension,
        )
    return build_embedding_model(settings=settings)


def build_async_repository(
    settings: Settings,
) -> AsyncInMemoryRepository | AsyncPostgresMemoryRepository:
    """Build the storage repository from settings without connecting.

    A missing database URL with ``EEM_STORE=postgres`` fails fast with
    ``RepositoryUnavailableError`` so the caller can decide between
    fail-closed and development fallback.
    """
    if settings.store == "postgres":
        if settings.database_url is None:
            raise RepositoryUnavailableError(
                "EEM_STORE=postgres but no database URL configured"
            )
        return AsyncPostgresMemoryRepository(
            settings.database_url,
            connect_timeout=settings.db_connect_timeout,
            operation_timeout=settings.db_operation_timeout,
            statement_timeout_ms=settings.db_statement_timeout_ms,
            model_id=settings.embedding_model_id,
            dimension=settings.embedding_dimension,
            schema_version=settings.schema_version,
        )
    return AsyncInMemoryRepository(
        model_id=settings.embedding_model_id,
        dimension=settings.embedding_dimension,
        schema_version=settings.schema_version,
    )


def build_async_service(
    settings: Settings,
    *,
    repository: Any,
    embedding: AsyncEmbeddingModel,
) -> AsyncMemoryService:
    return AsyncMemoryService(
        repository,
        embedding=embedding,
        token_overlap_policy=settings.embedding_policy == "token_overlap",
    )
