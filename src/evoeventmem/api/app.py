from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from evoeventmem.core.ports import RequestScope, SearchHit
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord, MemorySearchHit
from evoeventmem.infra.async_embedding import EmbeddingModelError
from evoeventmem.infra.async_in_memory_repository import AsyncInMemoryRepository
from evoeventmem.infra.config import Settings, redact_dsn
from evoeventmem.infra.failures import (
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_INTERNAL,
    REASON_MEMORY_ID_COLLISION,
    REASON_SCOPE_MISMATCH,
    REASON_STORE_UNAVAILABLE,
    reason_for_status,
    reason_source,
)
from evoeventmem.infra.logging import configure_logging, request_id_var
from evoeventmem.infra.metrics import MetricsRegistry
from evoeventmem.infra.postgres_repository import (
    AsyncPostgresMemoryRepository,
    RepositoryUnavailableError,
)
from evoeventmem.infra.service_factory import (
    build_async_embedding,
    build_async_repository,
    build_async_service,
)
from evoeventmem.services.async_memory_service import (
    AsyncMemoryService,
    ScopeMismatchError,
)
from evoeventmem.services.memory_service import MemoryIdentityCollisionError

logger = logging.getLogger("evoeventmem")


class _V1EvidenceResponse(BaseModel):
    source_type: str
    source_id: str
    locator: str | None
    quote: str | None

    @classmethod
    def from_domain(cls, evidence: EvidenceRef) -> _V1EvidenceResponse:
        return cls(
            source_type=evidence.source_type,
            source_id=evidence.source_id,
            locator=evidence.locator,
            quote=evidence.quote,
        )


class _V1MemoryResponse(BaseModel):
    memory_id: UUID
    tenant_id: str | None = None
    user_id: str
    session_id: str | None = None
    kind: MemoryKind
    content: str
    entities: list[str]
    evidence: list[_V1EvidenceResponse]
    event_time: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    supersedes: UUID | None
    confidence: float
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_domain(cls, memory: MemoryRecord) -> _V1MemoryResponse:
        return cls(
            memory_id=memory.memory_id,
            tenant_id=memory.tenant_id,
            user_id=memory.user_id,
            session_id=memory.session_id,
            kind=memory.memory_kind,
            content=memory.content,
            entities=[entity.name for entity in memory.entities],
            evidence=[
                _V1EvidenceResponse.from_domain(evidence)
                for evidence in memory.evidence_refs
            ],
            event_time=memory.event_time,
            valid_from=memory.valid_from,
            valid_to=memory.valid_to,
            supersedes=memory.supersedes[0] if memory.supersedes else None,
            confidence=memory.confidence,
            metadata=memory.metadata,
            created_at=memory.created_at,
        )


class _V1MemorySearchHitResponse(BaseModel):
    memory: _V1MemoryResponse
    score: float
    reason: str

    @classmethod
    def from_domain(cls, hit: MemorySearchHit) -> _V1MemorySearchHitResponse:
        return cls(
            memory=_V1MemoryResponse.from_domain(hit.memory),
            score=hit.score,
            reason=hit.reason,
        )

    @classmethod
    def from_hit(cls, hit: SearchHit) -> _V1MemorySearchHitResponse:
        return cls(
            memory=_V1MemoryResponse.from_domain(hit.memory),
            score=hit.score,
            reason=hit.reason,
        )


class _V1ExplainResponse(BaseModel):
    memory: _V1MemoryResponse
    related: list[_V1MemoryResponse]


class _V1FeedbackRequest(BaseModel):
    outcome: str = Field(min_length=1)
    rating: float | None = Field(default=None, ge=0.0, le=1.0)


