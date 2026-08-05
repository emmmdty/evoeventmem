from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from evoeventmem.core.ports import MemoryRepository
from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord, MemorySearchHit
from evoeventmem.infra.config import Settings, redact_dsn
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.infra.logging import configure_logging, request_id_var
from evoeventmem.infra.metrics import MetricsRegistry
from evoeventmem.infra.postgres_repository import (
    PostgresMemoryRepository,
    RepositoryUnavailableError,
)
from evoeventmem.services.memory_service import MemoryIdentityCollisionError, MemoryService

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
    user_id: str
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
            user_id=memory.user_id,
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


class _V1ExplainResponse(BaseModel):
    memory: _V1MemoryResponse
    related: list[_V1MemoryResponse]


class _V1FeedbackRequest(BaseModel):
    outcome: str = Field(min_length=1)
    rating: float | None = Field(default=None, ge=0.0, le=1.0)


async def _request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


async def _metrics_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - started
    request.app.state.http_requests.inc(
        labels={
            "method": request.method,
            "path": request.url.path,
            "status": str(response.status_code),
        }
    )
    request.app.state.http_duration.observe(
        duration,
        labels={"method": request.method, "path": request.url.path},
    )
    return response


def _get_service(app: FastAPI) -> MemoryService:
    service: MemoryService | None = app.state.service
    if service is None:
        service = MemoryService(InMemoryMemoryRepository())
        app.state.service = service
    return service


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


def _build_postgres_repository(settings: Settings) -> PostgresMemoryRepository:
    if settings.database_url is None:
        raise RepositoryUnavailableError("EEM_STORE=postgres but no database URL configured")
    return PostgresMemoryRepository(
        settings.database_url,
        connect_timeout=settings.db_connect_timeout,
        operation_timeout=settings.db_operation_timeout,
        statement_timeout_ms=settings.db_statement_timeout_ms,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings if settings is not None else Settings.from_env()
    configure_logging(resolved_settings.log_level)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository: MemoryRepository
        if resolved_settings.store == "postgres":
            try:
                repository = _build_postgres_repository(resolved_settings)
                repository.connect(apply_migrations=True)
            except RepositoryUnavailableError as exc:
                _emit_store_fallback(app, str(exc))
                repository = InMemoryMemoryRepository()
                app.state.store = "memory-degraded"
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
            repository = InMemoryMemoryRepository()
            app.state.store = "memory"
            logger.info(
                "started in-memory store",
                extra={"event": "store.started", "store": "memory"},
            )
        app.state.repository = repository
        app.state.service = MemoryService(repository)
        try:
            yield
        finally:
            if isinstance(repository, PostgresMemoryRepository):
                repository.close()

    app = FastAPI(title="EvoEventMem", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.metrics = MetricsRegistry()
    app.state.store = "memory"
    app.state.repository = InMemoryMemoryRepository()
    app.state.service = None
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

    app.middleware("http")(_request_id_middleware)
    app.middleware("http")(_metrics_middleware)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readiness")
    def readiness(request: Request) -> dict[str, Any]:
        store = request.app.state.store
        if store == "postgres":
            repository = request.app.state.repository
            if isinstance(repository, PostgresMemoryRepository) and repository.ping():
                return {"status": "ready", "store": "postgres"}
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "degraded",
                    "store": "postgres",
                    "reason": "database unreachable",
                },
            )
        if store == "memory-degraded":
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "degraded",
                    "store": "memory-degraded",
                    "reason": "configured postgres store unavailable; serving from memory",
                },
            )
        return {"status": "ready", "store": "memory"}

    @app.get("/metrics")
    def metrics_endpoint(request: Request) -> Response:
        return Response(
            content=request.app.state.metrics.render_text(),
            media_type="text/plain; version=0.0.4",
        )

    @app.post("/v1/memories", response_model=_V1MemoryResponse)
    def write_memory(memory: MemoryRecord, request: Request) -> _V1MemoryResponse:
        try:
            written = _get_service(request.app).write(memory)
        except MemoryIdentityCollisionError as exc:
            logger.info(
                "memory write rejected due to memory_id collision",
                extra={"event": "memory.write_rejected", "memory_id": str(memory.memory_id)},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info(
            "memory written",
            extra={"event": "memory.written", "memory_id": str(written.memory_id)},
        )
        return _V1MemoryResponse.from_domain(written)

    @app.get("/v1/memories/search", response_model=list[_V1MemorySearchHitResponse])
    def search_memories(
        request: Request,
        user_id: str,
        q: str = Query(min_length=1),
        limit: int = Query(default=5, ge=1, le=50),
        tenant_id: str | None = None,
    ) -> list[_V1MemorySearchHitResponse]:
        try:
            hits = _get_service(request.app).search(
                user_id=user_id,
                query=q,
                limit=limit,
                tenant_id=tenant_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [_V1MemorySearchHitResponse.from_domain(hit) for hit in hits]

    @app.get("/v1/memories/{memory_id}/explain", response_model=_V1ExplainResponse)
    def explain_memory(
        request: Request,
        memory_id: UUID,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> _V1ExplainResponse:
        result = _get_service(request.app).explain(
            memory_id, user_id=user_id, tenant_id=tenant_id
        )
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
    def feedback_memory(
        request: Request,
        memory_id: UUID,
        payload: _V1FeedbackRequest,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> _V1MemoryResponse:
        updated = _get_service(request.app).feedback(
            memory_id,
            outcome=payload.outcome,
            rating=payload.rating,
            user_id=user_id,
            tenant_id=tenant_id,
            request_id=request_id_var.get(),
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail="memory not found or out of scope",
            )
        return _V1MemoryResponse.from_domain(updated)

    @app.post("/v1/memories/{memory_id}/forget", response_model=_V1MemoryResponse)
    def forget_memory(
        request: Request,
        memory_id: UUID,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> _V1MemoryResponse:
        updated = _get_service(request.app).forget(
            memory_id,
            user_id=user_id,
            tenant_id=tenant_id,
            request_id=request_id_var.get(),
        )
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
