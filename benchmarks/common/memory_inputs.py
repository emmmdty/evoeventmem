"""Raw-turn memory construction and a single cached extraction snapshot.

Separates three operations:

- ``build_raw_turn_corpus``: one normalized memory chunk per eligible raw turn
  with exact full-turn evidence carrying the original raw turn ID. Used by the
  ``vector_rag`` baseline only; chunks never contain event summaries,
  observations, answers, or QA evidence targets.
- ``extract_event_snapshot``: one extraction invocation per conversation used
  by every event-memory method. Target/answer fields are cleared before
  extraction; every accepted event requires an exact raw-turn span with the
  original raw turn ID. Unsupported spans produce a cached rejection record,
  never silent summary provenance.
- ``materialize_event_store``: build a ``MemoryRepository`` from a snapshot with
  optional ETEC consolidation.

The extraction snapshot is shared, never handed to ``vector_rag``.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from benchmarks.common.artifacts import (
    ExtractionRejection,
    ExtractionSnapshot,
    ProviderIdentity,
    canonical_json_hash,
)
from benchmarks.common.normalization import NormalizedRecord, NormalizedSession, NormalizedTurn
from evoeventmem.consolidation import ETECConsolidator
from evoeventmem.core.ports import EmbeddingModel, MemoryRepository
from evoeventmem.domain.models import EvidenceRef, MemoryRecord
from evoeventmem.extraction import ExtractionInput
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.services.memory_service import (
    MemoryService,
    MemoryWriteCandidate,
    MemoryWriteRequest,
)

MEMORY_INPUTS_SCHEMA_VERSION = 1


class RawTurnChunk(BaseModel):
    schema_version: int = MEMORY_INPUTS_SCHEMA_VERSION
    raw_turn_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    speaker: str = Field(min_length=1)
    content: str = Field(min_length=1)
    timestamp: datetime | None = None
    evidence_ref: EvidenceRef


class RawTurnCorpus(BaseModel):
    schema_version: int = MEMORY_INPUTS_SCHEMA_VERSION
    conversation_id: str = Field(min_length=1)
    chunks: list[RawTurnChunk] = Field(default_factory=list)

    def chunk_count(self) -> int:
        return len(self.chunks)


def build_raw_turn_corpus(record: NormalizedRecord) -> RawTurnCorpus:
    """Index normalized raw turns only; never summaries, observations, or answers."""
    chunks: list[RawTurnChunk] = []
    for session in record.sessions:
        for turn in session.turns:
            if not turn.content.strip():
                continue
            evidence_ref = EvidenceRef(
                source_type="turn",
                source_id=_evidence_scope(record, session, turn),
                locator=f"chars=0:{len(turn.content)}",
                quote=turn.content,
                metadata={
                    "dataset": record.dataset,
                    "sample_id": record.sample_id,
                    "session_id": session.session_id,
                    "raw_turn_id": turn.turn_id,
                    "speaker": turn.speaker,
                },
            )
            chunks.append(
                RawTurnChunk(
                    raw_turn_id=turn.turn_id,
                    session_id=session.session_id,
                    speaker=turn.speaker,
                    content=turn.content,
                    timestamp=turn.timestamp,
                    evidence_ref=evidence_ref,
                )
            )
    return RawTurnCorpus(conversation_id=record.sample_id, chunks=chunks)


def extract_event_snapshot(
    record: NormalizedRecord,
    extractor: Any,
    *,
    user_id: str,
    extractor_identity: ProviderIdentity,
) -> ExtractionSnapshot:
    """Extract events once from target-cleared raw turns.

    The event-memory extraction input is built from normalized raw turns after
    all target/answer fields are cleared. Official event summaries are kept out
    of the extraction input entirely (they are evaluation targets only).
    Every accepted event must reference an exact raw-turn span.
    """
    request = ExtractionInput.from_normalized_record(record, user_id=user_id)
    request = request.clear_target_fields()
    request = request.model_copy(update={"event_summaries": [], "require_turn_evidence": True})

    result = extractor.extract(request)
    events: list[MemoryRecord] = []
    rejections: list[ExtractionRejection] = []
    for candidate in result.candidates:
        turn_refs = [
            ref for ref in candidate.memory.evidence_refs if ref.source_type == "turn"
        ]
        if not turn_refs:
            raw_turn_id = _first_raw_turn_id(candidate.memory)
            rejections.append(
                ExtractionRejection(
                    raw_turn_id=raw_turn_id or candidate.memory.content,
                    reason="missing_raw_turn_evidence",
                    span=None,
                )
            )
            continue
        events.append(candidate.memory)

    snapshot = ExtractionSnapshot(
        snapshot_id=canonical_json_hash(
            {
                "conversation_id": record.sample_id,
                "extractor": extractor_identity.model_dump(),
                "raw_turn_count": sum(
                    len(session.turns) for session in record.sessions
                ),
            }
        ),
        conversation_id=record.sample_id,
        extractor=extractor_identity,
        raw_turn_count=sum(len(session.turns) for session in record.sessions),
        event_count=len(events),
        events=events,
        rejections=rejections,
        created_at=datetime.now(UTC),
    )
    return snapshot


def materialize_event_store(
    snapshot: ExtractionSnapshot,
    *,
    apply_etec: bool,
    embedding_model: EmbeddingModel | None = None,
    user_id: str,
) -> tuple[MemoryRepository, dict[str, Any]]:
    """Build a MemoryRepository from a shared extraction snapshot."""
    repository = InMemoryMemoryRepository()
    candidates = sorted(
        snapshot.events,
        key=lambda memory: (memory.event_time or datetime.min.replace(tzinfo=UTC), memory.content),
    )
    if apply_etec:
        if embedding_model is None:
            raise ValueError("apply_etec requires an embedding_model")
        consolidator = ETECConsolidator(embedding_model)
        actions: Counter[str] = Counter()
        for memory in candidates:
            applied = consolidator.apply(repository, memory)
            actions[applied.decision.action.value] += 1
        return repository, {
            "apply_etec": True,
            "candidate_count": len(candidates),
            "memory_count": len(repository.list_for_user(user_id)),
            "actions": dict(actions),
        }

    write_request = MemoryWriteRequest(
        candidates=[
            MemoryWriteCandidate.from_extracted_event(_Candidate(memory))
            for memory in candidates
        ]
    )
    write_result = MemoryService(repository).write_extracted_events(write_request)
    return repository, {
        "apply_etec": False,
        "candidate_count": len(candidates),
        "memory_count": len(repository.list_for_user(user_id)),
        "write_metrics": write_result.metrics.model_dump(mode="json"),
    }


class _Candidate:
    """Adapter so MemoryWriteCandidate.from_extracted_event accepts MemoryRecord."""

    def __init__(self, memory: MemoryRecord) -> None:
        self.memory = memory
        self.prompt_version = "rule.v1"


def _evidence_scope(
    record: NormalizedRecord, session: NormalizedSession, turn: NormalizedTurn
) -> str:
    from urllib.parse import quote

    return "/".join(
        f"{quote(key, safe='')}={quote(value, safe='')}"
        for key, value in (
            ("dataset", record.dataset),
            ("sample", record.sample_id),
            ("session", session.session_id),
            ("turn", turn.turn_id),
        )
    )


def _first_raw_turn_id(memory: MemoryRecord) -> str | None:
    for ref in memory.evidence_refs:
        raw_id = ref.metadata.get("raw_turn_id")
        if raw_id is not None:
            return str(raw_id)
    return None


__all__ = [
    "MEMORY_INPUTS_SCHEMA_VERSION",
    "RawTurnChunk",
    "RawTurnCorpus",
    "build_raw_turn_corpus",
    "extract_event_snapshot",
    "materialize_event_store",
]