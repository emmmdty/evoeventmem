"""OpenCode MCP adapter: a thin tool surface over the public memory service.

The adapter only calls the public methods of a memory service (``write``,
``search``, ``explain``, ``feedback``, ``forget``); it never implements
extraction, ETEC, QEMR, or persistence logic. Every tool returns a stable
JSON envelope so the agent loop always receives a parseable result, even
when the underlying service is unavailable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from evoeventmem.core.ports import RequestScope, SearchHit
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord
from evoeventmem.infra.async_embedding import (
    DeterministicAsyncEmbeddingModel,
    EmbeddingModelError,
)
from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.infra.config import Settings
from evoeventmem.infra.failures import (
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_INTERNAL,
    REASON_INVALID_REQUEST,
    REASON_MEMORY_ID_COLLISION,
    REASON_NOT_FOUND_OR_OUT_OF_SCOPE,
    REASON_SCOPE_MISMATCH,
    REASON_STORE_UNAVAILABLE,
)
from evoeventmem.infra.postgres_repository import RepositoryUnavailableError
from evoeventmem.services.async_memory_service import (
    AsyncMemoryService,
    ScopeMismatchError,
)
from evoeventmem.services.memory_service import (
    MemoryExplainResult,
    MemoryIdentityCollisionError,
)

DEFAULT_TENANT_ID = "default"
_SERVER_NAME = "evoeventmem"
_STATUS_OK = "ok"
_STATUS_NOT_FOUND = "not_found"
_STATUS_UNAVAILABLE = "unavailable"
_STATUS_ERROR = "error"

_UNAVAILABLE_GUIDANCE = (
    "Memory service is unavailable; proceed without memory retrieval this turn."
)

_SEARCH_LIMIT = Annotated[int, Field(ge=1, le=50)]
_TIMELINE_LIMIT = Annotated[int, Field(ge=1, le=100)]


class MemoryServicePort(Protocol):
    """Public service surface the adapter is allowed to call."""

    async def write(self, scope: RequestScope, memory: MemoryRecord) -> MemoryRecord: ...

    async def search(
        self, scope: RequestScope, query: str, limit: int = 5
    ) -> list[SearchHit]: ...

    async def explain(
        self, scope: RequestScope, memory_id: UUID
    ) -> MemoryExplainResult | None: ...

    async def feedback(
        self,
        scope: RequestScope,
        memory_id: UUID,
        *,
        outcome: str,
        rating: float | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord | None: ...

    async def forget(
        self,
        scope: RequestScope,
        memory_id: UUID,
        *,
        request_id: str | None = None,
    ) -> MemoryRecord | None: ...


def _scope(user_id: str, tenant_id: str, session_id: str | None) -> RequestScope:
    return RequestScope(tenant_id=tenant_id, user_id=user_id, session_id=session_id)


def _parse_memory_id(value: str) -> UUID:
    return UUID(value)


def _parse_event_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _envelope(status: str, message: str, data: Any) -> dict[str, Any]:
    return {"status": status, "message": message, "data": data}


def _unavailable(reason: str, detail: str) -> dict[str, Any]:
    return _envelope(
        _STATUS_UNAVAILABLE,
        f"{_UNAVAILABLE_GUIDANCE} Reason: {reason} ({detail})",
        {"reason": reason},
    )


def _error(reason: str, detail: str) -> dict[str, Any]:
    return _envelope(_STATUS_ERROR, f"Invalid request: {detail}", {"reason": reason})


class _NotFound(Exception):
    """Internal signal: the target memory is missing or out of scope."""


async def _call(
    operation: Callable[[], Awaitable[Any]],
    *,
    render: Callable[[Any], Any],
) -> dict[str, Any]:
    try:
        data = await operation()
    except _NotFound:
        return _envelope(
            _STATUS_NOT_FOUND,
            "memory not found or out of scope",
            {"reason": REASON_NOT_FOUND_OR_OUT_OF_SCOPE},
        )
    except RepositoryUnavailableError as exc:
        return _unavailable(REASON_STORE_UNAVAILABLE, str(exc))
    except EmbeddingModelError as exc:
        return _unavailable(REASON_EMBEDDING_UNAVAILABLE, str(exc))
    except MemoryIdentityCollisionError as exc:
        return _error(REASON_MEMORY_ID_COLLISION, str(exc))
    except ScopeMismatchError as exc:
        return _error(REASON_SCOPE_MISMATCH, str(exc))
    except ValueError as exc:
        return _error(REASON_INVALID_REQUEST, str(exc))
    except Exception as exc:
        return _unavailable(REASON_INTERNAL, str(exc))
    return _envelope(_STATUS_OK, "ok", render(data))


def _evidence_payload(evidence: EvidenceRef) -> dict[str, Any]:
    return {
        "source_type": evidence.source_type,
        "source_id": evidence.source_id,
        "locator": evidence.locator,
        "quote": evidence.quote,
    }


def _memory_payload(memory: MemoryRecord) -> dict[str, Any]:
    return {
        "memory_id": str(memory.memory_id),
        "kind": memory.memory_kind.value,
        "content": memory.content,
        "entities": [entity.name for entity in memory.entities],
        "evidence": [_evidence_payload(ref) for ref in memory.evidence_refs],
        "event_time": memory.event_time.isoformat() if memory.event_time else None,
        "valid_from": memory.valid_from.isoformat() if memory.valid_from else None,
        "valid_to": memory.valid_to.isoformat() if memory.valid_to else None,
        "status": memory.status.value,
        "metadata": memory.metadata,
        "score": None,
        "reason": None,
        "source": None,
        "fallback": False,
    }


def _hit_payload(hit: SearchHit) -> dict[str, Any]:
    payload = _memory_payload(hit.memory)
    payload.update(
        {
            "score": hit.score,
            "reason": hit.reason,
            "source": hit.source,
            "fallback": hit.fallback,
            "fallback_reason": hit.fallback_reason,
        }
    )
    return payload


def _hits_payload(hits: list[SearchHit]) -> dict[str, Any]:
    return {"hits": [_hit_payload(hit) for hit in hits]}


def _explain_payload(result: MemoryExplainResult) -> dict[str, Any]:
    return {
        "memory": _memory_payload(result.memory),
        "related": [_memory_payload(memory) for memory in result.related],
    }


def _timeline_order(hits: list[SearchHit]) -> list[SearchHit]:
    return sorted(
        hits,
        key=lambda hit: (
            hit.memory.event_time is None,
            hit.memory.event_time or datetime.min.replace(tzinfo=UTC),
            -hit.score,
        ),
    )


def build_server(service: MemoryServicePort, *, server_name: str = _SERVER_NAME) -> FastMCP:
    """Build the MCP server. The service is injected; tests pass a fake."""
    mcp = FastMCP(server_name)

    @mcp.tool()
    async def memory_search(
        query: str,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
        limit: _SEARCH_LIMIT = 5,
    ) -> dict[str, Any]:
        """Search the user's memories. Returns hits with content, score,
        retrieval reason, and evidence references for the agent to cite."""
        return await _call(
            lambda: service.search(_scope(user_id, tenant_id, session_id), query, limit),
            render=_hits_payload,
        )

    @mcp.tool()
    async def memory_observe(
        observation: str,
        user_id: str,
        source_type: str = "opencode",
        source_id: str | None = None,
        kind: MemoryKind = MemoryKind.FACT,
        event_time: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an observation as a durable memory with evidence provenance.
        Repeated observations of the same content return the existing memory."""
        memory = MemoryRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            memory_kind=kind,
            content=observation,
            event_time=_parse_event_time(event_time),
            evidence_refs=[
                EvidenceRef(
                    source_type=source_type,
                    source_id=source_id or str(uuid4()),
                    quote=observation,
                )
            ],
            metadata={"channel": "opencode"},
        )
        return await _call(
            lambda: service.write(_scope(user_id, tenant_id, session_id), memory),
            render=lambda written: {"memory": _memory_payload(written)},
        )

    @mcp.tool()
    async def memory_explain(
        memory_id: str,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Explain a memory: its content, evidence, and temporally or
        derivationally related memories."""
        scope = _scope(user_id, tenant_id, session_id)
        try:
            parsed_id = _parse_memory_id(memory_id)
        except ValueError as exc:
            return _error(REASON_INVALID_REQUEST, str(exc))

        async def operation() -> dict[str, Any]:
            result = await service.explain(scope, parsed_id)
            if result is None:
                raise _NotFound
            return _explain_payload(result)

        return await _call(operation, render=lambda data: data)

    @mcp.tool()
    async def memory_timeline(
        entity_or_topic: str,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
        limit: _TIMELINE_LIMIT = 10,
    ) -> dict[str, Any]:
        """Return retrieved memories about an entity or topic ordered by event
        time (oldest first; untimed events last). Retrieval is delegated to the
        service; ordering is presentation only."""
        scope = _scope(user_id, tenant_id, session_id)

        async def operation() -> dict[str, Any]:
            hits = await service.search(scope, entity_or_topic, limit)
            return {"events": [_hit_payload(hit) for hit in _timeline_order(hits)]}

        return await _call(operation, render=lambda data: data)

    @mcp.tool()
    async def memory_feedback(
        memory_id: str,
        outcome: str,
        user_id: str,
        rating: Annotated[float | None, Field(ge=0.0, le=1.0)] = None,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Record how useful a memory was for the current task."""
        scope = _scope(user_id, tenant_id, session_id)
        try:
            parsed_id = _parse_memory_id(memory_id)
        except ValueError as exc:
            return _error(REASON_INVALID_REQUEST, str(exc))

        async def operation() -> dict[str, Any]:
            updated = await service.feedback(
                scope,
                parsed_id,
                outcome=outcome,
                rating=rating,
            )
            if updated is None:
                raise _NotFound
            return {"memory": _memory_payload(updated)}

        return await _call(operation, render=lambda data: data)

    @mcp.tool()
    async def memory_forget(
        memory_id: str,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Mark a memory as forgotten; it no longer appears in search."""
        scope = _scope(user_id, tenant_id, session_id)
        try:
            parsed_id = _parse_memory_id(memory_id)
        except ValueError as exc:
            return _error(REASON_INVALID_REQUEST, str(exc))

        async def operation() -> dict[str, Any]:
            updated = await service.forget(scope, parsed_id)
            if updated is None:
                raise _NotFound
            return {"memory": _memory_payload(updated)}

        return await _call(operation, render=lambda data: data)

    return mcp


def _build_default_service() -> AsyncMemoryService:
    """Dev-mode default for the stdio entry point: deterministic embeddings
    over the in-memory repository. Production deployments inject a real
    service via ``build_server`` instead."""
    settings = Settings.from_env()
    embedding = DeterministicAsyncEmbeddingModel(
        model_id=settings.embedding_model_id,
        dimension=settings.embedding_dimension,
    )
    repository = AsyncInMemoryRepository(
        model_id=settings.embedding_model_id,
        dimension=settings.embedding_dimension,
        schema_version=settings.schema_version,
    )
    return AsyncMemoryService(
        repository,
        embedding=embedding,
        token_overlap_policy=settings.embedding_policy == "token_overlap",
    )


def main() -> None:
    build_server(_build_default_service()).run(transport="stdio")


if __name__ == "__main__":
    main()
