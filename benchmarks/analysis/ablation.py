"""Offline retrieval ablations (M15: evidence, temporal, graph, router, weights, budget).

The ablations re-run the deterministic memory pipeline (rule extraction, write,
ETEC, retrieval) against the immutable main run's ``model_cache`` in strict
offline mode: any embedding lookup that is not already cached raises
:class:`OfflineCacheMiss` instead of hitting the network. No chat model is
ever called; every variant is scored with the deterministic
gold-answer-token-recall proxy from :mod:`benchmarks.analysis.taxonomy`.

Variants (all on the ETEC store unless noted):

- ``weights``: FIXED_VECTOR / FIXED_HYBRID / QEMR(rule router).
- ``router``: QEMR with the intent forced to semantic / temporal / graph /
  hybrid instead of the rule router.
- ``temporal``: QEMR weight profile with the temporal source removed.
- ``graph``: QEMR weight profile with the graph source removed.
- ``evidence``: count of memories excluded for missing evidence refs (the
  constraint is structurally satisfied when every event carries a ref).
- ``budget``: QEMR under 512 / 1024 / 2048 / 4096 token budgets.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.analysis.taxonomy import gold_token_recall
from benchmarks.common.normalization import NormalizedRecord, iter_locomo_records
from evoeventmem.core.ports import EmbeddingModel, EmbeddingResponse
from evoeventmem.extraction import ExtractionInput, RuleEventExtractor
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.retrieval import (
    CandidateSource,
    QEMRRetrievalResult,
    RetrievalHarness,
    RetrievalStrategy,
)
from evoeventmem.router import QueryFeatures, QueryIntent, QueryRoutingDecision
from evoeventmem.services.memory_service import (
    MemoryService,
    MemoryWriteCandidate,
    MemoryWriteRequest,
)

OFFLINE_CACHE = "model_cache/embeddings"
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


class OfflineCacheMiss(RuntimeError):
    """Raised when an embedding lookup is not covered by the run's model cache."""

    def __init__(self, text: str, model_id: str) -> None:
        super().__init__(f"offline cache miss for model {model_id}: {text!r}")


class CachedOnlyEmbeddingModel(EmbeddingModel):
    """Embedding model that only replays the immutable run's cached vectors."""

    def __init__(self, cache_root: Path, model_id: str) -> None:
        self.model_id = model_id
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._load(cache_root / OFFLINE_CACHE)

    def _load(self, directory: Path) -> None:
        if not directory.is_dir():
            raise FileNotFoundError(f"no cached embeddings at {directory}")
        for path in directory.glob("*.json"):
            entry = json.loads(path.read_text(encoding="utf-8"))
            payload = entry.get("input") or {}
            if payload.get("model_id") != self.model_id:
                continue
            output = entry.get("output") or {}
            self._vectors[str(payload.get("text") or "")] = tuple(
                float(value) for value in output.get("vector", [])
            )

    def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResponse]:
        responses: list[EmbeddingResponse] = []
        for text in texts:
            vector = self._vectors.get(text)
            if vector is None:
                raise OfflineCacheMiss(text, self.model_id)
            responses.append(EmbeddingResponse(vector=vector, model_id=self.model_id))
        return responses


class _ForcedIntentRouter:
    """Router stub that always returns one fixed intent (ablation only)."""

    def __init__(self, intent: QueryIntent) -> None:
        self._intent = intent

    def route(self, query: str) -> QueryRoutingDecision:
        return QueryRoutingDecision(
            query=query,
            intent=self._intent,
            confidence=0.7,
            features=QueryFeatures(),
            rule_hits=["forced_intent_ablation"],
            reason="Intent forced for the router ablation.",
        )


