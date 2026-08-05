from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError
from pydantic_core import to_jsonable_python

from evoeventmem.core.ports import MemoryRepository
from evoeventmem.domain.models import (
    EvidenceRef,
    MemoryRecord,
    MemorySearchHit,
    MemoryStatus,
    normalize_memory_content,
)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_STORAGE_FAILURE_REASON = "memory storage transaction failed"


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


class MemoryWriteDecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class MemoryWriteFailureCategory(StrEnum):
    INVALID_CANDIDATE = "invalid_candidate"
    MISSING_EVIDENCE = "missing_evidence"
    MEMORY_ID_COLLISION = "memory_id_collision"
    REQUEST_VALIDATION_FAILED = "request_validation_failed"
    STORAGE_FAILED = "storage_failed"


class MemoryIdentityCollisionError(ValueError):
    def __init__(self) -> None:
        super().__init__(MemoryWriteFailureCategory.MEMORY_ID_COLLISION.value)


class RawObservationLink(BaseModel):
    source_type: str = Field(default="observation", min_length=1)
    source_id: str = Field(min_length=1)
    locator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    memory: MemoryRecord
    extractor_version: str = Field(min_length=1)
    raw_observations: list[RawObservationLink] = Field(default_factory=list)
    raw_output: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_extracted_event(
        cls,
        candidate: Any,
        *,
        raw_observations: Sequence[RawObservationLink] = (),
        raw_output: str | None = None,
    ) -> Self:
        return cls(
            memory=candidate.memory,
            extractor_version=str(candidate.prompt_version),
            raw_observations=list(raw_observations),
            raw_output=raw_output,
        )


