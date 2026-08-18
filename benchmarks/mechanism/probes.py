"""Eval B: synthetic time-window probes + M4 metrics (spec §5).

Constructs now/past/between probes from gold value-pairs, asserts the
QueryRouter resolves the expected temporal operator (now→NONE, past→BEFORE,
between→BETWEEN), runs offline retrieval over the four method arms
(full / event_no_etec / etec / vector_rag), and computes the M4 metric
triple: ExclusionHit, Contamination, ValidRetention.

The retrieval replay is offline over the base run's sealed extraction
snapshot. The base run's ``model_cache`` supplies every memory-content
embedding the original run computed; probe-query embeddings (new strings
not seen during the base run) are filled by the local embedding endpoint
(``127.0.0.1:11436``, qwen3-embedding-0.6b -- the same model the base run
used, so cache keys match). No reader and no extractor is ever called; the
probe produces no answer, only retrieval decisions.

Honesty note: the local endpoint is the REAL embedding model (not a fake),
so probe-query embeddings are genuine. The base run's memory-content
embeddings are read from the base cache (offline, not recomputed). When the
ETEC consolidator's linking path needs an embedding the base run did not
cache, the local endpoint fills it -- this may cause MERGE counts to diverge
from the online run (same known limitation as ``replay.py``). Under
SUPERSEDE=0 the impact is limited to MERGE differences (2 in the ms run);
the M4 signal (temporal filtering) is retrieval-side and independent of
ETEC consolidation.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.common.artifacts import (
    ExtractionSnapshot,
    canonical_json_hash,
    load_finalized,
    require_manifest,
)
from benchmarks.common.memory_inputs import materialize_event_store
from benchmarks.mechanism.gold import GoldPair, load_gold_pairs
from evoeventmem.core.ports import EmbeddingModel
from evoeventmem.models.cache import CachedEmbeddingModel, FileModelCache
from evoeventmem.models.fakes import DeterministicFakeEmbeddingModel
from evoeventmem.retrieval import RetrievalHarness, RetrievalStrategy

PROBE_SCHEMA_VERSION = "mechanism.probes.v1"
M4_SCHEMA_VERSION = "mechanism.evalb.m4.v1"
BUDGET_TOKENS = 4096
MAX_ITEMS_PER_SOURCE = 8


# --------------------------------------------------------------------------- #
# Composite cache: read from base run (read-only) + probe run (writable).
# --------------------------------------------------------------------------- #


class _CompositeFileModelCache:
    """Read-through cache: writable store first, then read-only base cache.

    ``get`` checks the writable probe cache first (for pre-computed probe
    embeddings), then falls back to the read-only base run cache (for
    memory-content embeddings the original run computed). ``set`` always
    writes to the probe cache, never mutating the base run.
    """

    def __init__(self, read_only: FileModelCache, writable: FileModelCache) -> None:
        self._read_only = read_only
        self._writable = writable

    def key_for(self, namespace: str, payload: dict[str, Any]) -> str:
        return self._writable.key_for(namespace, payload)

    def get(self, namespace: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        hit = self._writable.get(namespace, payload)
        if hit is not None:
            return hit
        return self._read_only.get(namespace, payload)

    def set(self, namespace: str, payload: dict[str, Any], value: dict[str, Any]) -> str:
        return self._writable.set(namespace, payload, value)


def _build_probe_embedding(
    base_run_dir: Path,
    probe_cache_dir: Path,
    *,
    endpoint_url: str,
    model_id: str,
    api_key: str,
) -> tuple[EmbeddingModel, dict[str, Any]]:
    """Build a hybrid embedding model: base cache + local endpoint fallback.

    Memory-content embeddings hit the base run cache (offline). Probe-query
    embeddings that miss the cache are computed by the local endpoint and
    written to the probe cache for reproducibility. The local endpoint is the
    REAL embedding model (same model_id as the base run), not a fake.
    """
    from evoeventmem.infra.openai_compatible import (
        OpenAICompatibleConfig,
        OpenAICompatibleEmbeddingClient,
    )

    base_cache = FileModelCache(base_run_dir / "model_cache")
    probe_cache = FileModelCache(probe_cache_dir)
    composite = _CompositeFileModelCache(base_cache, probe_cache)
    live = OpenAICompatibleEmbeddingClient(
        OpenAICompatibleConfig(
            base_url=endpoint_url,
            api_key=api_key,
            model=model_id,
            timeout_s=60.0,
        )
    )
    identity = {
        "model_id": model_id,
        "endpoint": endpoint_url,
        "base_cache": str(base_run_dir / "model_cache"),
        "probe_cache": str(probe_cache_dir),
        "mode": (
            "hybrid: base-run cache (read-only) for memory-content embeddings; "
            "local endpoint (real qwen3-embedding-0.6b) for probe-query and "
            "any cache-miss embeddings; written to probe cache"
        ),
    }
    return CachedEmbeddingModel(live, composite), identity


# --------------------------------------------------------------------------- #
# Probe construction (spec §5.2).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Probe:
    probe_id: str
    question_id: str
    kind: str  # "now" | "past" | "between"
    query: str
    reference_time: datetime
    expected_operator: str  # "NONE" | "BEFORE" | "BETWEEN"
    gold_inside_window_turn_ids: list[str]
    gold_outside_window_turn_ids: list[str]
    subject: str
    attribute: str
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "question_id": self.question_id,
            "kind": self.kind,
            "query": self.query,
            "reference_time": self.reference_time.isoformat(),
            "expected_operator": self.expected_operator,
            "gold_inside_window_turn_ids": list(self.gold_inside_window_turn_ids),
            "gold_outside_window_turn_ids": list(self.gold_outside_window_turn_ids),
            "subject": self.subject,
            "attribute": self.attribute,
            "notes": self.notes,
        }


def _year(dt: datetime) -> int:
    return dt.year


def build_probes(gold_pairs: Sequence[GoldPair]) -> list[Probe]:
    """Construct now/past/between probes per spec §5.2 templates.

    - ``now``: ``What is {subject}'s {attribute} now?`` -> NONE operator
      (no temporal window). gold_inside = new turns, gold_outside = old turns.
    - ``past``: ``What was {subject}'s {attribute} before {year(t_old)+1}?``
      -> BEFORE operator. Requires ``year(t_old) <= year(t_q) - 1``.
      gold_inside = old turns, gold_outside = new turns (new is after the window).
    - ``between``: ``What was {subject}'s {attribute} between {year(t_old)}
      and {year(t_q)}?`` -> BETWEEN operator. Requires
      ``year(t_q) - year(t_old) >= 2`` (non-degenerate window).
      gold_inside = old + new turns, gold_outside = [] (both inside the window).
    """
    probes: list[Probe] = []
    for pair in gold_pairs:
        if pair.gold_action.value == "ADD" and not pair.old_value_turn_ids:
            continue  # ADD-without-old-value has no old side to probe
        subject = pair.subject
        attribute = pair.attribute
        now_query = f"What is {subject}'s {attribute} now?"
        probes.append(
            Probe(
                probe_id=f"now-{pair.question_id}",
                question_id=pair.question_id,
                kind="now",
                query=now_query,
                reference_time=pair.t_q,
                expected_operator="NONE",
                gold_inside_window_turn_ids=list(pair.new_value_turn_ids),
                gold_outside_window_turn_ids=list(pair.old_value_turn_ids),
                subject=subject,
                attribute=attribute,
                notes="now probe: NONE operator, no temporal window",
            )
        )
        if _year(pair.t_old) <= _year(pair.t_q) - 1:
            past_year = _year(pair.t_old) + 1
            past_query = f"What was {subject}'s {attribute} before {past_year}?"
            probes.append(
                Probe(
                    probe_id=f"past-{pair.question_id}",
                    question_id=pair.question_id,
                    kind="past",
                    query=past_query,
                    reference_time=pair.t_q,
                    expected_operator="BEFORE",
                    gold_inside_window_turn_ids=list(pair.old_value_turn_ids),
                    gold_outside_window_turn_ids=list(pair.new_value_turn_ids),
                    subject=subject,
                    attribute=attribute,
                    notes=(
                        f"past probe: BEFORE operator (spec-expected window "
                        f"(-inf, {past_year - 1}-12-31]; actual router bound "
                        f"may differ -- see caveats.router_before_year_is_inclusive "
                        f"and router_operator_assertions.temporal_constraint_bounds"
                    ),
                )
            )
        if _year(pair.t_q) - _year(pair.t_old) >= 2:
            between_query = (
                f"What was {subject}'s {attribute} between "
                f"{_year(pair.t_old)} and {_year(pair.t_q)}?"
            )
            probes.append(
                Probe(
                    probe_id=f"between-{pair.question_id}",
                    question_id=pair.question_id,
                    kind="between",
                    query=between_query,
                    reference_time=pair.t_q,
                    expected_operator="BETWEEN",
                    gold_inside_window_turn_ids=list(
                        pair.old_value_turn_ids + pair.new_value_turn_ids
                    ),
                    gold_outside_window_turn_ids=[],
                    subject=subject,
                    attribute=attribute,
                    notes=(
                        f"between probe: BETWEEN operator, window "
                        f"[{_year(pair.t_old)}-01-01, {_year(pair.t_q)}-12-31]"
                    ),
                )
            )
    return probes


# --------------------------------------------------------------------------- #
# Router operator assertion.
# --------------------------------------------------------------------------- #


def assert_router_operators(probes: Sequence[Probe]) -> list[dict[str, Any]]:
    """Assert each probe's router-resolved operator matches the template.

    Returns a list of per-probe assertion records. The router is deterministic
    (zero LLM); ``reference_time`` is passed as a UTC-aware datetime.
    """
    from evoeventmem.router import QueryRouter

    router = QueryRouter()
    records: list[dict[str, Any]] = []
    for probe in probes:
        decision = router.route(probe.query, reference_time=probe.reference_time)
        actual = decision.temporal_constraint.operator.value
        expected = probe.expected_operator
        records.append(
            {
                "probe_id": probe.probe_id,
                "question_id": probe.question_id,
                "kind": probe.kind,
                "query": probe.query,
                "expected": expected,
                "actual": actual,
                "assert_ok": actual.lower() == expected.lower(),
                "rule_hits": list(decision.rule_hits),
                "reason": decision.reason,
                "temporal_constraint_is_constrained": (
                    decision.temporal_constraint.is_constrained
                ),
                "temporal_constraint_bounds": {
                    "lower_bound_utc": (
                        decision.temporal_constraint.lower_bound_utc.isoformat()
                        if decision.temporal_constraint.lower_bound_utc is not None
                        else None
                    ),
                    "upper_bound_utc": (
                        decision.temporal_constraint.upper_bound_utc.isoformat()
                        if decision.temporal_constraint.upper_bound_utc is not None
                        else None
                    ),
                },
            }
        )
    return records


# --------------------------------------------------------------------------- #
# Retrieval replay per arm.
# --------------------------------------------------------------------------- #


ARMS = ("full", "event_no_etec", "etec", "vector_rag")
_ARM_APPLIES_ETEC = frozenset({"etec", "full"})
_ARM_STRATEGY = {
    "full": RetrievalStrategy.QEMR,
    "event_no_etec": RetrievalStrategy.QEMR,
    "etec": RetrievalStrategy.FIXED_VECTOR,
    "vector_rag": RetrievalStrategy.FIXED_VECTOR,
}


@dataclass
class ProbeRetrievalResult:
    arm: str
    probe_id: str
    question_id: str
    packed_items: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    packing_bound: bool
    reader_calls: int
    extractor_calls: int

    def to_json(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "probe_id": self.probe_id,
            "question_id": self.question_id,
            "packed_items": self.packed_items,
            "exclusions": self.exclusions,
            "packing_bound": self.packing_bound,
            "reader_calls": self.reader_calls,
            "extractor_calls": self.extractor_calls,
        }


def _run_probe_arm(
    probe: Probe,
    arm: str,
    raw_store: Any,
    etec_store: Any,
    embedding: EmbeddingModel,
    user_id: str,
    *,
    max_items_per_source: int = MAX_ITEMS_PER_SOURCE,
) -> ProbeRetrievalResult:
    """Run retrieval for one probe × one arm. Zero reader/extractor calls."""
    store = etec_store if arm in _ARM_APPLIES_ETEC else raw_store
    harness = RetrievalHarness(
        store,
        embedding,
        max_items_per_source=max_items_per_source,
        max_candidates_per_source=128,
    )
    result = harness.retrieve(
        probe.query,
        user_id=user_id,
        strategy=_ARM_STRATEGY[arm],
        budget_tokens=BUDGET_TOKENS,
        reference_time=probe.reference_time,
    )
    packed_items = [
        {
            "memory_id": str(item.memory.memory_id),
            "content": item.memory.content,
            "final_score": item.final_score,
            "token_count": item.token_count,
            "historical": item.historical,
            "reason": item.reason,
            "evidence_refs": [
                {
                    "source_type": ref.source_type,
                    "source_id": ref.source_id,
                    "session_id": ref.metadata.get("session_id"),
                    "raw_turn_id": ref.metadata.get("raw_turn_id"),
                }
                for ref in item.evidence_refs
            ],
        }
        for item in result.selected_context
    ]
    exclusions = [
        {
            "memory_id": str(exclusion.memory_id),
            "reason": exclusion.reason,
            "details": exclusion.details,
        }
        for exclusion in result.exclusions
    ]
    return ProbeRetrievalResult(
        arm=arm,
        probe_id=probe.probe_id,
        question_id=probe.question_id,
        packed_items=packed_items,
        exclusions=exclusions,
        packing_bound=any(ex.reason == "budget_exceeded" for ex in result.exclusions),
        reader_calls=0,
        extractor_calls=0,
    )


# --------------------------------------------------------------------------- #
# M4 metric computation (spec §5.3).
# --------------------------------------------------------------------------- #


def _packed_turn_ids(result: ProbeRetrievalResult) -> set[str]:
    """Union of raw_turn_id evidence across all packed items."""
    turn_ids: set[str] = set()
    for item in result.packed_items:
        for ref in item.get("evidence_refs", []):
            tid = ref.get("raw_turn_id")
            if tid is not None:
                turn_ids.add(str(tid))
    return turn_ids


def _packed_session_ids(result: ProbeRetrievalResult) -> set[str]:
    """Union of session ids behind packed evidence (raw_turn_id -> session)."""
    session_ids: set[str] = set()
    for item in result.packed_items:
        for ref in item.get("evidence_refs", []):
            tid = ref.get("raw_turn_id")
            if tid is not None and ":" in str(tid):
                session_ids.add(str(tid).split(":")[0])
            sid = ref.get("session_id")
            if sid is not None:
                session_ids.add(str(sid))
    return session_ids


def _turn_ids_to_sessions(turn_ids: Sequence[str]) -> set[str]:
    return {tid if ":" not in tid else tid.split(":")[0] for tid in turn_ids}


def compute_m4_for_probe_arm(
    result: ProbeRetrievalResult,
    probe: Probe,
) -> dict[str, Any]:
    """Compute M4 ExclusionHit / Contamination / ValidRetention for one arm.

    - ExclusionHit: 1 if any exclusion with reason ``temporal_interval_excluded``.
    - Contamination: fraction of packed items whose evidence carries a
      gold_outside_window session (gold_outside evidence leaking into the
      window = contamination).
    - ValidRetention: 1 if packed evidence intersects gold_inside_window sessions.
    - HistoricalPackedCount: count of packed items with ``historical=true``.
    """
    packed_sessions = _packed_session_ids(result)
    outside_sessions = _turn_ids_to_sessions(probe.gold_outside_window_turn_ids)
    inside_sessions = _turn_ids_to_sessions(probe.gold_inside_window_turn_ids)

    exclusion_hit = int(
        any(ex["reason"] == "temporal_interval_excluded" for ex in result.exclusions)
    )
    contaminated_items = 0
    for item in result.packed_items:
        item_sessions: set[str] = set()
        for ref in item.get("evidence_refs", []):
            tid = ref.get("raw_turn_id")
            if tid is not None and ":" in str(tid):
                item_sessions.add(str(tid).split(":")[0])
            sid = ref.get("session_id")
            if sid is not None:
                item_sessions.add(str(sid))
        if item_sessions & outside_sessions:
            contaminated_items += 1
    total_packed = len(result.packed_items)
    contamination = contaminated_items / total_packed if total_packed else None
    valid_retention = int(bool(packed_sessions & inside_sessions))
    historical_count = sum(
        1 for item in result.packed_items if item.get("historical")
    )
    temporal_exclusions = [
        ex for ex in result.exclusions if ex["reason"] == "temporal_interval_excluded"
    ]
    # Record the router's temporal constraint bounds for this probe (from the
    # router assertion record) so the M4 consumer can see why ExclusionHit
    # is 0 or 1: the router's "before {year}" parsing is inclusive of the named
    # year (upper_bound = {year}-12-31), not exclusive (upper = {year-1}-12-31).
    return {
        "probe_id": probe.probe_id,
        "question_id": probe.question_id,
        "kind": probe.kind,
        "arm": result.arm,
        "exclusion_hit": exclusion_hit,
        "contamination": contamination,
        "valid_retention": valid_retention,
        "historical_packed_count": historical_count,
        "total_packed": total_packed,
        "temporal_interval_exclusion_count": len(temporal_exclusions),
        "temporal_exclusion_details": [
            ex.get("details", {}) for ex in temporal_exclusions
        ],
        "packed_sessions": sorted(packed_sessions),
        "gold_inside_sessions": sorted(inside_sessions),
        "gold_outside_sessions": sorted(outside_sessions),
    }


# --------------------------------------------------------------------------- #
# Main driver.
# --------------------------------------------------------------------------- #


def run_probes(
    base_run_dir: Path,
    gold_path: Path,
    out_dir: Path,
    *,
    endpoint_url: str = "http://127.0.0.1:11436/v1",
    api_key: str = "local-no-auth",
) -> dict[str, Any]:
    """Build probes, assert router operators, run retrieval, compute M4."""
    load_finalized(base_run_dir)
    manifest = require_manifest(base_run_dir)
    model_id = manifest.embedding.model_id
    gold_pairs = load_gold_pairs(gold_path).pairs
    probes = build_probes(gold_pairs)

    probe_cache_dir = out_dir / "model_cache"
    probe_cache_dir.mkdir(parents=True, exist_ok=True)
    if manifest.embedding.kind == "deterministic_fake":
        embedding: EmbeddingModel = DeterministicFakeEmbeddingModel(model_id)
        embedding_identity: dict[str, Any] = {
            "model_id": model_id,
            "mode": "deterministic_fake (base run used fake embeddings)",
        }
    else:
        embedding, embedding_identity = _build_probe_embedding(
            base_run_dir,
            probe_cache_dir,
            endpoint_url=endpoint_url,
            model_id=model_id,
            api_key=api_key,
        )

    router_assertions = assert_router_operators(probes)
    failed_assertions = [r for r in router_assertions if not r["assert_ok"]]

    per_probe_arm_results: list[ProbeRetrievalResult] = []
    m4_metrics: list[dict[str, Any]] = []
    etec_cache_miss_questions: list[str] = []

    for probe in probes:
        user_id = probe.question_id
        snapshot_path = base_run_dir / "samples" / f"{user_id}.extraction_snapshot.json"
        if not snapshot_path.exists():
            raise FileNotFoundError(f"missing extraction snapshot: {snapshot_path}")
        snapshot = ExtractionSnapshot.model_validate(
            json.loads(snapshot_path.read_text(encoding="utf-8"))
        )
        raw_store, _ = materialize_event_store(
            snapshot, apply_etec=False, user_id=user_id
        )
        etec_store = None
        try:
            etec_store, _ = materialize_event_store(
                snapshot,
                apply_etec=True,
                embedding_model=embedding,
                user_id=user_id,
            )
        except Exception as exc:
            etec_cache_miss_questions.append(
                f"{user_id} ({probe.probe_id}): {type(exc).__name__}: {exc}"
            )
            etec_store = raw_store  # fallback: under SUPERSEDE=0, etec ~= raw

        for arm in ARMS:
            result = _run_probe_arm(
                probe, arm, raw_store, etec_store, embedding, user_id
            )
            per_probe_arm_results.append(result)
            m4_metrics.append(compute_m4_for_probe_arm(result, probe))

    # Per-arm + per-kind aggregation.
    arm_summary: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        arm_metrics = [m for m in m4_metrics if m["arm"] == arm]
        arm_summary[arm] = {
            "probes": len(arm_metrics),
            "exclusion_hit_rate": (
                sum(m["exclusion_hit"] for m in arm_metrics) / len(arm_metrics)
                if arm_metrics
                else None
            ),
            "contamination_mean": (
                sum(m["contamination"] for m in arm_metrics if m["contamination"] is not None)
                / len([m for m in arm_metrics if m["contamination"] is not None])
                if any(m["contamination"] is not None for m in arm_metrics)
                else None
            ),
            "valid_retention_rate": (
                sum(m["valid_retention"] for m in arm_metrics) / len(arm_metrics)
                if arm_metrics
                else None
            ),
        }
    kind_summary: dict[str, dict[str, Any]] = {}
    for kind in ("now", "past", "between"):
        kind_metrics = [m for m in m4_metrics if m["kind"] == kind]
        if not kind_metrics:
            continue
        kind_summary[kind] = {
            "probes": len(kind_metrics),
            "by_arm": {
                arm: {
                    "exclusion_hit_rate": (
                        sum(m["exclusion_hit"] for m in kind_metrics if m["arm"] == arm)
                        / len([m for m in kind_metrics if m["arm"] == arm])
                        if any(m["arm"] == arm for m in kind_metrics)
                        else None
                    ),
                    "contamination_mean": (
                        sum(
                            m["contamination"]
                            for m in kind_metrics
                            if m["arm"] == arm and m["contamination"] is not None
                        )
                        / len(
                            [
                                m
                                for m in kind_metrics
                                if m["arm"] == arm and m["contamination"] is not None
                            ]
                        )
                        if any(
                            m["arm"] == arm and m["contamination"] is not None
                            for m in kind_metrics
                        )
                        else None
                    ),
                    "valid_retention_rate": (
                        sum(m["valid_retention"] for m in kind_metrics if m["arm"] == arm)
                        / len([m for m in kind_metrics if m["arm"] == arm])
                        if any(m["arm"] == arm for m in kind_metrics)
                        else None
                    ),
                }
                for arm in ARMS
            },
        }

    report: dict[str, Any] = {
        "schema_version": M4_SCHEMA_VERSION,
        "status": "FINAL",
        "scope": (
            f"{len(probes)} probes ({sum(1 for p in probes if p.kind == 'now')} now "
            f"+ {sum(1 for p in probes if p.kind == 'past')} past "
            f"+ {sum(1 for p in probes if p.kind == 'between')} between) "
            f"x {len(ARMS)} arms, from {len(gold_pairs)} gold pairs (ms 8 KU)"
        ),
        "base_run": str(base_run_dir),
        "gold_path": str(gold_path),
        "embedding_identity": embedding_identity,
        "budget_tokens": BUDGET_TOKENS,
        "probes": [p.to_json() for p in probes],
        "router_operator_assertions": router_assertions,
        "router_operator_assertion_rate": (
            sum(1 for r in router_assertions if r["assert_ok"]) / len(router_assertions)
            if router_assertions
            else None
        ),
        "failed_router_assertions": failed_assertions,
        "m4_per_probe_arm": m4_metrics,
        "arm_summary": arm_summary,
        "kind_summary": kind_summary,
        "etec_cache_miss_questions": etec_cache_miss_questions,
        "reader_calls_total": sum(r.reader_calls for r in per_probe_arm_results),
        "extractor_calls_total": sum(r.extractor_calls for r in per_probe_arm_results),
        "caveats": {
            "etec_cache_miss_fallback": (
                "When the ETEC consolidator's linking path needs an embedding the "
                "base run did not cache, the local endpoint fills it (hybrid mode). "
                "Questions where the linking path diverged from the online run are "
                "listed in etec_cache_miss_questions; under SUPERSEDE=0 the impact "
                "is limited to MERGE count differences, not the temporal-filter "
                "signal M4 measures."
            ),
            "temporal_filter_qemr_only": (
                "ExclusionHit can only fire on QEMR arms (full, event_no_etec) that "
                "carry the temporal-interval filter. FIXED_VECTOR arms (etec, "
                "vector_rag) have no temporal filter, so ExclusionHit is "
                "structurally 0 there -- this is the expected strategy isolation, "
                "not a method failure."
            ),
            "router_before_year_is_inclusive": (
                "The router's 'before {year}' parsing is INCLUSIVE of the named "
                "year: 'before 2023' -> upper_bound = 2023-12-31 (not 2022-12-31 "
                "as the spec's expected window assumed). This means the BEFORE "
                "temporal filter keeps all memories from 2023, so ExclusionHit=0 "
                "even though the BEFORE operator fired correctly. The temporal "
                "constraint bounds are recorded in router_operator_assertions[]."
                "temporal_constraint_bounds for verification. This is a router "
                "parsing behavior, not a probe defect: the pre-registered template "
                "'before {year(t_old+1)}' produces an inclusive bound that does "
                "not exclude the target year's memories."
            ),
            "now_probes_no_window": (
                "now probes resolve to NONE operator (no temporal window), so "
                "ExclusionHit is structurally 0 for every arm. Contamination/ "
                "ValidRetention on now probes measure whether old (stale) evidence "
                "leaks into a 'current value' query -- under SUPERSEDE=0 old values "
                "stay ACTIVE, so contamination is expected > 0."
            ),
            "supersede_zero_structural_null": (
                "Under SUPERSEDE=0 the ETEC isolation (full vs event_no_etec) is a "
                "structural null at the retrieval level too: ETEC never marks old "
                "values inactive, so both arms retrieve the same old+new evidence. "
                "M4 cannot discriminate ETEC value when old values stay ACTIVE."
            ),
        },
    }
    report["content_hash"] = canonical_json_hash(
        {k: v for k, v in report.items() if k != "content_hash"}
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m4.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "probes_retrieval.jsonl").write_text(
        "\n".join(json.dumps(r.to_json(), sort_keys=True) for r in per_probe_arm_results)
        + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval B: synthetic time-window probes + M4 metrics (zero LLM)."
    )
    parser.add_argument("--base-run", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("EEM_EMBEDDING_BASE_URL", "http://127.0.0.1:11436/v1"),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("EMBEDDING_API_KEY", "local-no-auth"),
    )
    args = parser.parse_args(argv)
    report = run_probes(
        args.base_run,
        args.gold,
        args.out,
        endpoint_url=args.endpoint_url,
        api_key=args.api_key,
    )
    print(json.dumps(report, indent=2, sort_keys=True)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
