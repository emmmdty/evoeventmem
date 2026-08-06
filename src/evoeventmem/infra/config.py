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
    embedding_model_id: str = "test-embed"
    embedding_dimension: int = 4
    embedding_provider: str = "deterministic"
    embedding_base_url: str | None = None
    embedding_api_key_env: str = "EMBEDDING_API_KEY"
    embedding_timeout_s: float = 30.0
    schema_version: str = "memory.v1"
    embedding_policy: str = "vector"

    @property
    def embedding_api_key(self) -> str | None:
        return os.environ.get(self.embedding_api_key_env)

    @property
    def schema_model(self) -> str:
        return self.embedding_model_id

    @property
    def schema_dimension(self) -> int:
        return self.embedding_dimension

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env: Mapping[str, str] = os.environ if environ is None else environ
        store = env.get("EEM_STORE", "memory").strip().lower()
        if store not in {"memory", "postgres"}:
            raise ValueError(f"EEM_STORE must be 'memory' or 'postgres', got {store!r}")
        database_url = env.get("EEM_DATABASE_URL") or env.get("DATABASE_URL")
        provider = env.get("EEM_EMBEDDING_PROVIDER", "deterministic").strip().lower()
        if provider not in {"deterministic", "openai_compatible"}:
            raise ValueError(
                f"EEM_EMBEDDING_PROVIDER must be 'deterministic' or "
                f"'openai_compatible', got {provider!r}"
            )
        embedding_policy = env.get("EEM_EMBEDDING_POLICY", "vector").strip().lower()
        if embedding_policy not in {"vector", "token_overlap"}:
            raise ValueError(
                f"EEM_EMBEDDING_POLICY must be 'vector' or 'token_overlap', got "
                f"{embedding_policy!r}"
            )
        return cls(
            store=store,
            database_url=database_url or None,
            db_connect_timeout=_env_float(env, "EEM_DB_CONNECT_TIMEOUT", 10.0),
            db_operation_timeout=_env_float(env, "EEM_DB_OPERATION_TIMEOUT", 30.0),
            db_statement_timeout_ms=_env_int(env, "EEM_DB_STATEMENT_TIMEOUT_MS", 10_000),
            log_level=env.get("EEM_LOG_LEVEL", "INFO").strip().upper() or "INFO",
            embedding_model_id=env.get("EEM_EMBEDDING_MODEL_ID", "test-embed").strip(),
            embedding_dimension=_env_int(env, "EEM_EMBEDDING_DIMENSION", 4),
            embedding_provider=provider,
            embedding_base_url=env.get("EEM_EMBEDDING_BASE_URL") or None,
            embedding_api_key_env=(
                env.get("EEM_EMBEDDING_API_KEY_ENV", "EMBEDDING_API_KEY").strip()
                or "EMBEDDING_API_KEY"
            ),
            embedding_timeout_s=_env_float(env, "EEM_EMBEDDING_TIMEOUT_S", 30.0),
            schema_version=env.get("EEM_SCHEMA_VERSION", "memory.v1").strip(),
            embedding_policy=embedding_policy,
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


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
