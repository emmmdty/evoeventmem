from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from evoeventmem.core.ports import EmbeddingModel, MemoryRepository
from evoeventmem.domain.models import EntityRef, EvidenceRef, MemoryRecord, MemoryStatus
from evoeventmem.linking import normalized_linking_key


class ConsolidationAction(StrEnum):
    ADD = "ADD"
    MERGE = "MERGE"
    SUPERSEDE = "SUPERSEDE"
    REJECT = "REJECT"


class ETECFeatureVector(BaseModel):
    semantic_similarity: float = Field(ge=0.0, le=1.0)
    entity_role_overlap: float = Field(ge=0.0, le=1.0)
    temporal_overlap: float = Field(ge=0.0, le=1.0)
    structural_similarity: float = Field(ge=0.0, le=1.0)
    evidence_consistency: float = Field(ge=0.0, le=1.0)
    contradiction_score: float = Field(ge=0.0, le=1.0)
    multi_valued: bool = False


class ETECThresholds(BaseModel):
    merge_semantic_min: float = Field(default=0.82, ge=0.0, le=1.0)
    merge_entity_role_min: float = Field(default=0.35, ge=0.0, le=1.0)
    merge_score_min: float = Field(default=0.68, ge=0.0, le=1.0)
    supersede_contradiction_min: float = Field(default=0.7, ge=0.0, le=1.0)
    reject_evidence_min: float = Field(default=0.1, ge=0.0, le=1.0)


class ETECDecision(BaseModel):
    action: ConsolidationAction
    source_memory_id: UUID
    target_memory_id: UUID | None = None
    score: float = Field(ge=0.0, le=1.0)
    features: ETECFeatureVector
    thresholds: ETECThresholds
    rule_hits: list[str] = Field(default_factory=list)
    reason: str


class ETECApplyResult(BaseModel):
    decision: ETECDecision
    stored_memory: MemoryRecord | None = None
    updated_memories: list[MemoryRecord] = Field(default_factory=list)


