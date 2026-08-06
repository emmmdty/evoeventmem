from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    RepositoryUnavailableError,
)


def _require_postgres() -> bool:
    return os.environ.get("EEM_REQUIRE_POSTGRES", "0") == "1"


def _postgres_dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("EEM_DATABASE_URL")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "postgres: requires a live PostgreSQL database (asyncpg pool)"
    )


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _connect_repository(dsn: str) -> AsyncPostgresMemoryRepository:
    repository = AsyncPostgresMemoryRepository(
        dsn,
        connect_timeout=5.0,
        operation_timeout=15.0,
        db_connect_timeout=5.0,
        db_operation_timeout=15.0,
        model_id="test-model",
        dimension=4,
    )
    _run(repository.connect(apply_migrations=True))
    return repository


@pytest.fixture()
def postgres_repository() -> Iterator[AsyncPostgresMemoryRepository]:
    """Provide a scoped async PostgreSQL repository, fail-not-skip on demand."""
    require = _require_postgres()
    dsn = _postgres_dsn()
    if not dsn:
        if require:
            pytest.fail("EEM_REQUIRE_POSTGRES=1 but no DATABASE_URL is configured")
        pytest.skip("DATABASE_URL is not set; PostgreSQL integration tests are skipped")
    try:
        repository = _connect_repository(dsn)
    except (RepositoryUnavailableError, OSError) as exc:
        if require:
            pytest.fail(f"EEM_REQUIRE_POSTGRES=1 but PostgreSQL connection failed: {exc}")
        pytest.skip(f"PostgreSQL connection failed: {exc}")
    yield repository
    _run(repository.close())


@pytest.fixture()
def postgres_repository_unconnected() -> AsyncPostgresMemoryRepository:
    return AsyncPostgresMemoryRepository(
        "postgresql://user:secret@127.0.0.1:1/evoeventmem",
        connect_timeout=1.0,
        operation_timeout=5.0,
        model_id="test-model",
        dimension=4,
    )