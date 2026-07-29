from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from time import perf_counter
from uuid import UUID

from pydantic import BaseModel, Field

from evoeventmem.core.ports import EmbeddingModel
from evoeventmem.domain.models import EntityRef, MemoryKind, MemoryRecord

_KEY_TOKEN_RE = re.compile(r"[a-z0-9]+")


class LinkCandidateKind(StrEnum):
    ENTITY = "entity"
    EVENT = "event"


class CandidateGenerationRequest(BaseModel):
    source: MemoryRecord
    existing: list[MemoryRecord] = Field(default_factory=list)
    max_entity_candidates: int = Field(default=10, ge=1)
    max_event_candidates: int = Field(default=10, ge=1)
    event_time_window_days: int = Field(default=30, ge=0)
    min_embedding_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)


class LinkCandidate(BaseModel):
    candidate_id: str
    candidate_kind: LinkCandidateKind
    policy_name: str
    source_memory: MemoryRecord
    target_memory: MemoryRecord
    source_entity: EntityRef | None = None
    target_entity: EntityRef | None = None
    score: float
    reasons: list[str] = Field(default_factory=list)


class CandidateGenerationResult(BaseModel):
    entity_candidates: list[LinkCandidate]
    event_candidates: list[LinkCandidate]
    latency_ms: float
    embedding_model_id: str


class CandidateRecallMetrics(BaseModel):
    k: int = Field(ge=1)
    entity_recall_at_k: float
    event_recall_at_k: float
    generated_entity_candidates: int
    generated_event_candidates: int
    latency_ms: float


class LinkCandidateGenerator:
    ENTITY_POLICY = "entity-normalized-alias-embedding.v1"
    EVENT_POLICY = "event-time-window-embedding.v1"

    def __init__(self, embedding_model: EmbeddingModel) -> None:
        self._embedding_model = embedding_model

    def generate(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        started = perf_counter()
        entity_candidates = self._generate_entity_candidates(request)
        event_candidates = self._generate_event_candidates(request)
        latency_ms = (perf_counter() - started) * 1000.0
        return CandidateGenerationResult(
            entity_candidates=entity_candidates,
            event_candidates=event_candidates,
            latency_ms=latency_ms,
            embedding_model_id=self._embedding_model.model_id,
        )

    def _generate_entity_candidates(
        self,
        request: CandidateGenerationRequest,
    ) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        for source_entity in request.source.entities:
            source_keys = _entity_keys(request.source, source_entity)
            source_embedding = self._embed(source_entity.name)
            for target in _eligible_existing(request.source, request.existing):
                for target_entity in target.entities:
                    target_keys = _entity_keys(target, target_entity)
                    similarity = _cosine(source_embedding, self._embed(target_entity.name))
                    reasons = _entity_reasons(
                        source_entity,
                        target_entity,
                        source_keys,
                        target_keys,
                        similarity,
                        request.min_embedding_similarity,
                    )
                    if not reasons:
                        continue
                    score = _entity_score(reasons, similarity)
                    candidates.append(
                        LinkCandidate(
                            candidate_id=_candidate_id(
                                self.ENTITY_POLICY,
                                LinkCandidateKind.ENTITY,
                                request.source,
                                target,
                                source_entity,
                                target_entity,
                            ),
                            candidate_kind=LinkCandidateKind.ENTITY,
                            policy_name=self.ENTITY_POLICY,
                            source_memory=request.source,
                            target_memory=target,
                            source_entity=source_entity,
                            target_entity=target_entity,
                            score=score,
                            reasons=reasons,
                        )
                    )
        return _bounded(candidates, request.max_entity_candidates)

    def _generate_event_candidates(
        self,
        request: CandidateGenerationRequest,
    ) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        source_embedding = self._embed(request.source.content)
        for target in _eligible_existing(request.source, request.existing):
            if target.memory_kind is not MemoryKind.EVENT:
                continue
            time_delta_days = _time_delta_days(request.source, target)
            if time_delta_days is not None and time_delta_days > request.event_time_window_days:
                continue
            similarity = _cosine(source_embedding, self._embed(target.content))
            if similarity < request.min_embedding_similarity:
                continue
            reasons = ["embedding_candidate"]
            if time_delta_days is None:
                reasons.append("time_unbounded")
            else:
                reasons.append("within_time_window")
            if _entity_key_sets(request.source) & _entity_key_sets(target):
                reasons.append("shared_entity_key")
            score = similarity + (0.2 if "shared_entity_key" in reasons else 0.0)
            candidates.append(
                LinkCandidate(
                    candidate_id=_candidate_id(
                        self.EVENT_POLICY,
                        LinkCandidateKind.EVENT,
                        request.source,
                        target,
                    ),
                    candidate_kind=LinkCandidateKind.EVENT,
                    policy_name=self.EVENT_POLICY,
                    source_memory=request.source,
                    target_memory=target,
                    score=score,
                    reasons=reasons,
                )
            )
        return _bounded(candidates, request.max_event_candidates)

    def _embed(self, text: str) -> tuple[float, ...]:
        return self._embedding_model.embed_texts([text])[0].vector


def calculate_candidate_recall(
    result: CandidateGenerationResult,
    *,
    gold_entity_target_memory_ids: set[UUID],
    gold_event_target_memory_ids: set[UUID],
    k: int,
) -> CandidateRecallMetrics:
    if k < 1:
        raise ValueError("k must be at least 1")
    entity_hits = _top_k_target_ids(result.entity_candidates, k) & gold_entity_target_memory_ids
    event_hits = _top_k_target_ids(result.event_candidates, k) & gold_event_target_memory_ids
    return CandidateRecallMetrics(
        k=k,
        entity_recall_at_k=_recall(entity_hits, gold_entity_target_memory_ids),
        event_recall_at_k=_recall(event_hits, gold_event_target_memory_ids),
        generated_entity_candidates=len(result.entity_candidates),
        generated_event_candidates=len(result.event_candidates),
        latency_ms=result.latency_ms,
    )


def normalized_linking_key(value: str) -> str:
    return " ".join(_KEY_TOKEN_RE.findall(value.casefold()))


def _eligible_existing(
    source: MemoryRecord,
    existing: Iterable[MemoryRecord],
) -> Iterable[MemoryRecord]:
    for target in existing:
        if target.memory_id == source.memory_id:
            continue
        if target.user_id != source.user_id:
            continue
        if target.tenant_id != source.tenant_id:
            continue
        yield target


def _entity_reasons(
    source_entity: EntityRef,
    target_entity: EntityRef,
    source_keys: set[str],
    target_keys: set[str],
    similarity: float,
    min_embedding_similarity: float,
) -> list[str]:
    reasons: list[str] = []
    source_name_key = normalized_linking_key(source_entity.name)
    target_name_key = normalized_linking_key(target_entity.name)
    if source_name_key == target_name_key:
        reasons.append("exact_normalized_entity_key")
    elif source_keys & target_keys:
        reasons.append("alias_match")
    if similarity >= min_embedding_similarity:
        reasons.append("embedding_candidate")
    return reasons


def _entity_score(reasons: Sequence[str], similarity: float) -> float:
    score = similarity
    if "exact_normalized_entity_key" in reasons:
        score += 0.3
    if "alias_match" in reasons:
        score += 0.4
    return score


def _entity_keys(memory: MemoryRecord, entity: EntityRef) -> set[str]:
    keys = {normalized_linking_key(entity.name)}
    aliases = memory.metadata.get("entity_aliases", {})
    if not isinstance(aliases, dict):
        return keys
    entity_key = normalized_linking_key(entity.name)
    for raw_name, raw_aliases in aliases.items():
        if normalized_linking_key(str(raw_name)) != entity_key:
            continue
        keys.update(
            normalized_linking_key(str(alias))
            for alias in _iter_alias_values(raw_aliases)
            if normalized_linking_key(str(alias))
        )
    return keys


def _iter_alias_values(value: object) -> Iterable[object]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Sequence):
        yield from value


