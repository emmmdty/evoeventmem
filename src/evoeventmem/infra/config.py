from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


def coerce_dsn(dsn: str) -> str:
    """Normalize SQLAlchemy-style DSN schemes for asyncpg."""
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def redact_dsn(dsn: str) -> str:
    """Mask the password portion of a database URL for structured logs."""
    parsed = urlparse(dsn)
    if parsed.password is None:
        return dsn
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    username = parsed.username or ""
    netloc = f"{username}:***@{hostname}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


@dataclass(frozen=True)
class Settings:
    store: str = "memory"
    database_url: str | None = None
    db_connect_timeout: float = 10.0
    db_operation_timeout: float = 30.0
    db_statement_timeout_ms: int = 10_000
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env: Mapping[str, str] = os.environ if environ is None else environ
        store = env.get("EEM_STORE", "memory").strip().lower()
        if store not in {"memory", "postgres"}:
            raise ValueError(f"EEM_STORE must be 'memory' or 'postgres', got {store!r}")
        database_url = env.get("EEM_DATABASE_URL") or env.get("DATABASE_URL")
        return cls(
            store=store,
            database_url=database_url or None,
            db_connect_timeout=_env_float(env, "EEM_DB_CONNECT_TIMEOUT", 10.0),
            db_operation_timeout=_env_float(env, "EEM_DB_OPERATION_TIMEOUT", 30.0),
            db_statement_timeout_ms=_env_int(env, "EEM_DB_STATEMENT_TIMEOUT_MS", 10_000),
            log_level=env.get("EEM_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    return float(raw)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    return int(raw)