class MemoryWriteRequest(BaseModel):
    schema_version: Literal["memory-write-request.v1"] = "memory-write-request.v1"
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    candidates: list[MemoryWriteCandidate] = Field(default_factory=list)
    raw_observations: list[RawObservationLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteDecision(BaseModel):
    request_id: str
    candidate_id: str
    status: MemoryWriteDecisionStatus
    reason: str
    idempotency_key: str | None = None
    memory_id: UUID | None = None
    failure_category: MemoryWriteFailureCategory | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    raw_observations: list[RawObservationLink] = Field(default_factory=list)
    candidate_snapshot: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteMetrics(BaseModel):
    requested_candidates: int
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    failure_categories: dict[str, int] = Field(default_factory=dict)


class MemoryWriteResult(BaseModel):
    request_id: str
    accepted_memories: list[MemoryRecord]
    decisions: list[MemoryWriteDecision]
    metrics: MemoryWriteMetrics


class MemoryFeedbackEvent(BaseModel):
    outcome: str = Field(min_length=1)
    rating: float | None = Field(default=None, ge=0.0, le=1.0)
    recorded_at: datetime
    request_id: str | None = None


class MemoryExplainResult(BaseModel):
    memory: MemoryRecord
    related: list[MemoryRecord]


class _PreparedWrite(BaseModel):
    candidate: MemoryWriteCandidate
    memory: MemoryRecord
    idempotency_key: str
    raw_observations: list[RawObservationLink]


class MemoryService:
    """Minimal vertical slice; later tasks replace the retrieval and consolidation logic."""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository
        self._write_decisions: list[MemoryWriteDecision] = []

    def write(self, memory: MemoryRecord) -> MemoryRecord:
        normalized = normalize_memory_content(memory.content)
        with self._repository.transaction() as transaction:
            existing_by_id = transaction.get(memory.memory_id)
            if existing_by_id is not None:
                if _legacy_write_identity(existing_by_id) != _legacy_write_identity(memory):
                    raise MemoryIdentityCollisionError
                return existing_by_id

            for existing in transaction.list_for_user(memory.user_id):
                if existing.tenant_id != memory.tenant_id:
                    continue
                if normalize_memory_content(existing.content) == normalized:
                    return existing
            return transaction.add(memory)

    def write_extracted_events(self, request: MemoryWriteRequest) -> MemoryWriteResult:
        validation_decisions: list[MemoryWriteDecision] = []
        prepared_candidates: list[_PreparedWrite] = []

        for candidate in request.candidates:
            prepared = self._prepare_write_candidate(request, candidate)
            if isinstance(prepared, MemoryWriteDecision):
                validation_decisions.append(prepared)
                continue
            prepared_candidates.append(prepared)

        if validation_decisions:
            validation_decisions.extend(
                MemoryWriteDecision(
                    request_id=request.request_id,
                    candidate_id=prepared.candidate.candidate_id,
                    status=MemoryWriteDecisionStatus.REJECTED,
                    reason="request contains rejected candidates; no durable memories written",
                    idempotency_key=prepared.idempotency_key,
                    failure_category=MemoryWriteFailureCategory.REQUEST_VALIDATION_FAILED,
                    evidence_refs=list(prepared.memory.evidence_refs),
                    raw_observations=prepared.raw_observations,
                    candidate_snapshot=_candidate_snapshot(prepared.candidate),
                )
                for prepared in prepared_candidates
            )
            metrics = _build_write_metrics(len(request.candidates), validation_decisions)
            self._write_decisions.extend(validation_decisions)
            return MemoryWriteResult(
                request_id=request.request_id,
                accepted_memories=[],
                decisions=validation_decisions,
                metrics=metrics,
            )

        persistent_duplicate_decisions: list[MemoryWriteDecision] = []
        batch_duplicate_decisions: list[MemoryWriteDecision] = []
        accepted_pairs: list[tuple[_PreparedWrite, MemoryRecord]] = []
        collision_decisions: list[MemoryWriteDecision] = []

        try:
            with self._repository.transaction() as transaction:
                collision_indexes = _memory_id_collision_indexes(
                    transaction,
                    prepared_candidates,
                )
                if collision_indexes:
                    collision_decisions = _memory_id_collision_decisions(
                        request,
                        prepared_candidates,
                        collision_indexes,
                    )
                else:
                    pending_writes: list[_PreparedWrite] = []
                    seen_keys: dict[tuple[str | None, str, str], UUID] = {}

                    for prepared in prepared_candidates:
                        existing = self._find_by_idempotency_key(
                            transaction,
                            prepared.memory,
                            prepared.idempotency_key,
                        )
                        if existing is not None:
                            persistent_duplicate_decisions.append(
                                _duplicate_decision(request, prepared, existing.memory_id)
                            )
                            continue

                        scoped_key = (
                            prepared.memory.tenant_id,
                            prepared.memory.user_id,
                            prepared.idempotency_key,
                        )
                        duplicate_id = seen_keys.get(scoped_key)
                        if duplicate_id is not None:
                            batch_duplicate_decisions.append(
                                _duplicate_decision(request, prepared, duplicate_id)
                            )
                            continue

                        pending_writes.append(prepared)
                        seen_keys[scoped_key] = prepared.memory.memory_id

                    for prepared in pending_writes:
                        memory = transaction.add(prepared.memory)
                        accepted_pairs.append((prepared, memory))
        except Exception:
            decisions: list[MemoryWriteDecision] = []
            decisions.extend(
                MemoryWriteDecision(
                    request_id=request.request_id,
                    candidate_id=prepared.candidate.candidate_id,
                    status=MemoryWriteDecisionStatus.REJECTED,
                    reason=_STORAGE_FAILURE_REASON,
                    idempotency_key=prepared.idempotency_key,
                    failure_category=MemoryWriteFailureCategory.STORAGE_FAILED,
                    evidence_refs=list(prepared.memory.evidence_refs),
                    raw_observations=prepared.raw_observations,
                    candidate_snapshot=_candidate_snapshot(prepared.candidate),
                )
                for prepared in prepared_candidates
            )
            metrics = _build_write_metrics(len(request.candidates), decisions)
            self._write_decisions.extend(decisions)
            return MemoryWriteResult(
                request_id=request.request_id,
                accepted_memories=[],
                decisions=decisions,
                metrics=metrics,
            )

        if collision_decisions:
            metrics = _build_write_metrics(len(request.candidates), collision_decisions)
            self._write_decisions.extend(collision_decisions)
            return MemoryWriteResult(
                request_id=request.request_id,
                accepted_memories=[],
                decisions=collision_decisions,
                metrics=metrics,
            )

        decisions = [*persistent_duplicate_decisions, *batch_duplicate_decisions]
        accepted_memories: list[MemoryRecord] = []
        for prepared, memory in accepted_pairs:
            accepted_memories.append(memory)
            decisions.append(
                MemoryWriteDecision(
                    request_id=request.request_id,
                    candidate_id=prepared.candidate.candidate_id,
                    status=MemoryWriteDecisionStatus.ACCEPTED,
                    reason="candidate written as durable memory",
                    idempotency_key=prepared.idempotency_key,
                    memory_id=memory.memory_id,
                    evidence_refs=list(memory.evidence_refs),
                    raw_observations=prepared.raw_observations,
                    candidate_snapshot=_candidate_snapshot(prepared.candidate),
                )
            )

        metrics = _build_write_metrics(len(request.candidates), decisions)
        self._write_decisions.extend(decisions)
        return MemoryWriteResult(
            request_id=request.request_id,
            accepted_memories=accepted_memories,
            decisions=decisions,
            metrics=metrics,
        )

    def list_write_decisions(self, request_id: str | None = None) -> list[MemoryWriteDecision]:
        if request_id is None:
            return list(self._write_decisions)
        return [decision for decision in self._write_decisions if decision.request_id == request_id]

    def explain(
        self,
        memory_id: UUID,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> MemoryExplainResult | None:
        memory = self._repository.get(memory_id)
        if memory is None or not self._in_scope(memory, user_id, tenant_id):
            return None
        linked_ids = set(memory.supersedes)
        linked_ids.update(memory.derived_from)
        if memory.superseded_by is not None:
            linked_ids.add(memory.superseded_by)
        related = [
            item
            for item in self._repository.list_for_user(memory.user_id)
            if item.tenant_id == memory.tenant_id
            and item.status is not MemoryStatus.DELETED
            and item.memory_id in linked_ids
        ]
        return MemoryExplainResult(memory=memory, related=related)

    def feedback(
        self,
        memory_id: UUID,
        *,
        outcome: str,
        rating: float | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord | None:
        memory = self._repository.get(memory_id)
        if memory is None or not self._in_scope(memory, user_id, tenant_id):
            return None
        metadata = dict(memory.metadata)
        events = metadata.get("feedback_events")
        existing_events = [event for event in events if isinstance(event, dict)] if isinstance(
            events, list
        ) else []
        existing_events.append(
            MemoryFeedbackEvent(
                outcome=outcome,
                rating=rating,
                recorded_at=datetime.now(UTC),
                request_id=request_id,
            ).model_dump(mode="json")
        )
        updated = memory.model_copy(
            update={
                "metadata": {**metadata, "feedback_events": existing_events},
                "updated_at": datetime.now(UTC),
            }
        )
        return self._repository.update(updated)

    def forget(
        self,
        memory_id: UUID,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord | None:
        memory = self._repository.get(memory_id)
        if memory is None or not self._in_scope(memory, user_id, tenant_id):
            return None
        metadata = dict(memory.metadata)
        metadata["forgotten_at"] = datetime.now(UTC).isoformat()
        if request_id is not None:
            metadata["forget_request_id"] = request_id
        updated = memory.model_copy(
            update={
                "status": MemoryStatus.DELETED,
                "metadata": metadata,
                "updated_at": datetime.now(UTC),
            }
        )
        return self._repository.update(updated)

    def search(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        *,
        tenant_id: str | None = None,
    ) -> list[MemorySearchHit]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query_tokens = _tokens(query)
        hits: list[MemorySearchHit] = []
        for memory in self._repository.list_for_user(user_id):
            if memory.status is MemoryStatus.DELETED:
                continue
            if memory.tenant_id != tenant_id:
                continue
            entity_text = " ".join(entity.name for entity in memory.entities)
            memory_tokens = _tokens(memory.content + " " + entity_text)
            union = query_tokens | memory_tokens
            score = len(query_tokens & memory_tokens) / len(union) if union else 0.0
            if score > 0:
                hits.append(
                    MemorySearchHit(
                        memory=memory,
                        score=score,
                        reason="starter token-overlap baseline",
                    )
                )
        return sorted(hits, key=lambda hit: (-hit.score, str(hit.memory.memory_id)))[:limit]

    def _in_scope(
        self,
        memory: MemoryRecord,
        user_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        return (user_id is None or memory.user_id == user_id) and (
            tenant_id is None or memory.tenant_id == tenant_id
        )

    def _prepare_write_candidate(
        self,
        request: MemoryWriteRequest,
        candidate: MemoryWriteCandidate,
    ) -> _PreparedWrite | MemoryWriteDecision:
        raw_observations = list(candidate.raw_observations or request.raw_observations)
        try:
            validated = MemoryWriteCandidate.model_validate(candidate.model_dump(mode="python"))
        except ValidationError as exc:
            return MemoryWriteDecision(
                request_id=request.request_id,
                candidate_id=candidate.candidate_id,
                status=MemoryWriteDecisionStatus.REJECTED,
                reason=str(exc),
                failure_category=_failure_category(candidate),
                raw_observations=raw_observations,
                candidate_snapshot=_candidate_snapshot(candidate),
            )

        idempotency_key = _idempotency_key(
            validated.memory,
            validated.extractor_version,
        )
        memory = _with_write_metadata(
            validated.memory,
            request_id=request.request_id,
            candidate_id=validated.candidate_id,
            extractor_version=validated.extractor_version,
            idempotency_key=idempotency_key,
            raw_observations=raw_observations,
        )
        return _PreparedWrite(
            candidate=validated,
            memory=memory,
            idempotency_key=idempotency_key,
            raw_observations=raw_observations,
        )

    def _find_by_idempotency_key(
        self,
        repository: MemoryRepository,
        memory: MemoryRecord,
        idempotency_key: str,
    ) -> MemoryRecord | None:
        for existing in repository.list_for_user(memory.user_id):
            if existing.tenant_id != memory.tenant_id:
                continue
            if _memory_idempotency_key(existing) == idempotency_key:
                return existing
        return None


def _legacy_write_identity(memory: MemoryRecord) -> tuple[str | None, str, str]:
    return (
        memory.tenant_id,
        memory.user_id,
        normalize_memory_content(memory.content),
    )


def _memory_id_collision_indexes(
    repository: MemoryRepository,
    prepared_candidates: Sequence[_PreparedWrite],
) -> set[int]:
    collision_indexes: set[int] = set()
    batch_identities: dict[UUID, list[tuple[int, tuple[str | None, str, str]]]] = {}

    for index, prepared in enumerate(prepared_candidates):
        identity = (
            prepared.memory.tenant_id,
            prepared.memory.user_id,
            prepared.idempotency_key,
        )
        batch_identities.setdefault(prepared.memory.memory_id, []).append((index, identity))

        existing = repository.get(prepared.memory.memory_id)
        if existing is None:
            continue
        existing_identity = (
            existing.tenant_id,
            existing.user_id,
            _memory_idempotency_key(existing),
        )
        if existing_identity != identity:
            collision_indexes.add(index)

    for identities in batch_identities.values():
        if len({identity for _, identity in identities}) > 1:
            collision_indexes.update(index for index, _ in identities)

    return collision_indexes


def _memory_id_collision_decisions(
    request: MemoryWriteRequest,
    prepared_candidates: Sequence[_PreparedWrite],
    collision_indexes: set[int],
) -> list[MemoryWriteDecision]:
    decisions: list[MemoryWriteDecision] = []
    for index, prepared in enumerate(prepared_candidates):
        collision = index in collision_indexes
        decisions.append(
            MemoryWriteDecision(
                request_id=request.request_id,
                candidate_id=prepared.candidate.candidate_id,
                status=MemoryWriteDecisionStatus.REJECTED,
                reason=(
                    "memory_id belongs to a different durable or request identity"
                    if collision
                    else "request contains memory_id collisions; no durable memories written"
                ),
                idempotency_key=prepared.idempotency_key,
                failure_category=(
                    MemoryWriteFailureCategory.MEMORY_ID_COLLISION
                    if collision
                    else MemoryWriteFailureCategory.REQUEST_VALIDATION_FAILED
                ),
                evidence_refs=list(prepared.memory.evidence_refs),
                raw_observations=prepared.raw_observations,
                candidate_snapshot=_candidate_snapshot(prepared.candidate),
            )
        )
    return decisions


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_json_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, set | frozenset):
        return sorted(
            (_canonical_json_value(item) for item in value),
            key=_canonical_sort_key,
        )
    if isinstance(value, list | tuple):
        return [_canonical_json_value(item) for item in value]
    return to_jsonable_python(value)


def _canonical_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _candidate_identity(memory: MemoryRecord) -> dict[str, Any]:
    temporal = memory.model_dump(
        mode="json",
        include={"event_time", "valid_from", "valid_to"},
    )
    return {
        "memory_kind": memory.memory_kind.value,
        "normalized_content": normalize_memory_content(memory.content),
        "event_time": temporal["event_time"],
        "valid_from": temporal["valid_from"],
        "valid_to": temporal["valid_to"],
        "entities": sorted(
            (
                {
                    "entity_id": entity.entity_id,
                    "name": normalize_memory_content(entity.name),
                    "kind": entity.kind,
                    "role": entity.role,
                }
                for entity in memory.entities
            ),
            key=_canonical_sort_key,
        ),
        "roles": sorted(
            (
                [role_key, role_value]
                for role_key, role_value in memory.roles.items()
            ),
            key=_canonical_sort_key,
        ),
        "relations": sorted(
            (
                relation.model_dump(mode="json")
                for relation in memory.relations
            ),
            key=_canonical_sort_key,
        ),
        "fact_metadata": {
            field: {
                "present": field in memory.metadata,
                "value": _canonical_json_value(memory.metadata.get(field)),
            }
            for field in ("fact_slot", "fact_value", "multi_valued")
        },
    }


def _duplicate_decision(
    request: MemoryWriteRequest,
    prepared: _PreparedWrite,
    memory_id: UUID,
) -> MemoryWriteDecision:
    return MemoryWriteDecision(
        request_id=request.request_id,
        candidate_id=prepared.candidate.candidate_id,
        status=MemoryWriteDecisionStatus.DUPLICATE,
        reason="candidate has already been written with the same identity",
        idempotency_key=prepared.idempotency_key,
        memory_id=memory_id,
        evidence_refs=list(prepared.memory.evidence_refs),
        raw_observations=prepared.raw_observations,
        candidate_snapshot=_candidate_snapshot(prepared.candidate),
    )


def _idempotency_key(memory: MemoryRecord, extractor_version: str) -> str:
    evidence_payload = sorted(
        (
            {
                "source_type": ref.source_type,
                "source_id": ref.source_id,
                "locator": ref.locator,
                "quote": ref.quote,
                "metadata": _canonical_json_value(ref.metadata),
            }
            for ref in memory.evidence_refs
        ),
        key=_canonical_sort_key,
    )
    payload = {
        "extractor_version": extractor_version,
        "evidence_refs": evidence_payload,
        "candidate_identity": _candidate_identity(memory),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"memory-write.v1:{digest}"


def _with_write_metadata(
    memory: MemoryRecord,
    *,
    request_id: str,
    candidate_id: str,
    extractor_version: str,
    idempotency_key: str,
    raw_observations: Sequence[RawObservationLink],
) -> MemoryRecord:
    metadata = dict(memory.metadata)
    raw_observation_payload = [
        observation.model_dump(mode="json", exclude_none=True) for observation in raw_observations
    ]
    metadata["write_idempotency_key"] = idempotency_key
    metadata["source_observations"] = raw_observation_payload
    metadata["write_pipeline"] = {
        "request_id": request_id,
        "candidate_id": candidate_id,
        "extractor_version": extractor_version,
        "idempotency_key": idempotency_key,
        "raw_observations": raw_observation_payload,
    }
    return memory.model_copy(update={"metadata": metadata})


def _memory_idempotency_key(memory: MemoryRecord) -> str | None:
    key = memory.metadata.get("write_idempotency_key")
    if isinstance(key, str):
        return key
    pipeline = memory.metadata.get("write_pipeline")
    if isinstance(pipeline, dict):
        pipeline_key = pipeline.get("idempotency_key")
        if isinstance(pipeline_key, str):
            return pipeline_key
    return None


def _candidate_snapshot(candidate: MemoryWriteCandidate) -> dict[str, Any]:
    return candidate.model_dump(mode="json")


def _failure_category(candidate: MemoryWriteCandidate) -> MemoryWriteFailureCategory:
    if not candidate.memory.evidence_refs:
        return MemoryWriteFailureCategory.MISSING_EVIDENCE
    return MemoryWriteFailureCategory.INVALID_CANDIDATE


def _build_write_metrics(
    requested_candidates: int,
    decisions: Sequence[MemoryWriteDecision],
) -> MemoryWriteMetrics:
    metrics = MemoryWriteMetrics(requested_candidates=requested_candidates)
    for decision in decisions:
        if decision.status is MemoryWriteDecisionStatus.ACCEPTED:
            metrics.accepted += 1
        elif decision.status is MemoryWriteDecisionStatus.DUPLICATE:
            metrics.duplicates += 1
        elif decision.status is MemoryWriteDecisionStatus.REJECTED:
            metrics.rejected += 1
            if decision.failure_category is not None:
                key = decision.failure_category.value
                metrics.failure_categories[key] = metrics.failure_categories.get(key, 0) + 1
    return metrics