class ETECConsolidator:
    """Evidence-constrained temporal consolidation with inspectable rules."""

    POLICY_NAME = "etec-rule-weighted.v1"

    def __init__(
        self,
        embedding_model: EmbeddingModel,
        thresholds: ETECThresholds | None = None,
    ) -> None:
        self._embedding_model = embedding_model
        self._thresholds = thresholds or ETECThresholds()

    def decide(
        self,
        source: MemoryRecord,
        candidates: Sequence[MemoryRecord],
    ) -> ETECDecision:
        reject_decision = self._reject_without_evidence(source)
        if reject_decision is not None:
            return reject_decision

        scored = [self._score_pair(source, candidate) for candidate in candidates]
        supersede = [
            decision
            for decision in scored
            if decision.action is ConsolidationAction.SUPERSEDE
        ]
        if supersede:
            return max(supersede, key=lambda decision: decision.features.contradiction_score)

        merge = [
            decision
            for decision in scored
            if decision.action is ConsolidationAction.MERGE
        ]
        if merge:
            return max(merge, key=lambda decision: decision.score)

        if scored:
            best = max(scored, key=lambda decision: decision.score)
            return best.model_copy(
                update={
                    "action": ConsolidationAction.ADD,
                    "rule_hits": [*best.rule_hits, "no_merge_or_conflict_rule_hit"],
                    "reason": "No candidate satisfied MERGE or SUPERSEDE thresholds.",
                }
            )

        return self._add_without_candidates(source)

    def apply(
        self,
        repository: MemoryRepository,
        source: MemoryRecord,
        candidates: Sequence[MemoryRecord] | None = None,
    ) -> ETECApplyResult:
        existing = (
            list(candidates)
            if candidates is not None
            else repository.list_for_user(source.user_id)
        )
        active_candidates = [
            memory
            for memory in existing
            if memory.memory_id != source.memory_id and memory.status is MemoryStatus.ACTIVE
        ]
        decision = self.decide(source, active_candidates)
        if decision.action is ConsolidationAction.REJECT:
            return ETECApplyResult(decision=decision)

        if decision.action is ConsolidationAction.MERGE:
            target = _require_target(decision, active_candidates)
            merged = self._merge_memory(target, source, decision)
            repository.add(merged)
            return ETECApplyResult(
                decision=decision,
                stored_memory=merged,
                updated_memories=[merged],
            )

        if decision.action is ConsolidationAction.SUPERSEDE:
            contradictions = [
                candidate
                for candidate in active_candidates
                if self._features(source, candidate).contradiction_score
                >= self._thresholds.supersede_contradiction_min
                and not _is_multi_valued(source, candidate)
            ]
            superseded = [
                self._supersede_memory(target, source, decision)
                for target in contradictions
            ]
            for memory in superseded:
                repository.add(memory)
            stored = self._store_new_memory(
                source,
                decision,
                supersedes=[memory.memory_id for memory in superseded],
            )
            repository.add(stored)
            return ETECApplyResult(
                decision=decision,
                stored_memory=stored,
                updated_memories=[*superseded, stored],
            )

        stored = self._store_new_memory(source, decision, supersedes=list(source.supersedes))
        repository.add(stored)
        return ETECApplyResult(decision=decision, stored_memory=stored, updated_memories=[stored])

    def _reject_without_evidence(self, source: MemoryRecord) -> ETECDecision | None:
        features = self._standalone_features(source)
        if features.evidence_consistency >= self._thresholds.reject_evidence_min:
            return None
        return ETECDecision(
            action=ConsolidationAction.REJECT,
            source_memory_id=source.memory_id,
            score=0.0,
            features=features,
            thresholds=self._thresholds,
            rule_hits=["missing_source_evidence"],
            reason="ETEC rejects memories that are not constrained by source evidence.",
        )

    def _add_without_candidates(self, source: MemoryRecord) -> ETECDecision:
        return ETECDecision(
            action=ConsolidationAction.ADD,
            source_memory_id=source.memory_id,
            score=self._standalone_features(source).evidence_consistency,
            features=self._standalone_features(source),
            thresholds=self._thresholds,
            rule_hits=["no_active_candidate"],
            reason="No active candidate was available for consolidation.",
        )

    def _score_pair(self, source: MemoryRecord, target: MemoryRecord) -> ETECDecision:
        features = self._features(source, target)
        score = _weighted_score(features)
        rule_hits: list[str] = []
        action = ConsolidationAction.ADD
        reason = "Candidate did not meet a consolidation threshold."

        if features.evidence_consistency < self._thresholds.reject_evidence_min:
            action = ConsolidationAction.REJECT
            rule_hits.append("missing_source_evidence")
            reason = "Candidate pair is not constrained by source evidence."
        elif (
            features.contradiction_score >= self._thresholds.supersede_contradiction_min
            and not features.multi_valued
        ):
            action = ConsolidationAction.SUPERSEDE
            rule_hits.append("temporal_contradiction")
            reason = "A current single-valued fact contradicts the incoming evidence."
        elif (
            features.multi_valued
            and _same_fact_slot(source, target)
            and not _same_fact_value(source, target)
        ):
            rule_hits.append("explicit_multi_valued_slot")
            reason = "Same temporal slot is explicitly multi-valued, so the memory is added."
        elif (
            features.semantic_similarity >= self._thresholds.merge_semantic_min
            and features.entity_role_overlap >= self._thresholds.merge_entity_role_min
            and score >= self._thresholds.merge_score_min
        ):
            action = ConsolidationAction.MERGE
            rule_hits.append("duplicate_fact")
            reason = (
                "Candidate is semantically and structurally consistent with the incoming memory."
            )

        if _same_fact_value(source, target):
            rule_hits.append("same_fact_value")

        return ETECDecision(
            action=action,
            source_memory_id=source.memory_id,
            target_memory_id=target.memory_id,
            score=score,
            features=features,
            thresholds=self._thresholds,
            rule_hits=rule_hits,
            reason=reason,
        )

    def _features(self, source: MemoryRecord, target: MemoryRecord) -> ETECFeatureVector:
        semantic = _semantic_similarity(
            self._embedding_model.embed_texts([source.content, target.content])[0].vector,
            self._embedding_model.embed_texts([source.content, target.content])[1].vector,
        )
        if (
            source.normalized_content == target.normalized_content
            or _same_fact_value(source, target)
        ):
            semantic = 1.0
        entity_role = _jaccard(_entity_role_keys(source), _entity_role_keys(target))
        if entity_role == 0.0 and _same_fact_slot(source, target):
            entity_role = 1.0
        temporal = _temporal_overlap_score(source, target)
        structural = _structural_similarity(source, target)
        evidence = _evidence_consistency(source.evidence_refs, target.evidence_refs)
        multi_valued = _is_multi_valued(source, target)
        contradiction = _contradiction_score(
            source=source,
            target=target,
            entity_role_overlap=entity_role,
            structural_similarity=structural,
            multi_valued=multi_valued,
        )
        return ETECFeatureVector(
            semantic_similarity=semantic,
            entity_role_overlap=entity_role,
            temporal_overlap=temporal,
            structural_similarity=structural,
            evidence_consistency=evidence,
            contradiction_score=contradiction,
            multi_valued=multi_valued,
        )

    def _standalone_features(self, source: MemoryRecord) -> ETECFeatureVector:
        evidence = 1.0 if source.evidence_refs else 0.0
        return ETECFeatureVector(
            semantic_similarity=0.0,
            entity_role_overlap=0.0,
            temporal_overlap=0.0,
            structural_similarity=0.0,
            evidence_consistency=evidence,
            contradiction_score=0.0,
            multi_valued=_memory_is_multi_valued(source),
        )

    def _merge_memory(
        self,
        target: MemoryRecord,
        source: MemoryRecord,
        decision: ETECDecision,
    ) -> MemoryRecord:
        metadata = _metadata_with_decision(target, decision)
        metadata["merged_source_memory_ids"] = [
            *[str(memory_id) for memory_id in target.derived_from],
            str(source.memory_id),
        ]
        return _validated_copy(
            target,
            {
                "evidence_refs": _unique_evidence([*target.evidence_refs, *source.evidence_refs]),
                "entities": _unique_entities([*target.entities, *source.entities]),
                "relations": [*target.relations, *source.relations],
                "derived_from": _unique_uuids([*target.derived_from, source.memory_id]),
                "valid_from": _earliest_time(target.valid_from, source.valid_from),
                "valid_to": _latest_time(target.valid_to, source.valid_to),
                "confidence": max(target.confidence, source.confidence),
                "metadata": metadata,
                "updated_at": datetime.now(UTC),
            },
        )

    def _supersede_memory(
        self,
        target: MemoryRecord,
        source: MemoryRecord,
        decision: ETECDecision,
    ) -> MemoryRecord:
        cutoff = _supersession_cutoff(source, target)
        return _validated_copy(
            target,
            {
                "status": MemoryStatus.SUPERSEDED,
                "valid_to": cutoff,
                "superseded_by": source.memory_id,
                "metadata": _metadata_with_decision(target, decision),
                "updated_at": datetime.now(UTC),
            },
        )

    def _store_new_memory(
        self,
        source: MemoryRecord,
        decision: ETECDecision,
        supersedes: list[UUID],
    ) -> MemoryRecord:
        return _validated_copy(
            source,
            {
                "status": MemoryStatus.ACTIVE,
                "supersedes": _unique_uuids(supersedes),
                "superseded_by": None,
                "valid_from": source.valid_from or source.event_time,
                "metadata": _metadata_with_decision(source, decision),
                "updated_at": datetime.now(UTC),
            },
        )


