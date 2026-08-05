from __future__ import annotations

import os

import pytest

from evoeventmem.infra.migrations import MIGRATIONS
from evoeventmem.infra.postgres_repository import (
    PostgresMemoryRepository,
    RepositoryUnavailableError,
)


def test_unreachable_database_url_raises_repository_unavailable() -> None:
    repository = PostgresMemoryRepository(
        "postgresql://user:secret@127.0.0.1:1/evoeventmem",
        connect_timeout=1.0,
        operation_timeout=5.0,
    )
    try:
        with pytest.raises(RepositoryUnavailableError):
            repository.connect(apply_migrations=True)
        assert not repository.connected
        with pytest.raises(RepositoryUnavailableError, match="not connected"):
            repository.list_for_user("u1")
    finally:
        repository.close()


def test_ping_false_while_disconnected() -> None:
    repository = PostgresMemoryRepository(
        "postgresql://user:secret@127.0.0.1:1/evoeventmem",
        connect_timeout=1.0,
        operation_timeout=5.0,
    )
    try:
        assert repository.ping() is False
    finally:
        repository.close()


@pytest.fixture()
def connected_repository() -> pytest.FixtureRequest:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set; PostgreSQL integration tests are skipped")
    repository = PostgresMemoryRepository(dsn, connect_timeout=5.0, operation_timeout=15.0)
    repository.connect(apply_migrations=True)
    yield repository
    repository.close()


def test_ping_true_when_connected(connected_repository: PostgresMemoryRepository) -> None:
    assert connected_repository.connected
    assert connected_repository.ping() is True


def test_statement_timeout_is_applied(connected_repository: PostgresMemoryRepository) -> None:
    timeout_ms = connected_repository._statement_timeout_ms
    assert timeout_ms > 0


def test_migrations_are_versioned_and_idempotent(
    connected_repository: PostgresMemoryRepository,
) -> None:
    assert {version for version, _ in MIGRATIONS} == {"0001_initial_schema"}

    applied_first = connected_repository.run_migrations()
    applied_second = connected_repository.run_migrations()

    assert applied_first == []
    assert applied_second == []
