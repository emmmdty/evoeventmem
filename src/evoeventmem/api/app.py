from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from evoeventmem.domain.models import MemoryRecord, MemorySearchHit
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.services.memory_service import MemoryService

app = FastAPI(title="EvoEventMem", version="0.1.0")
_repository = InMemoryMemoryRepository()
_service = MemoryService(_repository)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/memories", response_model=MemoryRecord)
def write_memory(memory: MemoryRecord) -> MemoryRecord:
    return _service.write(memory)


@app.get("/v1/memories/search", response_model=list[MemorySearchHit])
def search_memories(
    user_id: str,
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=50),
) -> list[MemorySearchHit]:
    try:
        return _service.search(user_id=user_id, query=q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