def _require_target(decision: ETECDecision, candidates: Sequence[MemoryRecord]) -> MemoryRecord:
    if decision.target_memory_id is None:
        raise ValueError("decision target is required")
    for candidate in candidates:
        if candidate.memory_id == decision.target_memory_id:
            return candidate
    raise ValueError("decision target is not in candidate set")


def _weighted_score(features: ETECFeatureVector) -> float:
    score = (
        features.semantic_similarity * 0.35
        + features.entity_role_overlap * 0.2
        + features.temporal_overlap * 0.15
        + features.structural_similarity * 0.15
        + features.evidence_consistency * 0.15
        - features.contradiction_score * 0.35
    )
    return min(1.0, max(0.0, score))


def _semantic_similarity(left: Sequence[float], right: Sequence[float]) -> float:
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
    return max(0.0, min(1.0, dot_product / (left_norm * right_norm)))


def _entity_role_keys(memory: MemoryRecord) -> set[str]:
    keys: set[str] = set()
    for entity in memory.entities:
        entity_key = entity.entity_id or normalized_linking_key(entity.name)
        if entity_key:
            keys.add(f"entity:{entity_key}")
            if entity.role:
                keys.add(f"role:{entity_key}:{normalized_linking_key(entity.role)}")
    for raw_name, raw_role in memory.roles.items():
        name = normalized_linking_key(raw_name)
        role = normalized_linking_key(raw_role)
        if name and role:
            keys.add(f"role:{name}:{role}")
    slot = _fact_slot(memory)
    if slot:
        keys.add(f"slot:{slot}")
    return keys


