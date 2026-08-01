from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from evoeventmem.domain.models import EvidenceRef, MemoryKind, MemoryRecord, MemorySearchHit
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.services.memory_service import MemoryIdentityCollisionError, MemoryService


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


app = FastAPI(title="EvoEventMem", version="0.1.0")
_repository = InMemoryMemoryRepository()
_service = MemoryService(_repository)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/memories", response_model=_V1MemoryResponse)
def write_memory(memory: MemoryRecord) -> _V1MemoryResponse:
    try:
        return _V1MemoryResponse.from_domain(_service.write(memory))
    except MemoryIdentityCollisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/memories/search", response_model=list[_V1MemorySearchHitResponse])
def search_memories(
    user_id: str,
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=50),
    tenant_id: str | None = None,
) -> list[_V1MemorySearchHitResponse]:
    try:
        return [
            _V1MemorySearchHitResponse.from_domain(hit)
            for hit in _service.search(
                user_id=user_id,
                query=q,
                limit=limit,
                tenant_id=tenant_id,
            )
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