class _WeightOverridingHarness(RetrievalHarness):
    """RetrievalHarness whose per-intent weights are supplied by the caller.

    Mirrors ``RetrievalHarness.retrieve`` exactly, except that weight
    resolution is replaced by ``weights_for(intent)`` so that ablations can
    remove a source (e.g., temporal or graph) from a QEMR profile.
    """

    def __init__(
        self,
        repository: InMemoryMemoryRepository,
        embedding_model: EmbeddingModel,
        *,
        weights_for: Callable[[QueryIntent], dict[CandidateSource, float]],
        max_items_per_source: int,
        max_candidates_per_source: int,
        router: Any = None,
    ) -> None:
        super().__init__(
            repository,
            embedding_model,
            router=router,
            max_items_per_source=max_items_per_source,
            max_candidates_per_source=max_candidates_per_source,
        )
        self._weights_for = weights_for

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str | None = None,
        strategy: RetrievalStrategy = RetrievalStrategy.QEMR,
        budget_tokens: int | None = None,
        reference_time: datetime | None = None,
    ) -> QEMRRetrievalResult:
        budget = self._default_budget_tokens if budget_tokens is None else budget_tokens
        if budget < 1:
            raise ValueError("budget_tokens must be at least 1")
        routing = self._router.route(query)
        memories = [
            memory
            for memory in self._repository.list_for_user(user_id)
            if memory.tenant_id == tenant_id
        ]
        if routing.intent is QueryIntent.NO_MEMORY:
            return self._no_memory_result(
                query, user_id, tenant_id, routing, strategy, budget, memories
            )
        weights = self._weights_for(routing.intent)
        reference = _query_reference_datetime(
            query,
            reference_time if reference_time is not None else self._clock(),
        )
        candidates, capped_memory_ids = self._cap_candidates(
            self._collect_candidates(query, routing, memories, reference)
        )
        normalized = self._normalize(candidates)
        scored = self._merge_candidates(normalized, weights, routing.intent)
        eligible, exclusions = self._classify_memories(scored, routing.intent)
        exclusions.extend(self._capped_memory_exclusions(scored, capped_memory_ids))
        selected, packing_exclusions = self._pack(eligible, budget)
        exclusions.extend(packing_exclusions)
        selected_context = self._build_packed_items(selected, routing.intent)
        return QEMRRetrievalResult(
            query=query,
            user_id=user_id,
            tenant_id=tenant_id,
            intent=routing.intent,
            strategy=strategy,
            policy_name=self.POLICY_NAME,
            budget_tokens=budget,
            selected_context=selected_context,
            total_tokens=sum(item.token_count for item in selected_context),
            candidates=scored,
            exclusions=exclusions,
            routing=routing,
        )


def _query_reference_datetime(query: str, now: datetime) -> datetime:
    match = _YEAR_RE.search(query)
    if match is not None:
        return datetime(int(match.group(1)), 1, 1, tzinfo=UTC)
    return now


def _order_record(record: NormalizedRecord) -> NormalizedRecord:
    sessions = sorted(record.sessions, key=lambda session: (session.timestamp, session.session_id))
    sessions = [
        session.model_copy(
            update={
                "turns": sorted(
                    session.turns,
                    key=lambda turn: (turn.timestamp or session.timestamp, turn.turn_id),
                )
            }
        )
        for session in sessions
    ]
    return record.model_copy(update={"sessions": sessions})


def _reference_time(record: NormalizedRecord) -> datetime | None:
    ordered = sorted(record.sessions, key=lambda session: (session.timestamp, session.session_id))
    if not ordered:
        return None
    return ordered[-1].timestamp


def build_etec_store(
    record: NormalizedRecord,
    embedding_model: EmbeddingModel,
    *,
    user_id: str,
) -> tuple[InMemoryMemoryRepository, InMemoryMemoryRepository, dict[str, Any]]:
    """Reproduce the runner's raw + ETEC stores (deterministic, offline)."""
    from evoeventmem.consolidation import ETECConsolidator

    request = ExtractionInput.from_normalized_record(record, user_id=user_id)
    request = request.model_copy(update={"observations": []})
    extraction = RuleEventExtractor().extract(request)
    candidates = sorted(
        extraction.candidates,
        key=lambda candidate: (
            candidate.memory.event_time or datetime.min.replace(tzinfo=UTC),
            candidate.memory.content,
        ),
    )
    raw_repository = InMemoryMemoryRepository()
    write_request = MemoryWriteRequest(
        candidates=[
            MemoryWriteCandidate.from_extracted_event(candidate) for candidate in candidates
        ]
    )
    MemoryService(raw_repository).write_extracted_events(write_request)

    etec_repository = InMemoryMemoryRepository()
    consolidator = ETECConsolidator(embedding_model)
    actions: Counter[str] = Counter()
    for candidate in candidates:
        applied = consolidator.apply(etec_repository, candidate.memory)
        actions[applied.decision.action.value] += 1
    missing_evidence = sum(
        1
        for memory in etec_repository.list_for_user(user_id)
        if not memory.evidence_refs
    )
    return raw_repository, etec_repository, {
        "candidate_count": len(candidates),
        "raw_memory_count": len(raw_repository.list_for_user(user_id)),
        "etec_memory_count": len(etec_repository.list_for_user(user_id)),
        "actions": dict(actions),
        "missing_evidence_memories": missing_evidence,
    }