async def _observability_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Record every request with request ID, route template, stable reason
    code, failure source, and duration in a try/except/finally block.

    Degraded serving (development fallback or token-overlap policy) is marked
    on every response with ``X-EvoEventMem-Degraded: true``. Metrics always use
    route templates, never UUID-bearing raw paths.
    """
    started = time.monotonic()
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    token = request_id_var.set(request_id)
    status = 500
    reason: str | None = None
    try:
        response = await call_next(request)
        path_template = _path_template(request)
        status = response.status_code
        reason = reason_for_status(status, _extract_detail(response))
        response.headers["X-Request-ID"] = request_id
        if _is_degraded(request.app):
            response.headers["X-EvoEventMem-Degraded"] = "true"
        return response
    except Exception as exc:
        path_template = _path_template(request)
        status = 500
        reason = REASON_INTERNAL
        logger.exception(
            "unhandled request failure",
            extra={
                "event": "http.request",
                "method": request.method,
                "path": path_template,
                "status": status,
                "reason": reason,
                "source": reason_source(reason),
            },
        )
        raise exc
    finally:
        duration = time.monotonic() - started
        resolved_reason = reason or REASON_INTERNAL
        request.app.state.http_requests.inc(
            labels={
                "method": request.method,
                "path": _path_template(request),
                "status": str(status),
            }
        )
        request.app.state.http_duration.observe(
            duration,
            labels={"method": request.method, "path": _path_template(request)},
        )
        if status >= 400:
            request.app.state.http_exceptions.inc(
                labels={
                    "reason": resolved_reason,
                    "source": reason_source(resolved_reason),
                }
            )
        if _is_degraded(request.app):
            request.app.state.degraded_responses.inc()
        logger.info(
            "http request completed",
            extra={
                "event": "http.request",
                "request_id": request_id,
                "method": request.method,
                "path": path_template,
                "status": status,
                "reason": resolved_reason,
                "source": reason_source(resolved_reason),
                "duration_ms": round(duration * 1000.0, 3),
                "degraded": _is_degraded(request.app),
            },
        )
        request_id_var.reset(token)


def _extract_detail(response: Response) -> object:
    try:
        body = bytes(response.body)
        payload = json.loads(body)
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload.get("detail")
    return None


def _path_template(request: Request) -> str:
    route = request.scope.get("route")
    return route.path if route is not None else "unmatched"


def _is_degraded(app: FastAPI) -> bool:
    settings = cast(Settings, app.state.settings)
    return app.state.store == "memory-degraded" or settings.embedding_policy == "token_overlap"


def _fail_closed_check(request: Request) -> None:
    if request.app.state.store == "postgres-unavailable":
        raise HTTPException(status_code=503, detail=REASON_STORE_UNAVAILABLE)


def _scope_dependency(
    x_tenant_id: str = Header(alias="X-Tenant-Id", min_length=1),
    x_user_id: str = Header(alias="X-User-Id", min_length=1),
    x_session_id: str | None = Header(alias="X-Session-Id", default=None),
) -> RequestScope:
    """Required tenant/user identity with an optional session narrowing.

    The concrete header names are part of the API contract: ``X-Tenant-Id``,
    ``X-User-Id``, and the optional ``X-Session-Id``. Missing tenant or user
    headers fail the request before any handler runs.
    """
    return RequestScope(tenant_id=x_tenant_id, user_id=x_user_id, session_id=x_session_id)


ScopeDependency = Depends(_scope_dependency)


def _get_service(app: FastAPI) -> AsyncMemoryService:
    return cast(AsyncMemoryService, app.state.service)


def _embedding_identity(settings: Settings) -> dict[str, str | int]:
    return {
        "provider": settings.embedding_provider,
        "model_id": settings.embedding_model_id,
        "dimension": settings.embedding_dimension,
    }


def _emit_store_fallback(app: FastAPI, reason: str) -> None:
    app.state.store_fallback.inc()
    logger.warning(
        "configured store unavailable; serving from in-memory repository",
        extra={
            "event": "store.fallback",
            "requested_store": "postgres",
            "store": "memory",
            "degraded": True,
            "reason": reason,
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings if settings is not None else Settings.from_env()
    configure_logging(resolved_settings.log_level)
    embedding = build_async_embedding(resolved_settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository: AsyncPostgresMemoryRepository | AsyncInMemoryRepository | None = None
        if resolved_settings.store == "postgres":
            try:
                candidate = build_async_repository(resolved_settings)
                if not isinstance(candidate, AsyncPostgresMemoryRepository):
                    raise RepositoryUnavailableError(
                        "EEM_STORE=postgres produced a non-postgres repository"
                    )
                repository = candidate
                await repository.connect(run_migrations=True)
            except RepositoryUnavailableError as exc:
                if resolved_settings.allow_development_fallback:
                    _emit_store_fallback(app, str(exc))
                    repository = AsyncInMemoryRepository(
                        model_id=resolved_settings.embedding_model_id,
                        dimension=resolved_settings.embedding_dimension,
                        schema_version=resolved_settings.schema_version,
                    )
                    app.state.store = "memory-degraded"
                else:
                    app.state.store = "postgres-unavailable"
                    logger.warning(
                        "postgres store unavailable; failing closed",
                        extra={
                            "event": "store.unavailable",
                            "store": "postgres",
                            "reason": REASON_STORE_UNAVAILABLE,
                            "degraded": False,
                        },
                    )
            else:
                app.state.repository = repository
                app.state.store = "postgres"
                logger.info(
                    "connected to postgres store",
                    extra={
                        "event": "store.connected",
                        "store": "postgres",
                        "dsn": (
                            redact_dsn(resolved_settings.database_url)
                            if resolved_settings.database_url
                            else None
                        ),
                    },
                )
        else:
            repository = AsyncInMemoryRepository(
                model_id=resolved_settings.embedding_model_id,
                dimension=resolved_settings.embedding_dimension,
                schema_version=resolved_settings.schema_version,
            )
            app.state.store = "memory"
            logger.info(
                "started in-memory store",
                extra={"event": "store.started", "store": "memory"},
            )
        if repository is not None:
            app.state.repository = repository
            app.state.service = build_async_service(
                resolved_settings,
                repository=repository,
                embedding=embedding,
            )
        try:
            yield
        finally:
            if isinstance(repository, AsyncPostgresMemoryRepository):
                await repository.close()

    app = FastAPI(title="EvoEventMem", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.metrics = MetricsRegistry()
    app.state.store = "memory"
    app.state.repository = AsyncInMemoryRepository(
        model_id=resolved_settings.embedding_model_id,
        dimension=resolved_settings.embedding_dimension,
        schema_version=resolved_settings.schema_version,
    )
    app.state.service = build_async_service(
        resolved_settings,
        repository=app.state.repository,
        embedding=embedding,
    )
    app.state.embedding = embedding
    app.state.http_requests = app.state.metrics.counter(
        "evoeventmem_http_requests_total",
        "HTTP requests served by the memory service API.",
        labels=("method", "path", "status"),
    )
    app.state.http_duration = app.state.metrics.histogram(
        "evoeventmem_http_request_duration_seconds",
        "HTTP request latency in seconds.",
        labels=("method", "path"),
    )
    app.state.store_fallback = app.state.metrics.counter(
        "evoeventmem_store_fallback_total",
        "Count of observable fallbacks from the configured store to in-memory.",
    )
    app.state.http_exceptions = app.state.metrics.counter(
        "evoeventmem_http_exceptions_total",
        "HTTP exception responses with a stable reason code and failure source.",
        labels=("reason", "source"),
    )
    app.state.degraded_responses = app.state.metrics.counter(
        "evoeventmem_degraded_responses_total",
        "Responses served in an explicitly degraded development mode.",
    )

    app.middleware("http")(_observability_middleware)

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        body: dict[str, Any] = {"status": "ok"}
        if _is_degraded(request.app):
            body["degraded"] = True
        return body

    @app.get("/readiness")
    async def readiness(request: Request) -> dict[str, Any]:
        store = request.app.state.store
        settings = cast(Settings, request.app.state.settings)
        embedding = _embedding_identity(settings)
        if store == "postgres-unavailable":
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "degraded",
                    "store": "postgres-unavailable",
                    "reason": REASON_STORE_UNAVAILABLE,
                    "embedding": embedding,
                },
            )
        if store == "postgres":
            repository = request.app.state.repository
            if isinstance(repository, AsyncPostgresMemoryRepository):
                ping = await repository.ping()
                if ping.ok:
                    if settings.embedding_policy == "token_overlap":
                        return {
                            "status": "degraded",
                            "store": "postgres",
                            "reason": "development_token_overlap_policy",
                            "embedding": embedding,
                        }
                    return {"status": "ready", "store": "postgres", "embedding": embedding}
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "degraded",
                    "store": "postgres",
                    "reason": REASON_STORE_UNAVAILABLE,
                    "embedding": embedding,
                },
            )
        if store == "memory-degraded":
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "degraded",
                    "store": "memory-degraded",
                    "reason": REASON_STORE_UNAVAILABLE,
                    "embedding": embedding,
                },
            )
        if settings.embedding_policy == "token_overlap":
            return {
                "status": "degraded",
                "store": "memory",
                "reason": "development_token_overlap_policy",
                "embedding": embedding,
            }
        return {"status": "ready", "store": "memory", "embedding": embedding}

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> Response:
        return Response(
            content=request.app.state.metrics.render_text(),
            media_type="text/plain; version=0.0.4",
        )

    @app.post("/v1/memories", response_model=_V1MemoryResponse)
    async def write_memory(
        memory: MemoryRecord,
        request: Request,
        scope: RequestScope = ScopeDependency,
    ) -> _V1MemoryResponse:
        _fail_closed_check(request)
        try:
            written = await _get_service(request.app).write(scope, memory)
        except MemoryIdentityCollisionError as exc:
            logger.info(
                "memory write rejected due to memory_id collision",
                extra={
                    "event": "memory.write_rejected",
                    "memory_id": str(memory.memory_id),
                },
            )
            raise HTTPException(status_code=409, detail=REASON_MEMORY_ID_COLLISION) from exc
        except ScopeMismatchError as exc:
            raise HTTPException(status_code=400, detail=REASON_SCOPE_MISMATCH) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except EmbeddingModelError as exc:
            raise HTTPException(status_code=502, detail=REASON_EMBEDDING_UNAVAILABLE) from exc
        except RepositoryUnavailableError as exc:
            raise HTTPException(status_code=503, detail=REASON_STORE_UNAVAILABLE) from exc
        logger.info(
            "memory written",
            extra={"event": "memory.written", "memory_id": str(written.memory_id)},
        )
        return _V1MemoryResponse.from_domain(written)

    @app.get("/v1/memories/search", response_model=list[_V1MemorySearchHitResponse])
    async def search_memories(
        request: Request,
        scope: RequestScope = ScopeDependency,
        q: str = Query(min_length=1),
        limit: int = Query(default=5, ge=1, le=50),
    ) -> list[_V1MemorySearchHitResponse]:
        _fail_closed_check(request)
        try:
            hits = await _get_service(request.app).search(scope, q, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except EmbeddingModelError as exc:
            raise HTTPException(status_code=502, detail=REASON_EMBEDDING_UNAVAILABLE) from exc
        except RepositoryUnavailableError as exc:
            raise HTTPException(status_code=503, detail=REASON_STORE_UNAVAILABLE) from exc
        return [_V1MemorySearchHitResponse.from_hit(hit) for hit in hits]

    @app.get("/v1/memories/{memory_id}/explain", response_model=_V1ExplainResponse)
    async def explain_memory(
        request: Request,
        memory_id: UUID,
        scope: RequestScope = ScopeDependency,
    ) -> _V1ExplainResponse:
        _fail_closed_check(request)
        result = await _get_service(request.app).explain(scope, memory_id)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="memory not found or out of scope",
            )
        return _V1ExplainResponse(
            memory=_V1MemoryResponse.from_domain(result.memory),
            related=[_V1MemoryResponse.from_domain(item) for item in result.related],
        )

    @app.post("/v1/memories/{memory_id}/feedback", response_model=_V1MemoryResponse)
    async def feedback_memory(
        request: Request,
        memory_id: UUID,
        payload: _V1FeedbackRequest,
        scope: RequestScope = ScopeDependency,
    ) -> _V1MemoryResponse:
        _fail_closed_check(request)
        try:
            updated = await _get_service(request.app).feedback(
                scope,
                memory_id,
                outcome=payload.outcome,
                rating=payload.rating,
                request_id=request_id_var.get(),
            )
        except RepositoryUnavailableError as exc:
            raise HTTPException(status_code=503, detail=REASON_STORE_UNAVAILABLE) from exc
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail="memory not found or out of scope",
            )
        return _V1MemoryResponse.from_domain(updated)

    @app.post("/v1/memories/{memory_id}/forget", response_model=_V1MemoryResponse)
    async def forget_memory(
        request: Request,
        memory_id: UUID,
        scope: RequestScope = ScopeDependency,
    ) -> _V1MemoryResponse:
        _fail_closed_check(request)
        try:
            updated = await _get_service(request.app).forget(
                scope,
                memory_id,
                request_id=request_id_var.get(),
            )
        except RepositoryUnavailableError as exc:
            raise HTTPException(status_code=503, detail=REASON_STORE_UNAVAILABLE) from exc
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail="memory not found or out of scope",
            )
        logger.info(
            "memory forgotten",
            extra={"event": "memory.forgotten", "memory_id": str(memory_id)},
        )
        return _V1MemoryResponse.from_domain(updated)

    return app


app = create_app()