def _structural_similarity(source: MemoryRecord, target: MemoryRecord) -> float:
    score = 0.4 if source.memory_kind is target.memory_kind else 0.0
    source_roles = {normalized_linking_key(role) for role in source.roles.values()}
    target_roles = {normalized_linking_key(role) for role in target.roles.values()}
    score += 0.3 * _jaccard(source_roles, target_roles)
    source_predicates = {
        normalized_linking_key(relation.predicate) for relation in source.relations
    }
    target_predicates = {
        normalized_linking_key(relation.predicate) for relation in target.relations
    }
    score += 0.3 * _jaccard(source_predicates, target_predicates)
    if _same_fact_slot(source, target):
        score = max(score, 0.8)
    return min(1.0, score)


def _temporal_overlap_score(source: MemoryRecord, target: MemoryRecord) -> float:
    source_start, source_end = _interval(source)
    target_start, target_end = _interval(target)
    if source_start is None or target_start is None:
        return 0.5
    if _intervals_overlap(source_start, source_end, target_start, target_end):
        return 1.0
    return 0.0


def _contradiction_score(
    source: MemoryRecord,
    target: MemoryRecord,
    entity_role_overlap: float,
    structural_similarity: float,
    multi_valued: bool,
) -> float:
    if multi_valued or not _same_fact_slot(source, target) or _same_fact_value(source, target):
        return 0.0
    source_start, source_end = _interval(source)
    target_start, target_end = _interval(target)
    if (
        source_start is not None
        and target_start is not None
        and not _intervals_overlap(source_start, source_end, target_start, target_end)
    ):
        return 0.0
    return min(1.0, 0.6 + entity_role_overlap * 0.2 + structural_similarity * 0.2)


def _evidence_consistency(source: Sequence[EvidenceRef], target: Sequence[EvidenceRef]) -> float:
    if not source:
        return 0.0
    if not target:
        return 0.7
    source_keys = {_evidence_key(item) for item in source}
    target_keys = {_evidence_key(item) for item in target}
    if source_keys & target_keys:
        return 1.0
    source_types = {item.source_type for item in source}
    target_types = {item.source_type for item in target}
    return 0.8 if source_types & target_types else 0.6


