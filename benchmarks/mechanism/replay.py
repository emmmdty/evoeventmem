"""Offline, deterministic replay of sealed run artifacts (Eval A).

Rebuilds the raw and ETEC memory stores for samples of a finalized run from
their immutable extraction snapshots, using the base run's ``model_cache`` in
strict offline mode: any embedding lookup not already cached raises
``OfflineCacheMiss`` instead of contacting a network endpoint. No reader and
no extractor calls are ever made; the base run's cache is only read.

The replay records every consolidation decision (rule hits + feature vector)
so Eval A can bucket non-ADD actions into the R1-R7 root causes and verify
that the action counts match the run's ``ingestion.etec.actions``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.common.artifacts import ExtractionSnapshot, load_finalized, require_manifest
from benchmarks.common.memory_inputs import materialize_event_store
from evoeventmem.consolidation import ETECConsolidator, ETECDecision
from evoeventmem.core.ports import EmbeddingModel, EmbeddingResponse
from evoeventmem.domain.models import MemoryRecord
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.models.cache import CachedEmbeddingModel, FileModelCache
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel


class OfflineCacheMiss(RuntimeError):
    """Raised when an embedding lookup is not covered by the base run's cache."""


class _OfflineOnlyEmbedding(EmbeddingModel):
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        raise OfflineCacheMiss(f"offline cache miss for model {self.model_id}: {texts[0]!r}")


@dataclass
class ReplayDecision:
    """One consolidation decision with its rule hits and feature vector."""

    sample_id: str
    source_memory_id: str
    action: str
    score: float
    reason: str
    rule_hits: list[str]
    features: dict[str, Any]
    source_metadata: dict[str, Any]
    target_memory_id: str | None
    target_metadata: dict[str, Any] | None

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_memory_id": self.source_memory_id,
            "action": self.action,
            "score": self.score,
            "reason": self.reason,
            "rule_hits": list(self.rule_hits),
            "features": self.features,
            "source_metadata": dict(self.source_metadata),
            "target_memory_id": self.target_memory_id,
            "target_metadata": dict(self.target_metadata) if self.target_metadata else None,
        }


@dataclass
class SampleReplay:
    """Replayed stores and decision dump for one sample."""

    sample_id: str
    actions: Counter[str]
    decisions: list[ReplayDecision] = field(default_factory=list)
    snapshot_event_count: int = 0
    etec_memory_count: int = 0
    raw_memory_count: int = 0
    snapshot_events_with_fact_slot: int = 0
    snapshot_events_with_fact_value: int = 0
    etec_store: InMemoryMemoryRepository | None = None
    raw_store: InMemoryMemoryRepository | None = None

    def decisions_json(self) -> list[dict[str, Any]]:
        return [decision.to_json() for decision in self.decisions]


def build_offline_embedding(base_run_dir: Path) -> EmbeddingModel:
    """Return a strict-offline embedding model reading the base run's cache.

    The cached model id is taken from the base run manifest so cache keys
    (which include the model id) match the keys written during the run.
    """
    manifest = require_manifest(base_run_dir)
    if manifest.embedding.kind == "deterministic_fake":
        return DeterministicFakeEmbeddingModel()
    cache_root = base_run_dir / "model_cache"
    if not cache_root.exists():
        raise FileNotFoundError(f"base run model cache missing: {cache_root}")
    return CachedEmbeddingModel(
        _OfflineOnlyEmbedding(manifest.embedding.model_id),
        FileModelCache(cache_root),
    )


