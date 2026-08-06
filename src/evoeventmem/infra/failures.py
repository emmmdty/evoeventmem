from __future__ import annotations

"""Stable, bounded failure reason codes for API responses, logs, and metrics.

Reason codes are part of the public failure contract: clients and operators
may rely on them. They never embed UUIDs, request text, or secrets.
"""

REASON_STORE_UNAVAILABLE = "store_unavailable"
REASON_STORE_TIMEOUT = "store_timeout"
REASON_EMBEDDING_UNAVAILABLE = "embedding_unavailable"
REASON_EMBEDDING_DIMENSION_MISMATCH = "embedding_dimension_mismatch"
REASON_SCOPE_MISMATCH = "scope_mismatch"
REASON_MEMORY_ID_COLLISION = "memory_id_collision"
REASON_NOT_FOUND_OR_OUT_OF_SCOPE = "not_found_or_out_of_scope"
REASON_INVALID_REQUEST = "invalid_request"
REASON_INTERNAL = "internal_error"

KNOWN_REASONS: frozenset[str] = frozenset(
    {
        REASON_STORE_UNAVAILABLE,
        REASON_STORE_TIMEOUT,
        REASON_EMBEDDING_UNAVAILABLE,
        REASON_EMBEDDING_DIMENSION_MISMATCH,
        REASON_SCOPE_MISMATCH,
        REASON_MEMORY_ID_COLLISION,
        REASON_NOT_FOUND_OR_OUT_OF_SCOPE,
        REASON_INVALID_REQUEST,
        REASON_INTERNAL,
    }
)

# The origin of a failure: where the fault was observed.
REASON_SOURCES: dict[str, str] = {
    REASON_STORE_UNAVAILABLE: "postgres",
    REASON_STORE_TIMEOUT: "postgres",
    REASON_EMBEDDING_UNAVAILABLE: "embedding",
    REASON_EMBEDDING_DIMENSION_MISMATCH: "embedding",
}


def reason_source(reason: str) -> str:
    return REASON_SOURCES.get(reason, "api")


def reason_for_status(status: int, detail: object) -> str:
    """Map an HTTP response to a stable reason code.

    A ``detail`` string that is itself a known reason code wins; otherwise the
    status code provides the mapping.
    """
    if isinstance(detail, str) and detail in KNOWN_REASONS:
        return detail
    if status == 400:
        return REASON_INVALID_REQUEST
    if status == 404:
        return REASON_NOT_FOUND_OR_OUT_OF_SCOPE
    if status == 409:
        return REASON_MEMORY_ID_COLLISION
    if status == 422:
        return REASON_INVALID_REQUEST
    if status == 502:
        return REASON_EMBEDDING_UNAVAILABLE
    if status == 503:
        return REASON_STORE_UNAVAILABLE
    return REASON_INTERNAL