def _evidence_key(evidence: EvidenceRef) -> tuple[str, str, str | None]:
    return (evidence.source_type, evidence.source_id, evidence.locator)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _interval(memory: MemoryRecord) -> tuple[datetime | None, datetime | None]:
    return (memory.valid_from or memory.event_time, memory.valid_to)


def _intervals_overlap(
    left_start: datetime,
    left_end: datetime | None,
    right_start: datetime,
    right_end: datetime | None,
) -> bool:
    left_stop = left_end or datetime.max.replace(tzinfo=UTC)
    right_stop = right_end or datetime.max.replace(tzinfo=UTC)
    return left_start <= right_stop and right_start <= left_stop


def _fact_slot(memory: MemoryRecord) -> str | None:
    value = memory.metadata.get("fact_slot")
    if isinstance(value, str) and normalized_linking_key(value):
        return normalized_linking_key(value)
    entity_keys = sorted(
        entity.entity_id or normalized_linking_key(entity.name)
        for entity in memory.entities
        if entity.entity_id or normalized_linking_key(entity.name)
    )
    if not entity_keys:
        return None
    role_keys = sorted(normalized_linking_key(value) for value in memory.roles.values() if value)
    return "|".join([memory.memory_kind.value, *entity_keys, *role_keys])


def _same_fact_slot(source: MemoryRecord, target: MemoryRecord) -> bool:
    source_slot = _fact_slot(source)
    target_slot = _fact_slot(target)
    return source_slot is not None and source_slot == target_slot


def _fact_value(memory: MemoryRecord) -> str:
    value = memory.metadata.get("fact_value")
    if isinstance(value, str) and normalized_linking_key(value):
        return normalized_linking_key(value)
    return memory.normalized_content or normalized_linking_key(memory.content)


def _same_fact_value(source: MemoryRecord, target: MemoryRecord) -> bool:
    return _fact_value(source) == _fact_value(target)


def _memory_is_multi_valued(memory: MemoryRecord) -> bool:
    return memory.metadata.get("multi_valued") is True


def _is_multi_valued(source: MemoryRecord, target: MemoryRecord) -> bool:
    return _memory_is_multi_valued(source) or _memory_is_multi_valued(target)


def _unique_evidence(items: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[EvidenceRef] = []
    for item in items:
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _unique_entities(items: Iterable[EntityRef]) -> list[EntityRef]:
    seen: set[tuple[str | None, str, str | None, str | None]] = set()
    unique: list[EntityRef] = []
    for item in items:
        key = (item.entity_id, normalized_linking_key(item.name), item.kind, item.role)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _unique_uuids(items: Iterable[UUID]) -> list[UUID]:
    seen: set[UUID] = set()
    unique: list[UUID] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _earliest_time(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _latest_time(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None or right is None:
        return None
    return max(left, right)


def _supersession_cutoff(source: MemoryRecord, target: MemoryRecord) -> datetime:
    cutoff = source.valid_from or source.event_time or datetime.now(UTC)
    target_start = target.valid_from or target.event_time
    if target_start is not None and cutoff < target_start:
        return target_start
    return cutoff


def _metadata_with_decision(memory: MemoryRecord, decision: ETECDecision) -> dict[str, object]:
    metadata: dict[str, object] = dict(memory.metadata)
    metadata["etec"] = {
        "policy_name": ETECConsolidator.POLICY_NAME,
        "decision": decision.model_dump(mode="json"),
    }
    return metadata


def _validated_copy(memory: MemoryRecord, update: dict[str, object]) -> MemoryRecord:
    payload = memory.model_dump(mode="python")
    payload.update(update)
    return MemoryRecord.model_validate(payload)


__all__ = [
    "ConsolidationAction",
    "ETECApplyResult",
    "ETECConsolidator",
    "ETECDecision",
    "ETECFeatureVector",
    "ETECThresholds",
]