def replay_sample(
    snapshot: ExtractionSnapshot,
    *,
    embedding: EmbeddingModel,
    user_id: str,
) -> SampleReplay:
    """Rebuild raw + ETEC stores from one snapshot and dump consolidation decisions.

    The ETEC store is rebuilt by applying the consolidator in the same order
    as ``materialize_event_store`` (sorted by event time, then content) so the
    action counts are exactly reproducible.
    """
    candidates = sorted(
        snapshot.events,
        key=lambda memory: (memory.event_time or datetime.min.replace(tzinfo=UTC), memory.content),
    )
    repository = InMemoryMemoryRepository()
    consolidator = ETECConsolidator(embedding)
    actions: Counter[str] = Counter()
    decisions: list[ReplayDecision] = []
    for memory in candidates:
        result = consolidator.apply(repository, memory)
        actions[result.decision.action.value] += 1
        decisions.append(
            _decision_record(
                snapshot.conversation_id, memory, result.decision, repository
            )
        )

    raw_store, _ = materialize_event_store(snapshot, apply_etec=False, user_id=user_id)
    fact_slot_count = sum(1 for event in snapshot.events if _has_fact_slot(event))
    fact_value_count = sum(1 for event in snapshot.events if _has_fact_value(event))
    return SampleReplay(
        sample_id=snapshot.conversation_id,
        actions=actions,
        decisions=decisions,
        snapshot_event_count=snapshot.event_count,
        etec_memory_count=len(repository.list_for_user(user_id)),
        raw_memory_count=len(raw_store.list_for_user(user_id)),
        snapshot_events_with_fact_slot=fact_slot_count,
        snapshot_events_with_fact_value=fact_value_count,
        etec_store=repository,
        raw_store=raw_store,
    )


def _decision_record(
    sample_id: str,
    source: MemoryRecord,
    decision: ETECDecision,
    repository: InMemoryMemoryRepository,
) -> ReplayDecision:
    target: MemoryRecord | None = None
    if decision.target_memory_id is not None:
        target = repository.get(decision.target_memory_id)
    return ReplayDecision(
        sample_id=sample_id,
        source_memory_id=str(source.memory_id),
        action=decision.action.value,
        score=decision.score,
        reason=decision.reason,
        rule_hits=list(decision.rule_hits),
        features=decision.features.model_dump(mode="json"),
        source_metadata=dict(source.metadata),
        target_memory_id=str(decision.target_memory_id) if decision.target_memory_id else None,
        target_metadata=dict(target.metadata) if target is not None else None,
    )


def _has_fact_slot(memory: MemoryRecord) -> bool:
    return isinstance(memory.metadata.get("fact_slot"), str) and bool(memory.metadata["fact_slot"])


def _has_fact_value(memory: MemoryRecord) -> bool:
    value = memory.metadata.get("fact_value")
    return isinstance(value, str) and bool(value)


def replay_run(
    run_dir: Path,
    *,
    base_run_dir: Path | None = None,
    sample_ids: Sequence[str] | None = None,
) -> dict[str, SampleReplay]:
    """Replay every (selected) sample of a finalized run.

    ``base_run_dir`` provides the model cache; it defaults to ``run_dir``
    itself (runs carry their own cache).
    """
    load_finalized(run_dir)
    cache_dir = base_run_dir or run_dir
    embedding = build_offline_embedding(cache_dir)
    wanted = set(sample_ids) if sample_ids is not None else None
    samples_dir = run_dir / "samples"
    replays: dict[str, SampleReplay] = {}
    for path in sorted(samples_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        if ".extraction_snapshot.json" in path.name:
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        sample_id = sample["sample_id"]
        if wanted is not None and sample_id not in wanted:
            continue
        snapshot_path = samples_dir / f"{path.stem}.extraction_snapshot.json"
        if not snapshot_path.exists():
            raise FileNotFoundError(f"missing extraction snapshot: {snapshot_path}")
        snapshot = ExtractionSnapshot.model_validate(
            json.loads(snapshot_path.read_text(encoding="utf-8"))
        )
        replays[sample_id] = replay_sample(snapshot, embedding=embedding, user_id=sample_id)
    if wanted is not None:
        missing = sorted(wanted - set(replays))
        if missing:
            raise ValueError(f"run {run_dir.name} is missing requested samples: {missing}")
    return replays


def ingestion_actions(sample_json_path: Path) -> dict[str, int]:
    payload = json.loads(sample_json_path.read_text(encoding="utf-8"))
    return dict(payload["ingestion"]["etec"]["actions"])


def actions_match(replay: SampleReplay, sample_json_path: Path) -> bool:
    return dict(replay.actions) == ingestion_actions(sample_json_path)


__all__ = [
    "OfflineCacheMiss",
    "ReplayDecision",
    "SampleReplay",
    "actions_match",
    "build_offline_embedding",
    "ingestion_actions",
    "replay_run",
    "replay_sample",
]
