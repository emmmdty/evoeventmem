from __future__ import annotations

import hashlib
import heapq
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from time import perf_counter
from uuid import UUID

from pydantic import BaseModel, Field

from evoeventmem.core.ports import EmbeddingModel
from evoeventmem.domain.models import EntityRef, MemoryKind, MemoryRecord, MemoryStatus

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
    entity_comparison_count: int = Field(default=0, ge=0)
    event_comparison_count: int = Field(default=0, ge=0)


class CandidateRecallMetrics(BaseModel):
    k: int = Field(ge=1)
    entity_recall_at_k: float
    event_recall_at_k: float
    generated_entity_candidates: int
    generated_event_candidates: int
    latency_ms: float
    entity_comparison_count: int = Field(default=0, ge=0)
    event_comparison_count: int = Field(default=0, ge=0)


@dataclass(frozen=True, slots=True)
class _EntityIndexEntry:
    memory: MemoryRecord
    entity: EntityRef
    name_key: str
    keys: frozenset[str]
    stable_key: tuple[str, ...]


@dataclass(slots=True)
class _RequestIndexes:
    entity_entries: list[_EntityIndexEntry] = field(default_factory=list)
    entity_name_index: dict[str, list[_EntityIndexEntry]] = field(default_factory=dict)
    entity_key_index: dict[str, list[_EntityIndexEntry]] = field(default_factory=dict)
    entity_token_index: dict[str, list[_EntityIndexEntry]] = field(default_factory=dict)
    event_targets: list[MemoryRecord] = field(default_factory=list)
    event_content_index: dict[str, list[MemoryRecord]] = field(default_factory=dict)
    event_token_index: dict[str, list[MemoryRecord]] = field(default_factory=dict)
    event_entity_key_index: dict[str, list[MemoryRecord]] = field(default_factory=dict)
    fact_slot_index: dict[str, list[MemoryRecord]] = field(default_factory=dict)


@dataclass(slots=True)
class _EntityPoolItem:
    source_position: int
    source_entity: EntityRef
    target: _EntityIndexEntry
    reasons: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _EventPoolItem:
    target: MemoryRecord
    reasons: set[str] = field(default_factory=set)


