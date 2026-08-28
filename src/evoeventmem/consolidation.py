from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from evoeventmem.core.math_utils import evidence_key, jaccard, unique_evidence
from evoeventmem.core.ports import EmbeddingModel, MemoryRepository
from evoeventmem.domain.models import (
    EntityRef,
    EvidenceRef,
    MemoryRecord,
    MemoryStatus,
    normalize_memory_content,
)
from evoeventmem.linking import (
    CandidateGenerationRequest,
    CandidateGenerationResult,
    LinkCandidateGenerator,
    normalized_linking_key,
)
from evoeventmem.router import QueryIntent


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
    conflict_target_memory_id: UUID | None = None


class ETECApplyResult(BaseModel):
    decision: ETECDecision
    stored_memory: MemoryRecord | None = None
    updated_memories: list[MemoryRecord] = Field(default_factory=list)


class ETECConsolidator:
    """Evidence-constrained temporal consolidation with inspectable rules."""

    POLICY_NAME = "etec-rule-weighted.v2"

    def __init__(
        self,
        embedding_model: EmbeddingModel,
        thresholds: ETECThresholds | None = None,
        *,
        candidate_generator: LinkCandidateGenerator | None = None,
        routing_intent: QueryIntent | None = None,
    ) -> None:
        self._embedding_model = embedding_model
        self._thresholds = thresholds or ETECThresholds()
        self._candidate_generator = candidate_generator or LinkCandidateGenerator(embedding_model)
        self._routing_intent = routing_intent

    def decide(
        self,
        source: MemoryRecord,
        candidates: Sequence[MemoryRecord],
    ) -> ETECDecision:
        reject_decision = self._reject_inactive_source(source) or self._reject_without_evidence(
            source
        )
        if reject_decision is not None:
            return reject_decision
        return self._select_decision(source, self._score_candidates(source, candidates))

    def apply(
        self,
        repository: MemoryRepository,
        source: MemoryRecord,
        candidates: Sequence[MemoryRecord] | None = None,
    ) -> ETECApplyResult:
        with repository.transaction() as transaction:
            source = _sanitize_source_supersedes(transaction, source)
            source_rejection = self._reject_inactive_source(source)
            if source_rejection is not None:
                return ETECApplyResult(decision=source_rejection)
            if transaction.get(source.memory_id) is not None:
                return ETECApplyResult(decision=self._reject_source_id_collision(source))

            existing = (
                _resolve_explicit_candidates(transaction, candidates)
                if candidates is not None
                else transaction.list_for_user(source.user_id)
            )
            eligible = _eligible_candidates(source, existing)
            generated = self._candidate_generator.generate(
                CandidateGenerationRequest(source=source, existing=eligible)
            )
            bounded_targets = _bounded_target_memories(generated, eligible)

            reject_decision = self._reject_without_evidence(source)
            scored = (
                []
                if reject_decision is not None
                else self._score_candidates(source, bounded_targets)
            )
            decision = reject_decision or self._select_decision(source, scored)
            if decision.action is ConsolidationAction.REJECT:
                return ETECApplyResult(decision=decision)

            if (
                decision.action is ConsolidationAction.SUPERSEDE
                and self._routing_intent is QueryIntent.TEMPORAL
            ):
                decision = decision.model_copy(
                    update={
                        "action": ConsolidationAction.MERGE,
                        "rule_hits": [
                            *decision.rule_hits,
                            "temporal_intent_supersede_downgraded_to_merge",
                        ],
                        "reason": (
                            "SUPERSEDE was downgraded to MERGE because the query "
                            "intent is TEMPORAL; old values are preserved for sorting."
                        ),
                    }
                )

            targets_by_id = {target.memory_id: target for target in bounded_targets}
            if decision.action is ConsolidationAction.MERGE:
                target = _require_target(decision, bounded_targets)
                merged = self._merge_memory(target, source, decision)
                relinked_histories = self._relink_merged_source_histories(
                    transaction,
                    merged,
                    source,
                )
                for history in relinked_histories:
                    transaction.add(history)
                transaction.add(merged)
                return ETECApplyResult(
                    decision=decision,
                    stored_memory=merged,
                    updated_memories=[*relinked_histories, merged],
                )

            if decision.action is ConsolidationAction.SUPERSEDE:
                return self._apply_supersede(
                    transaction,
                    source,
                    decision,
                    scored,
                    targets_by_id,
                )

            stored = self._store_new_memory(
                source,
                decision,
                supersedes=list(source.supersedes),
            )
            transaction.add(stored)
            return ETECApplyResult(
                decision=decision,
                stored_memory=stored,
                updated_memories=[stored],
            )

    def _score_candidates(
        self,
        source: MemoryRecord,
        candidates: Sequence[MemoryRecord],
    ) -> list[ETECDecision]:
        return [self._score_pair(source, candidate) for candidate in candidates]

    def _select_decision(
        self,
        source: MemoryRecord,
        scored: Sequence[ETECDecision],
    ) -> ETECDecision:
        conflicts = [
            decision
            for decision in scored
            if decision.conflict_target_memory_id is not None
        ]
        if conflicts:
            return max(
                conflicts,
                key=lambda decision: decision.features.contradiction_score,
            )

        supersede = [
            decision for decision in scored if decision.action is ConsolidationAction.SUPERSEDE
        ]
        if supersede:
            return max(
                supersede,
                key=lambda decision: decision.features.contradiction_score,
            )

        merge = [decision for decision in scored if decision.action is ConsolidationAction.MERGE]
        if merge:
            return max(merge, key=lambda decision: decision.score)

        if scored:
            best = max(scored, key=lambda decision: decision.score)
            return best.model_copy(
                update={
                    "action": ConsolidationAction.ADD,
                    "rule_hits": [*best.rule_hits, "no_merge_or_conflict_rule_hit"],
                    "reason": (
                        best.reason
                        if "disjoint_temporal_intervals" in best.rule_hits
                        else "No candidate satisfied MERGE or SUPERSEDE thresholds."
                    ),
                }
            )

        return self._add_without_candidates(source)

    def _apply_supersede(
        self,
        repository: MemoryRepository,
        source: MemoryRecord,
        decision: ETECDecision,
        scored: Sequence[ETECDecision],
        targets_by_id: dict[UUID, MemoryRecord],
    ) -> ETECApplyResult:
        source_time = _require_fact_effective_time(source)
        pair_decisions = [
            pair
            for pair in scored
            if pair.action is ConsolidationAction.SUPERSEDE
            and pair.target_memory_id in targets_by_id
        ]
        stale_pairs = [
            pair
            for pair in pair_decisions
            if _require_fact_effective_time(targets_by_id[_decision_target_id(pair)]) > source_time
        ]

        if stale_pairs:
            winner_decision = max(
                stale_pairs,
                key=lambda pair: (
                    _require_fact_effective_time(targets_by_id[_decision_target_id(pair)]),
                    str(_decision_target_id(pair)),
                ),
            )
            winner = targets_by_id[_decision_target_id(winner_decision)]
            changed_targets: list[MemoryRecord] = []
            winner_supersedes = list(winner.supersedes)
            source_supersedes = list(source.supersedes)
            for pair in pair_decisions:
                target = targets_by_id[_decision_target_id(pair)]
                if target.memory_id == winner.memory_id:
                    continue
                target_time = _fact_effective_time(target)
                if target_time is None:
                    continue
                superseder = source if target_time < source_time else winner
                if superseder.memory_id == source.memory_id:
                    actual_decision = pair
                else:
                    actual_pair_decision = self._score_pair(superseder, target)
                    if actual_pair_decision.action not in (
                        ConsolidationAction.MERGE,
                        ConsolidationAction.SUPERSEDE,
                    ):
                        return ETECApplyResult(
                            decision=_ambiguous_current_winners_decision(
                                source,
                                winner_decision,
                                actual_pair_decision,
                            )
                        )
                    actual_decision = _cleanup_supersede_decision(actual_pair_decision)
                updated_target = self._supersede_memory(
                    target,
                    superseder,
                    actual_decision,
                )
                changed_targets.append(updated_target)
                if superseder.memory_id == source.memory_id:
                    source_supersedes.append(target.memory_id)
                else:
                    winner_supersedes.append(target.memory_id)

            stored_source = self._store_superseded_memory(
                source,
                winner,
                winner_decision,
                supersedes=source_supersedes,
            )
            winner_supersedes.append(source.memory_id)
            updated_winner = self._add_supersedes(
                winner,
                winner_decision,
                winner_supersedes,
            )
            updated = [*changed_targets, stored_source, updated_winner]
            for memory in updated:
                repository.add(memory)
            return ETECApplyResult(
                decision=winner_decision,
                stored_memory=stored_source,
                updated_memories=updated,
            )

        superseded: list[MemoryRecord] = []
        for pair in pair_decisions:
            target = targets_by_id[_decision_target_id(pair)]
            superseded.append(self._supersede_memory(target, source, pair))
        for memory in superseded:
            repository.add(memory)
        stored = self._store_new_memory(
            source,
            decision,
            supersedes=[
                *source.supersedes,
                *(memory.memory_id for memory in superseded),
            ],
        )
        repository.add(stored)
        return ETECApplyResult(
            decision=decision,
            stored_memory=stored,
            updated_memories=[*superseded, stored],
        )

    def _reject_inactive_source(self, source: MemoryRecord) -> ETECDecision | None:
        if source.status is MemoryStatus.ACTIVE:
            return None
        return ETECDecision(
            action=ConsolidationAction.REJECT,
            source_memory_id=source.memory_id,
            score=0.0,
            features=self._standalone_features(source),
            thresholds=self._thresholds,
            rule_hits=["inactive_source"],
            reason="ETEC accepts only active source memories for consolidation.",
        )

    def _reject_source_id_collision(self, source: MemoryRecord) -> ETECDecision:
        return ETECDecision(
            action=ConsolidationAction.REJECT,
            source_memory_id=source.memory_id,
            score=0.0,
            features=self._standalone_features(source),
            thresholds=self._thresholds,
            rule_hits=["source_memory_id_collision"],
            reason="A durable memory with the source memory ID already exists.",
        )

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
        features = self._standalone_features(source)
        return ETECDecision(
            action=ConsolidationAction.ADD,
            source_memory_id=source.memory_id,
            score=features.evidence_consistency,
            features=features,
            thresholds=self._thresholds,
            rule_hits=["no_active_candidate"],
            reason="No active candidate was available for consolidation.",
        )

    def _score_pair(self, source: MemoryRecord, target: MemoryRecord) -> ETECDecision:
        features = self._features(source, target)
        score = _weighted_score(features)
        rule_hits: list[str] = []
        conflict_target_id: UUID | None = None
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
            source_time = _fact_effective_time(source)
            target_time = _fact_effective_time(target)
            if source_time is None or target_time is None:
                rule_hits.extend(
                    ["missing_fact_effective_time", "temporal_conflict_kept_both"]
                )
                reason = (
                    "A contradictory single-valued fact is missing an effective time, "
                    "so temporal ordering is unsafe; both sides are retained with an "
                    "explicit conflict marker instead of silently dropping the incoming fact."
                )
                action = ConsolidationAction.ADD
                conflict_target_id = target.memory_id
            elif source_time == target_time:
                rule_hits.extend(["equal_fact_effective_time", "temporal_conflict_kept_both"])
                reason = (
                    "Contradictory single-valued facts have equal effective times, "
                    "so neither can supersede the other; both sides are retained with an "
                    "explicit conflict marker instead of silently dropping the incoming fact."
                )
                action = ConsolidationAction.ADD
                conflict_target_id = target.memory_id
            elif source_time > target_time:
                action = ConsolidationAction.SUPERSEDE
                rule_hits.extend(["temporal_contradiction", "newer_source_supersedes_older_target"])
                reason = "The incoming fact is strictly newer and supersedes the target."
            else:
                action = ConsolidationAction.SUPERSEDE
                rule_hits.extend(
                    ["temporal_contradiction", "stale_source_superseded_by_newer_target"]
                )
                reason = "The incoming fact is stale and is superseded by the newer target."
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
            if _distinct_fact_value(source, target):
                rule_hits.append("distinct_fact_value")
                reason = (
                    "Both memories declare fact metadata with distinct values, "
                    "so they are added as separate facts."
                )
            elif _merge_temporally_compatible(source, target):
                action = ConsolidationAction.MERGE
                rule_hits.append("duplicate_fact")
                reason = (
                    "Candidate is semantically and structurally consistent with the "
                    "incoming memory."
                )
            else:
                rule_hits.append("disjoint_temporal_intervals")
                reason = "Known temporal intervals are disjoint, so the facts remain separate."

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
            conflict_target_memory_id=conflict_target_id,
        )

    def _features(self, source: MemoryRecord, target: MemoryRecord) -> ETECFeatureVector:
        embeddings = self._embedding_model.embed_texts([source.content, target.content])
        semantic = _semantic_similarity(embeddings[0].vector, embeddings[1].vector)
        if normalize_memory_content(source.content) == normalize_memory_content(
            target.content
        ) or _same_fact_value(source, target):
            semantic = 1.0
        entity_role = jaccard(_entity_role_keys(source), _entity_role_keys(target))
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
        merged_source_ids = [
            memory_id
            for memory_id in _unique_uuids(
                [*target.derived_from, *source.derived_from, source.memory_id]
            )
            if memory_id != target.memory_id
        ]
        metadata = _metadata_with_decision(target, decision)
        metadata["merged_source_memory_ids"] = [str(memory_id) for memory_id in merged_source_ids]
        metadata["merged_contents"] = sorted({target.content, source.content})
        return _validated_copy(
            target,
            {
                "content": _compose_merge_content(target.content, source.content),
                "evidence_refs": unique_evidence([*target.evidence_refs, *source.evidence_refs]),
                "entities": _unique_entities([*target.entities, *source.entities]),
                "relations": [*target.relations, *source.relations],
                "supersedes": _unique_uuids([*target.supersedes, *source.supersedes]),
                "derived_from": merged_source_ids,
                "valid_from": _earliest_time(target.valid_from, source.valid_from),
                "valid_to": _latest_time(target.valid_to, source.valid_to),
                "confidence": max(target.confidence, source.confidence),
                "metadata": metadata,
                "updated_at": datetime.now(UTC),
            },
        )

    def _relink_merged_source_histories(
        self,
        repository: MemoryRepository,
        merged: MemoryRecord,
        source: MemoryRecord,
    ) -> list[MemoryRecord]:
        relinked: list[MemoryRecord] = []
        for memory_id in source.supersedes:
            history = repository.get(memory_id)
            if history is None:
                raise ValueError("verified source history disappeared during merge")
            pair_decision = self._score_pair(merged, history)
            relink_decision = _merged_source_history_relink_decision(pair_decision)
            relinked.append(
                _validated_copy(
                    history,
                    {
                        "superseded_by": merged.memory_id,
                        "metadata": _metadata_with_decision(history, relink_decision),
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
        return relinked

    def _supersede_memory(
        self,
        target: MemoryRecord,
        superseder: MemoryRecord,
        decision: ETECDecision,
    ) -> MemoryRecord:
        cutoff = _supersession_cutoff(superseder, target)
        return _validated_copy(
            target,
            {
                "status": MemoryStatus.SUPERSEDED,
                "valid_to": cutoff,
                "superseded_by": superseder.memory_id,
                "metadata": _metadata_with_decision(target, decision),
                "updated_at": datetime.now(UTC),
            },
        )

    def _add_supersedes(
        self,
        target: MemoryRecord,
        decision: ETECDecision,
        supersedes: Iterable[UUID],
    ) -> MemoryRecord:
        return _validated_copy(
            target,
            {
                "supersedes": _unique_uuids(supersedes),
                "metadata": _metadata_with_decision(target, decision),
                "updated_at": datetime.now(UTC),
            },
        )

    def _store_superseded_memory(
        self,
        source: MemoryRecord,
        superseder: MemoryRecord,
        decision: ETECDecision,
        *,
        supersedes: Iterable[UUID],
    ) -> MemoryRecord:
        cutoff = _supersession_cutoff(superseder, source)
        return _validated_copy(
            source,
            {
                "status": MemoryStatus.SUPERSEDED,
                "valid_to": cutoff,
                "supersedes": _unique_uuids(supersedes),
                "superseded_by": superseder.memory_id,
                "metadata": _metadata_with_decision(source, decision),
                "updated_at": datetime.now(UTC),
            },
        )

    def _store_new_memory(
        self,
        source: MemoryRecord,
        decision: ETECDecision,
        supersedes: Iterable[UUID],
    ) -> MemoryRecord:
        metadata = _metadata_with_decision(source, decision)
        if decision.conflict_target_memory_id is not None:
            metadata["conflicts_with"] = [str(decision.conflict_target_memory_id)]
        return _validated_copy(
            source,
            {
                "status": MemoryStatus.ACTIVE,
                "supersedes": _unique_uuids(supersedes),
                "superseded_by": None,
                "valid_from": source.valid_from,
                "valid_to": source.valid_to,
                "metadata": metadata,
                "updated_at": datetime.now(UTC),
            },
        )


def _merged_source_history_relink_decision(decision: ETECDecision) -> ETECDecision:
    return decision.model_copy(
        update={
            "action": ConsolidationAction.SUPERSEDE,
            "rule_hits": list(dict.fromkeys([*decision.rule_hits, "merged_source_history_relink"])),
            "reason": (
                "The merged target inherits the source's verified historical supersession link."
            ),
        }
    )


def _cleanup_supersede_decision(decision: ETECDecision) -> ETECDecision:
    if decision.action is not ConsolidationAction.MERGE:
        return decision
    return decision.model_copy(
        update={
            "action": ConsolidationAction.SUPERSEDE,
            "rule_hits": [*decision.rule_hits, "duplicate_current_fact_cleanup"],
            "reason": (
                "Current-fact cleanup supersedes the intermediate target with the selected winner."
            ),
        }
    )


def _ambiguous_current_winners_decision(
    source: MemoryRecord,
    winner_decision: ETECDecision,
    actual_pair_decision: ETECDecision,
) -> ETECDecision:
    rule_hits = list(
        dict.fromkeys(
            [
                *winner_decision.rule_hits,
                *actual_pair_decision.rule_hits,
                "ambiguous_current_fact_winners",
            ]
        )
    )
    return winner_decision.model_copy(
        update={
            "action": ConsolidationAction.REJECT,
            "source_memory_id": source.memory_id,
            "rule_hits": rule_hits,
            "reason": (
                "Current facts are ambiguous because contradictory winners share an "
                "equal effective time; the incoming source is rejected without mutation."
            ),
        }
    )


def _sanitize_source_supersedes(
    repository: MemoryRepository,
    source: MemoryRecord,
) -> MemoryRecord:
    verified: list[UUID] = []
    for memory_id in _unique_uuids(source.supersedes):
        durable = repository.get(memory_id)
        if durable is None:
            continue
        if durable.user_id != source.user_id or durable.tenant_id != source.tenant_id:
            continue
        if durable.status is not MemoryStatus.SUPERSEDED:
            continue
        if durable.superseded_by != source.memory_id:
            continue
        verified.append(memory_id)
    if verified == source.supersedes:
        return source
    return _validated_copy(source, {"supersedes": verified})


def _resolve_explicit_candidates(
    repository: MemoryRepository,
    candidates: Iterable[MemoryRecord],
) -> list[MemoryRecord]:
    resolved: list[MemoryRecord] = []
    seen: set[UUID] = set()
    for candidate in candidates:
        if candidate.memory_id in seen:
            continue
        seen.add(candidate.memory_id)
        durable = repository.get(candidate.memory_id)
        if durable is not None:
            resolved.append(durable)
    return resolved


def _eligible_candidates(
    source: MemoryRecord,
    candidates: Iterable[MemoryRecord],
) -> list[MemoryRecord]:
    return [
        candidate
        for candidate in candidates
        if candidate.memory_id != source.memory_id
        and candidate.user_id == source.user_id
        and candidate.tenant_id == source.tenant_id
        and candidate.status is MemoryStatus.ACTIVE
    ]


def _bounded_target_memories(
    generated: CandidateGenerationResult,
    eligible: Sequence[MemoryRecord],
) -> list[MemoryRecord]:
    eligible_by_id = {candidate.memory_id: candidate for candidate in eligible}
    seen: set[UUID] = set()
    targets: list[MemoryRecord] = []
    for candidate in [*generated.entity_candidates, *generated.event_candidates]:
        target = eligible_by_id.get(candidate.target_memory.memory_id)
        if target is None or target.memory_id in seen:
            continue
        seen.add(target.memory_id)
        targets.append(target)
    return targets


def _decision_target_id(decision: ETECDecision) -> UUID:
    if decision.target_memory_id is None:
        raise ValueError("decision target is required")
    return decision.target_memory_id


def _fact_effective_time(memory: MemoryRecord) -> datetime | None:
    return memory.valid_from or memory.event_time


def _require_fact_effective_time(memory: MemoryRecord) -> datetime:
    effective_time = _fact_effective_time(memory)
    if effective_time is None:
        raise ValueError("fact effective time is required")
    return effective_time


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
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
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
    slot = fact_slot_key(memory)
    if slot:
        keys.add(f"slot:{slot}")
    return keys


def _structural_similarity(source: MemoryRecord, target: MemoryRecord) -> float:
    score = 0.4 if source.memory_kind is target.memory_kind else 0.0
    source_roles = {normalized_linking_key(role) for role in source.roles.values()}
    target_roles = {normalized_linking_key(role) for role in target.roles.values()}
    score += 0.3 * jaccard(source_roles, target_roles)
    source_predicates = {
        normalized_linking_key(relation.predicate) for relation in source.relations
    }
    target_predicates = {
        normalized_linking_key(relation.predicate) for relation in target.relations
    }
    score += 0.3 * jaccard(source_predicates, target_predicates)
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


def _merge_temporally_compatible(source: MemoryRecord, target: MemoryRecord) -> bool:
    source_start, source_end = _interval(source)
    target_start, target_end = _interval(target)
    if source_start is None or target_start is None:
        return True
    return _intervals_overlap(source_start, source_end, target_start, target_end)


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
    source_keys = {evidence_key(item) for item in source}
    target_keys = {evidence_key(item) for item in target}
    if source_keys & target_keys:
        return 1.0
    source_types = {item.source_type for item in source}
    target_types = {item.source_type for item in target}
    return 0.8 if source_types & target_types else 0.6


def _interval(memory: MemoryRecord) -> tuple[datetime | None, datetime | None]:
    if memory.valid_from is not None:
        return (memory.valid_from, memory.valid_to)
    if memory.event_time is not None:
        return (memory.event_time, memory.event_time)
    return (None, memory.valid_to)


def _intervals_overlap(
    left_start: datetime,
    left_end: datetime | None,
    right_start: datetime,
    right_end: datetime | None,
) -> bool:
    left_stop = left_end or datetime.max.replace(tzinfo=UTC)
    right_stop = right_end or datetime.max.replace(tzinfo=UTC)
    return left_start <= right_stop and right_start <= left_stop


def fact_slot_key(memory: MemoryRecord) -> str | None:
    value = memory.metadata.get("fact_slot")
    if not isinstance(value, str):
        return None
    normalized = normalized_linking_key(value)
    return normalized or None


def _same_fact_slot(source: MemoryRecord, target: MemoryRecord) -> bool:
    source_slot = fact_slot_key(source)
    target_slot = fact_slot_key(target)
    return source_slot is not None and source_slot == target_slot


def fact_value_key(memory: MemoryRecord) -> str:
    value = memory.metadata.get("fact_value")
    if isinstance(value, str) and normalized_linking_key(value):
        return normalized_linking_key(value)
    return normalize_memory_content(memory.content)


def _same_fact_value(source: MemoryRecord, target: MemoryRecord) -> bool:
    return fact_value_key(source) == fact_value_key(target)


def _explicit_fact_value(memory: MemoryRecord) -> str | None:
    value = memory.metadata.get("fact_value")
    if isinstance(value, str) and normalized_linking_key(value):
        return normalized_linking_key(value)
    return None


def _distinct_fact_value(source: MemoryRecord, target: MemoryRecord) -> bool:
    """True when both memories declare explicit fact values that disagree.

    Memories that only carry semantic content (no explicit fact value) keep
    the ordinary merge path; the gate applies only when the write pipeline
    declares durable fact identity and the values disagree, so distinct
    facts are never folded into one record.
    """
    source_value = _explicit_fact_value(source)
    target_value = _explicit_fact_value(target)
    return (
        source_value is not None
        and target_value is not None
        and source_value != target_value
    )


def _memory_is_multi_valued(memory: MemoryRecord) -> bool:
    return memory.metadata.get("multi_valued") is True


def _is_multi_valued(source: MemoryRecord, target: MemoryRecord) -> bool:
    return _memory_is_multi_valued(source) or _memory_is_multi_valued(target)


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
    cutoff = _require_fact_effective_time(source)
    target_start = _fact_effective_time(target)
    if target_start is not None and cutoff < target_start:
        return target_start
    return cutoff


def _content_tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", text.lower()))


def _compose_merge_content(target_content: str, source_content: str) -> str:
    """Compose merged content without destroying either side's information.

    When one side's tokens fully contain the other's, keep the more specific
    phrasing. Otherwise concatenate both surface forms so no answer-bearing
    token is lost from the retrievable content.
    """
    target_tokens = _content_tokens(target_content)
    source_tokens = _content_tokens(source_content)
    if source_tokens <= target_tokens:
        return target_content
    if target_tokens <= source_tokens:
        return source_content
    return f"{target_content} {source_content}"


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
    "fact_slot_key",
    "fact_value_key",
]
