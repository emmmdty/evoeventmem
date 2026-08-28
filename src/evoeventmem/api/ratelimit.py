"""Rate limiting middleware using slowapi with token bucket algorithm.

Provides per-tenant rate limiting based on configurable request limits.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger("evoeventmem")

# Default rate limits (requests per minute)
DEFAULT_WRITE_LIMIT = "30/minute"
DEFAULT_READ_LIMIT = "60/minute"
DEFAULT_FORGET_LIMIT = "10/minute"


def _get_tenant_key(request: Request) -> str:
    """Extract tenant_id from request headers for rate limiting.

    Falls back to client IP if tenant header is missing.
    """
    tenant_id = request.headers.get("X-Tenant-Id")
    if tenant_id:
        return f"tenant:{tenant_id}"
    return get_remote_address(request)


def create_limiter(
    write_limit: str = DEFAULT_WRITE_LIMIT,
    read_limit: str = DEFAULT_READ_LIMIT,
    forget_limit: str = DEFAULT_FORGET_LIMIT,
) -> Limiter:
    """Create a rate limiter with per-tenant token bucket algorithm."""
    return Limiter(
        key_func=_get_tenant_key,
        default_limits=[],
        storage_uri="memory://",
    )


def setup_rate_limiting(
    app: FastAPI,
    write_limit: str = DEFAULT_WRITE_LIMIT,
    read_limit: str = DEFAULT_READ_LIMIT,
    forget_limit: str = DEFAULT_FORGET_LIMIT,
) -> Limiter:
    """Configure rate limiting for the FastAPI application.

    Args:
        app: FastAPI application instance
        write_limit: Rate limit for write operations (POST /v1/memories)
        read_limit: Rate limit for read operations (GET /v1/memories/search)
        forget_limit: Rate limit for forget operations (POST /v1/memories/{id}/forget)

    Returns:
        Configured Limiter instance for use in route decorators
    """
    limiter = create_limiter(write_limit, read_limit, forget_limit)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    logger.info(
        "rate limiting configured",
        extra={
            "event": "ratelimit.configured",
            "write_limit": write_limit,
            "read_limit": read_limit,
            "forget_limit": forget_limit,
        },
    )

    return limiter