class LinkCandidateGenerator:
    ENTITY_POLICY = "entity-normalized-alias-embedding.v1"
    EVENT_POLICY = "event-time-window-embedding.v1"

    def __init__(self, embedding_model: EmbeddingModel) -> None:
        self._embedding_model = embedding_model

    def generate(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        started = perf_counter()
        indexes = _build_request_indexes(request)
        entity_pool = _entity_comparison_pool(request, indexes)
        event_pool = _event_comparison_pool(request, indexes)
        embeddings = self._embed_unique(
            [
                text
                for item in entity_pool
                for text in (item.source_entity.name, item.target.entity.name)
            ]
            + [
                text
                for item in event_pool
                for text in (request.source.content, item.target.content)
            ]
        )
        entity_candidates = _score_entity_pool(request, entity_pool, embeddings)
        event_candidates = _score_event_pool(request, event_pool, embeddings)
        latency_ms = (perf_counter() - started) * 1000.0
        return CandidateGenerationResult(
            entity_candidates=entity_candidates,
            event_candidates=event_candidates,
            latency_ms=latency_ms,
            embedding_model_id=self._embedding_model.model_id,
            entity_comparison_count=len(entity_pool),
            event_comparison_count=len(event_pool),
        )

    def _embed_unique(self, texts: Sequence[str]) -> dict[str, tuple[float, ...]]:
        unique_texts = list(dict.fromkeys(texts))
        if not unique_texts:
            return {}
        responses = self._embedding_model.embed_texts(unique_texts)
        return {
            text: response.vector
            for text, response in zip(unique_texts, responses, strict=True)
        }


_ENTITY_REASON_ORDER = (
    "exact_normalized_entity_key",
    "alias_match",
    "lexical_token_match",
    "stable_fallback",
    "embedding_candidate",
)
_EVENT_REASON_ORDER = (
    "fact_slot_match",
    "exact_normalized_content",
    "shared_entity_key",
    "lexical_token_match",
    "stable_fallback",
    "embedding_candidate",
    "within_time_window",
    "time_unbounded",
)


def _build_request_indexes(request: CandidateGenerationRequest) -> _RequestIndexes:
    indexes = _RequestIndexes()
    seen_memory_ids: set[UUID] = set()
    for target in _eligible_existing(request.source, request.existing):
        if target.memory_id in seen_memory_ids:
            continue
        seen_memory_ids.add(target.memory_id)

        memory_entity_keys: set[str] = set()
        for position, entity in enumerate(target.entities):
            name_key = normalized_linking_key(entity.name)
            keys = frozenset(_entity_keys(target, entity))
            entry = _EntityIndexEntry(
                memory=target,
                entity=entity,
                name_key=name_key,
                keys=keys,
                stable_key=_entity_target_identity(target, entity, position),
            )
            indexes.entity_entries.append(entry)
            indexes.entity_name_index.setdefault(name_key, []).append(entry)
            for key in keys:
                indexes.entity_key_index.setdefault(key, []).append(entry)
                memory_entity_keys.add(key)
                for token in _linking_tokens(key):
                    indexes.entity_token_index.setdefault(token, []).append(entry)

        time_delta_days = _time_delta_days(request.source, target)
        time_eligible_event = target.memory_kind is MemoryKind.EVENT and (
            time_delta_days is None or time_delta_days <= request.event_time_window_days
        )
        if time_eligible_event:
            indexes.event_targets.append(target)
            content_key = normalized_linking_key(target.content)
            indexes.event_content_index.setdefault(content_key, []).append(target)
            for token in _linking_tokens(content_key):
                indexes.event_token_index.setdefault(token, []).append(target)
            for key in memory_entity_keys:
                indexes.event_entity_key_index.setdefault(key, []).append(target)

        fact_slot = _fact_slot_key(target)
        if fact_slot and (target.memory_kind is not MemoryKind.EVENT or time_eligible_event):
            indexes.fact_slot_index.setdefault(fact_slot, []).append(target)
    return indexes


def _entity_comparison_pool(
    request: CandidateGenerationRequest,
    indexes: _RequestIndexes,
) -> list[_EntityPoolItem]:
    pool: dict[tuple[object, ...], _EntityPoolItem] = {}
    source_entities = sorted(
        enumerate(request.source.entities),
        key=lambda item: (*_entity_identity(item[1]), item[0]),
    )
    for source_position, source_entity in source_entities:
        source_name_key = normalized_linking_key(source_entity.name)
        source_keys = _entity_keys(request.source, source_entity)

        for entry in _take_entity_entries(
            indexes.entity_name_index.get(source_name_key, ()),
            request.max_entity_candidates,
        ):
            _add_entity_pool_item(
                pool,
                source_position,
                source_entity,
                entry,
                "exact_normalized_entity_key",
            )

        for source_key in sorted(source_keys):
            for entry in _take_entity_entries(
                indexes.entity_key_index.get(source_key, ()),
                request.max_entity_candidates,
            ):
                reason = (
                    "exact_normalized_entity_key"
                    if source_name_key == entry.name_key
                    else "alias_match"
                )
                _add_entity_pool_item(
                    pool,
                    source_position,
                    source_entity,
                    entry,
                    reason,
                )

        source_tokens = {
            token for key in source_keys for token in _linking_tokens(key)
        }
        for token in sorted(source_tokens):
            for entry in _take_entity_entries(
                indexes.entity_token_index.get(token, ()),
                request.max_entity_candidates,
            ):
                _add_entity_pool_item(
                    pool,
                    source_position,
                    source_entity,
                    entry,
                    "lexical_token_match",
                )

    if len(pool) < request.max_entity_candidates:
        for source_position, source_entity in source_entities:
            for entry in _take_entity_entries(
                indexes.entity_entries,
                request.max_entity_candidates,
            ):
                _add_entity_pool_item(
                    pool,
                    source_position,
                    source_entity,
                    entry,
                    "stable_fallback",
                )
                if len(pool) >= request.max_entity_candidates:
                    break
            if len(pool) >= request.max_entity_candidates:
                break

    return sorted(pool.values(), key=_entity_pool_sort_key)[
        : request.max_entity_candidates
    ]


def _event_comparison_pool(
    request: CandidateGenerationRequest,
    indexes: _RequestIndexes,
) -> list[_EventPoolItem]:
    pool: dict[UUID, _EventPoolItem] = {}
    fact_slot = _fact_slot_key(request.source)
    if fact_slot:
        for target in _take_memories(
            indexes.fact_slot_index.get(fact_slot, ()),
            request.max_event_candidates,
        ):
            _add_event_pool_item(pool, request.source, target, "fact_slot_match")

    content_key = normalized_linking_key(request.source.content)
    for target in _take_memories(
        indexes.event_content_index.get(content_key, ()),
        request.max_event_candidates,
    ):
        _add_event_pool_item(pool, request.source, target, "exact_normalized_content")

    for entity_key in sorted(_entity_key_sets(request.source)):
        for target in _take_memories(
            indexes.event_entity_key_index.get(entity_key, ()),
            request.max_event_candidates,
        ):
            _add_event_pool_item(pool, request.source, target, "shared_entity_key")

    for token in sorted(_linking_tokens(content_key)):
        for target in _take_memories(
            indexes.event_token_index.get(token, ()),
            request.max_event_candidates,
        ):
            _add_event_pool_item(pool, request.source, target, "lexical_token_match")

    if len(pool) < request.max_event_candidates:
        for target in _take_memories(
            indexes.event_targets,
            request.max_event_candidates,
        ):
            _add_event_pool_item(pool, request.source, target, "stable_fallback")
            if len(pool) >= request.max_event_candidates:
                break

    return sorted(pool.values(), key=_event_pool_sort_key)[
        : request.max_event_candidates
    ]


def _add_entity_pool_item(
    pool: dict[tuple[object, ...], _EntityPoolItem],
    source_position: int,
    source_entity: EntityRef,
    target: _EntityIndexEntry,
    reason: str,
) -> None:
    key = (source_position, *target.stable_key)
    item = pool.get(key)
    if item is None:
        item = _EntityPoolItem(
            source_position=source_position,
            source_entity=source_entity,
            target=target,
        )
        pool[key] = item
    item.reasons.add(reason)


def _add_event_pool_item(
    pool: dict[UUID, _EventPoolItem],
    source: MemoryRecord,
    target: MemoryRecord,
    reason: str,
) -> None:
    item = pool.get(target.memory_id)
    if item is None:
        item = _EventPoolItem(target=target)
        pool[target.memory_id] = item
    item.reasons.add(reason)
    if target.memory_kind is MemoryKind.EVENT:
        if _time_delta_days(source, target) is None:
            item.reasons.add("time_unbounded")
        else:
            item.reasons.add("within_time_window")


def _score_entity_pool(
    request: CandidateGenerationRequest,
    pool: Sequence[_EntityPoolItem],
    embeddings: dict[str, tuple[float, ...]],
) -> list[LinkCandidate]:
    candidates: list[LinkCandidate] = []
    direct_reasons = {
        "exact_normalized_entity_key",
        "alias_match",
        "lexical_token_match",
    }
    for item in pool:
        similarity = _cosine(
            embeddings[item.source_entity.name],
            embeddings[item.target.entity.name],
        )
        if not (item.reasons & direct_reasons) and (
            similarity < request.min_embedding_similarity
        ):
            continue
        reasons = set(item.reasons)
        if similarity >= request.min_embedding_similarity:
            reasons.add("embedding_candidate")
        score = _entity_score(tuple(reasons), similarity)
        if "lexical_token_match" in reasons:
            score += 0.1
        candidates.append(
            LinkCandidate(
                candidate_id=_candidate_id(
                    LinkCandidateGenerator.ENTITY_POLICY,
                    LinkCandidateKind.ENTITY,
                    request.source,
                    item.target.memory,
                    item.source_entity,
                    item.target.entity,
                ),
                candidate_kind=LinkCandidateKind.ENTITY,
                policy_name=LinkCandidateGenerator.ENTITY_POLICY,
                source_memory=request.source,
                target_memory=item.target.memory,
                source_entity=item.source_entity,
                target_entity=item.target.entity,
                score=score,
                reasons=_ordered_reasons(reasons, _ENTITY_REASON_ORDER),
            )
        )
    return _bounded(candidates, request.max_entity_candidates)


def _score_event_pool(
    request: CandidateGenerationRequest,
    pool: Sequence[_EventPoolItem],
    embeddings: dict[str, tuple[float, ...]],
) -> list[LinkCandidate]:
    candidates: list[LinkCandidate] = []
    direct_reasons = {
        "fact_slot_match",
        "exact_normalized_content",
        "shared_entity_key",
        "lexical_token_match",
    }
    for item in pool:
        similarity = _cosine(
            embeddings[request.source.content],
            embeddings[item.target.content],
        )
        if not (item.reasons & direct_reasons) and (
            similarity < request.min_embedding_similarity
        ):
            continue
        reasons = set(item.reasons)
        if similarity >= request.min_embedding_similarity:
            reasons.add("embedding_candidate")
        score = similarity
        if "fact_slot_match" in reasons:
            score += 0.5
        if "exact_normalized_content" in reasons:
            score += 0.4
        if "shared_entity_key" in reasons:
            score += 0.2
        if "lexical_token_match" in reasons:
            score += 0.1
        candidates.append(
            LinkCandidate(
                candidate_id=_candidate_id(
                    LinkCandidateGenerator.EVENT_POLICY,
                    LinkCandidateKind.EVENT,
                    request.source,
                    item.target,
                ),
                candidate_kind=LinkCandidateKind.EVENT,
                policy_name=LinkCandidateGenerator.EVENT_POLICY,
                source_memory=request.source,
                target_memory=item.target,
                score=score,
                reasons=_ordered_reasons(reasons, _EVENT_REASON_ORDER),
            )
        )
    return _bounded(candidates, request.max_event_candidates)


def _take_entity_entries(
    entries: Iterable[_EntityIndexEntry],
    limit: int,
) -> list[_EntityIndexEntry]:
    return heapq.nsmallest(limit, entries, key=lambda entry: entry.stable_key)


def _take_memories(
    memories: Iterable[MemoryRecord],
    limit: int,
) -> list[MemoryRecord]:
    return heapq.nsmallest(limit, memories, key=lambda memory: str(memory.memory_id))


def _entity_pool_sort_key(item: _EntityPoolItem) -> tuple[object, ...]:
    return (
        _reason_priority(
            item.reasons,
            (
                "exact_normalized_entity_key",
                "alias_match",
                "lexical_token_match",
                "stable_fallback",
            ),
        ),
        _entity_identity(item.source_entity),
        item.source_position,
        item.target.stable_key,
    )


def _event_pool_sort_key(item: _EventPoolItem) -> tuple[object, ...]:
    return (
        _reason_priority(
            item.reasons,
            (
                "fact_slot_match",
                "exact_normalized_content",
                "shared_entity_key",
                "lexical_token_match",
                "stable_fallback",
            ),
        ),
        str(item.target.memory_id),
    )


def _reason_priority(reasons: set[str], order: Sequence[str]) -> int:
    return min((order.index(reason) for reason in reasons if reason in order), default=len(order))


def _ordered_reasons(reasons: set[str], order: Sequence[str]) -> list[str]:
    return [reason for reason in order if reason in reasons]


def _entity_identity(entity: EntityRef) -> tuple[str, ...]:
    return (
        entity.entity_id or "",
        normalized_linking_key(entity.name),
        entity.kind or "",
        entity.role or "",
    )


def _entity_target_identity(
    memory: MemoryRecord,
    entity: EntityRef,
    position: int,
) -> tuple[str, ...]:
    return (str(memory.memory_id), *_entity_identity(entity), str(position))


def _linking_tokens(value: str) -> frozenset[str]:
    return frozenset(_KEY_TOKEN_RE.findall(value.casefold()))


def _fact_slot_key(memory: MemoryRecord) -> str | None:
    value = memory.metadata.get("fact_slot")
    if value is None:
        return None
    normalized = normalized_linking_key(str(value))
    return normalized or None


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
        entity_comparison_count=result.entity_comparison_count,
        event_comparison_count=result.event_comparison_count,
    )


def normalized_linking_key(value: str) -> str:
    return " ".join(_KEY_TOKEN_RE.findall(value.casefold()))


def _eligible_existing(
    source: MemoryRecord,
    existing: Iterable[MemoryRecord],
) -> Iterable[MemoryRecord]:
    if source.status is not MemoryStatus.ACTIVE:
        return
    for target in existing:
        if target.memory_id == source.memory_id:
            continue
        if target.user_id != source.user_id:
            continue
        if target.tenant_id != source.tenant_id:
            continue
        if target.status is not MemoryStatus.ACTIVE:
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


def _time_delta_days(source: MemoryRecord, target: MemoryRecord) -> float | None:
    source_time = _anchor_time(source)
    target_time = _anchor_time(target)
    if source_time is None or target_time is None:
        return None
    duration = abs(source_time - target_time)
    return duration.total_seconds() / 86_400.0


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