def ablation_variants(
    *,
    records: Sequence[NormalizedRecord],
    embedding_model: EmbeddingModel,
    max_items_per_source: int,
    max_candidates_per_source: int,
) -> dict[str, dict[str, Any]]:
    """Run every ablation variant and return per-variant summary rows."""

    from evoeventmem.retrieval import QEMR_WEIGHT_PROFILES, resolve_weights

    def profile_without(
        intent: QueryIntent, removed: CandidateSource
    ) -> dict[CandidateSource, float]:
        return {
            source: (0.0 if source is removed else weight)
            for source, weight in QEMR_WEIGHT_PROFILES[intent].items()
        }

    stores: dict[str, InMemoryMemoryRepository] = {}
    store_info: dict[str, dict[str, Any]] = {}
    for record in records:
        user_id = record.sample_id
        ordered = _order_record(record)
        raw_repository, etec_repository, info = build_etec_store(
            ordered, embedding_model, user_id=user_id
        )
        stores[user_id] = etec_repository
        store_info[user_id] = info

    def make_harness(
        store: InMemoryMemoryRepository,
        *,
        weights_for: Callable[[QueryIntent], dict[CandidateSource, float]] | None = None,
        router: Any = None,
    ) -> RetrievalHarness:
        if weights_for is None:
            return RetrievalHarness(
                store,
                embedding_model,
                router=router,
                max_items_per_source=max_items_per_source,
                max_candidates_per_source=max_candidates_per_source,
            )
        return _WeightOverridingHarness(
            store,
            embedding_model,
            weights_for=weights_for,
            max_items_per_source=max_items_per_source,
            max_candidates_per_source=max_candidates_per_source,
            router=router,
        )

    def collect(
        label: str,
        weights_for: Callable[[QueryIntent], dict[CandidateSource, float]] | None = None,
        *,
        budget: int,
        router: Any = None,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        exclusions: Counter[str] = Counter()
        for record in records:
            user_id = record.sample_id
            ordered = _order_record(record)
            reference_time = _reference_time(ordered)
            harness = make_harness(
                stores[user_id], weights_for=weights_for, router=router
            )
            for question in ordered.questions:
                result = harness.retrieve(
                    question.question,
                    user_id=user_id,
                    strategy=RetrievalStrategy.QEMR,
                    budget_tokens=budget,
                    reference_time=reference_time,
                )
                for exclusion in result.exclusions:
                    exclusions[exclusion.reason] += 1
                context = " ".join(item.memory.content for item in result.selected_context)
                rows.append(
                    {
                        "question_id": question.question_id,
                        "recall": gold_token_recall(question.answer, context),
                        "tokens": sum(item.token_count for item in result.selected_context),
                        "intent": result.intent.value,
                        "items": len(result.selected_context),
                    }
                )
        return {"label": label, "rows": rows, "exclusions": dict(exclusions)}

    variants: dict[str, dict[str, Any]] = {}
    variants["qemr"] = collect("qemr", budget=4000)
    variants["weights_fixed_vector"] = collect(
        "weights_fixed_vector",
        weights_for=lambda intent: resolve_weights(RetrievalStrategy.FIXED_VECTOR, intent),
        budget=4000,
    )
    variants["weights_fixed_hybrid"] = collect(
        "weights_fixed_hybrid",
        weights_for=lambda intent: resolve_weights(RetrievalStrategy.FIXED_HYBRID, intent),
        budget=4000,
    )

    for intent in (
        QueryIntent.SEMANTIC,
        QueryIntent.TEMPORAL,
        QueryIntent.GRAPH,
        QueryIntent.HYBRID,
    ):
        variants[f"router_forced_{intent.value}"] = collect(
            f"router_forced_{intent.value}",
            weights_for=lambda target, intent=intent: resolve_weights(
                RetrievalStrategy.QEMR, intent
            ),
            budget=4000,
            router=_ForcedIntentRouter(intent),
        )

    for removed, label in (
        (CandidateSource.TEMPORAL, "no_temporal"),
        (CandidateSource.GRAPH, "no_graph"),
    ):
        variants[label] = collect(
            label,
            weights_for=lambda intent, removed=removed: profile_without(intent, removed),
            budget=4000,
        )

    for budget in (512, 1024, 2048, 4096):
        variants[f"budget_{budget}"] = collect(f"budget_{budget}", budget=budget)

    return {
        "variants": variants,
        "stores": store_info,
    }


def summarize_variant(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    recalls = [row["recall"] for row in rows if row["recall"] is not None]
    tokens = [row["tokens"] for row in rows]
    mean_recall = sum(recalls) / len(recalls) if recalls else 0.0
    return {
        "questions": len(rows),
        "answer_recall": mean_recall,
        "answer_recall_questions": len(recalls),
        "recall_ge_0_5": (
            sum(1 for recall in recalls if recall >= 0.5) / len(recalls) if recalls else 0.0
        ),
        "tokens_mean": sum(tokens) / len(tokens) if tokens else 0.0,
        "items_mean": sum(row["items"] for row in rows) / len(rows) if rows else 0.0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline retrieval ablations.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--model-id", default="qwen3-embedding-0.6b")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-items-per-source", type=int, default=8)
    parser.add_argument("--max-candidates-per-source", type=int, default=128)
    args = parser.parse_args(argv)

    embedding_model = CachedOnlyEmbeddingModel(args.cache_root, args.model_id)
    records = list(iter_locomo_records(args.dataset))
    payload = ablation_variants(
        records=records,
        embedding_model=embedding_model,
        max_items_per_source=args.max_items_per_source,
        max_candidates_per_source=args.max_candidates_per_source,
    )
    output = {
        "dataset": str(args.dataset),
        "cache_root": str(args.cache_root),
        "embedding_model_id": args.model_id,
        "stores": payload["stores"],
        "variants": {
            label: {**summarize_variant(item["rows"]), "exclusions": item["exclusions"]}
            for label, item in sorted(payload["variants"].items())
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