def _entity_key_sets(memory: MemoryRecord) -> set[str]:
    keys: set[str] = set()
    for entity in memory.entities:
        keys.update(_entity_keys(memory, entity))
    return keys


def _time_delta_days(source: MemoryRecord, target: MemoryRecord) -> int | None:
    source_time = _anchor_time(source)
    target_time = _anchor_time(target)
    if source_time is None or target_time is None:
        return None
    return abs((source_time - target_time).days)


def _anchor_time(memory: MemoryRecord) -> datetime | None:
    return memory.event_time or memory.valid_from


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )
    return dot_product / (left_norm * right_norm)


def _bounded(candidates: list[LinkCandidate], limit: int) -> list[LinkCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            str(candidate.target_memory.memory_id),
            candidate.target_entity.name if candidate.target_entity else "",
            candidate.candidate_id,
        ),
    )[:limit]


def _candidate_id(
    policy_name: str,
    kind: LinkCandidateKind,
    source: MemoryRecord,
    target: MemoryRecord,
    source_entity: EntityRef | None = None,
    target_entity: EntityRef | None = None,
) -> str:
    parts = [
        policy_name,
        kind.value,
        str(source.memory_id),
        source_entity.name if source_entity else "",
        str(target.memory_id),
        target_entity.name if target_entity else "",
    ]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{kind.value}-candidate-{digest}"


def _top_k_target_ids(candidates: Sequence[LinkCandidate], k: int) -> set[UUID]:
    return {candidate.target_memory.memory_id for candidate in candidates[:k]}


def _recall(hits: set[UUID], gold: set[UUID]) -> float:
    if not gold:
        return 1.0
    return len(hits) / len(gold)


__all__ = [
    "CandidateGenerationRequest",
    "CandidateGenerationResult",
    "CandidateRecallMetrics",
    "LinkCandidate",
    "LinkCandidateGenerator",
    "LinkCandidateKind",
    "calculate_candidate_recall",
    "normalized_linking_key",
]
