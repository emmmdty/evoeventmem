"""M13 LongMemEval Small experiment runner.

Runs six fair, resumable methods on LongMemEval records:

- ``no_memory``: question-only prompt (M04 builder).
- ``full_context``: raw sessions truncated to the shared token budget (M04 builder).
- ``vector_rag``: event memories without ETEC, retrieved with ``FIXED_VECTOR``.
- ``event_no_etec``: event memories without ETEC, retrieved with ``QEMR``.
- ``etec``: ETEC-consolidated memories, retrieved with ``FIXED_VECTOR``.
- ``full``: ETEC-consolidated memories, retrieved with ``QEMR``.

All memory methods share one :class:`RetrievalHarness` with the same token
budget, max items per source, and max candidates per source (M12 handoff).
Every question passes its ``asked_at`` timestamp as ``reference_time``.

Resume: per-sample results are written once (atomic link) to
``<run_dir>/samples/<sample_id>.json``; re-runs skip completed samples.
Retry a sample by deleting its file and re-running with ``--resume-dir``.
The combined per-method ``predictions.jsonl`` / ``samples.jsonl`` /
``retrieval.jsonl`` files and ``summary.json`` are derived artifacts and are
regenerated from the immutable per-sample files at the end of every run.

Usage::

    uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/smoke.toml
    uv run python -m benchmarks.longmemeval.run \\
        --config configs/longmemeval/main.toml --validate-config
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tomllib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from benchmarks.common.artifacts import (
    EvidencePrediction,
    PredictionRecord,
    SampleEvaluation,
    current_git_commit,
    write_json_write_once,
    write_jsonl_write_once,
)
from benchmarks.common.metrics import compute_answer_metrics, compute_evidence_metrics
from benchmarks.common.normalization import (
    NormalizedQuestion,
    NormalizedRecord,
    NormalizedSession,
    iter_longmemeval_records,
)
from benchmarks.context_baselines import FullContextBuilder, NoMemoryContextBuilder
from evoeventmem.consolidation import ETECConsolidator
from evoeventmem.core.ports import ChatMessage, ChatModel, EmbeddingModel
from evoeventmem.extraction import ExtractionInput, RuleEventExtractor
from evoeventmem.infra.in_memory_repository import InMemoryMemoryRepository
from evoeventmem.infra.openai_compatible import (
    OpenAICompatibleChatClient,
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingClient,
)
from evoeventmem.models.cache import CachedChatModel, CachedEmbeddingModel, FileModelCache
from evoeventmem.models.fakes import DeterministicFakeChatModel, DeterministicFakeEmbeddingModel
from evoeventmem.retrieval import (
    POLICY_NAME as RETRIEVAL_POLICY_NAME,
)
from evoeventmem.retrieval import (
    PackedItem,
    QEMRRetrievalResult,
    RetrievalHarness,
    RetrievalStrategy,
)
from evoeventmem.router import POLICY_NAME as ROUTER_POLICY_NAME
from evoeventmem.services.memory_service import (
    MemoryService,
    MemoryWriteCandidate,
    MemoryWriteRequest,
)

DEFAULT_OUTPUT_ROOT = Path("artifacts/m13_longmemeval")
EXTRACTION_PROMPT_VERSION = RuleEventExtractor.PROMPT_VERSION
CONSOLIDATION_POLICY_NAME = ETECConsolidator.POLICY_NAME

OFFICIAL_ABILITIES = (
    "information-extraction",
    "multi-session-reasoning",
    "knowledge-update",
    "temporal-reasoning",
    "abstention",
)
CATEGORY_BY_QUESTION_TYPE = {
    "single-session-user": "information-extraction",
    "single-session-preference": "information-extraction",
    "single-session-assistant": "information-extraction",
    "multi-session": "multi-session-reasoning",
    "knowledge-update": "knowledge-update",
    "temporal-reasoning": "temporal-reasoning",
    "abstention": "abstention",
}


class Method(StrEnum):
    NO_MEMORY = "no_memory"
    FULL_CONTEXT = "full_context"
    VECTOR_RAG = "vector_rag"
    EVENT_NO_ETEC = "event_no_etec"
    ETEC = "etec"
    FULL = "full"


_METHOD_STRATEGY: dict[Method, RetrievalStrategy] = {
    Method.VECTOR_RAG: RetrievalStrategy.FIXED_VECTOR,
    Method.EVENT_NO_ETEC: RetrievalStrategy.QEMR,
    Method.ETEC: RetrievalStrategy.FIXED_VECTOR,
    Method.FULL: RetrievalStrategy.QEMR,
}
_METHOD_APPLIES_ETEC = frozenset({Method.ETEC, Method.FULL})
_CONTEXT_METHODS = frozenset({Method.NO_MEMORY, Method.FULL_CONTEXT})


class LiveProviderConfig(BaseModel):
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    chat_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    timeout_s: float = Field(default=60.0, gt=0)


class LongMemEvalConfig(BaseModel):
    schema_version: Literal["longmemeval.config.v1"] = "longmemeval.config.v1"
    run_id_prefix: str = Field(min_length=1)
    dataset_path: Path
    methods: list[Method] = Field(default_factory=lambda: list(Method))
    provider: Literal["deterministic_fake", "openai_compatible"] = "deterministic_fake"
    chat_model_id: str = Field(default="deterministic-local-fake", min_length=1)
    embedding_model_id: str = Field(default="deterministic-local-embedding", min_length=1)
    max_input_tokens: int = Field(gt=0)
    max_candidates_per_source: int = Field(ge=1)
    max_items_per_source: int = Field(ge=1)
    sample_limit: int | None = Field(default=None, ge=1)
    live_provider: LiveProviderConfig | None = None

    @model_validator(mode="after")
    def require_live_provider(self) -> LongMemEvalConfig:
        if self.provider == "openai_compatible" and self.live_provider is None:
            raise ValueError("openai_compatible provider requires explicit live_provider config")
        return self


class MethodSampleRecord(BaseModel):
    schema_version: Literal["longmemeval.method-sample.v1"] = "longmemeval.method-sample.v1"
    method: Method
    prediction: str
    exact_match: float = Field(ge=0.0, le=1.0)
    token_f1: float = Field(ge=0.0, le=1.0)
    evidence_precision: float = Field(ge=0.0, le=1.0)
    evidence_recall: float = Field(ge=0.0, le=1.0)
    evidence_f1: float = Field(ge=0.0, le=1.0)
    predicted_evidence: list[EvidencePrediction] = Field(default_factory=list)
    question_latency_ms: float = Field(ge=0.0)
    search_latency_ms: float = Field(ge=0.0)
    write_latency_ms: float | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    model_cache_key: str | None = None
    retrieval: dict[str, Any] | None = None
    context: dict[str, Any] | None = None


class SampleResult(BaseModel):
    schema_version: Literal["longmemeval.sample.v1"] = "longmemeval.sample.v1"
    dataset: str
    sample_id: str
    question_id: str
    question_type: str | None
    category: str | None
    session_count: int = Field(ge=1)
    turn_count: int = Field(ge=1)
    ingestion: dict[str, Any] = Field(default_factory=dict)
    methods: dict[str, MethodSampleRecord] = Field(default_factory=dict)


class EfficiencyMetrics(BaseModel):
    p50_write_latency_ms: float | None = None
    p95_write_latency_ms: float | None = None
    p50_search_latency_ms: float | None = None
    p95_search_latency_ms: float | None = None
    tokens_per_query: float | None = None
    llm_calls_per_query: float | None = None


class CategoryMetrics(BaseModel):
    sample_count: int = Field(ge=0)
    exact_match: float = Field(ge=0.0, le=1.0)
    token_f1: float = Field(ge=0.0, le=1.0)
    evidence_precision: float = Field(ge=0.0, le=1.0)
    evidence_recall: float = Field(ge=0.0, le=1.0)
    evidence_f1: float = Field(ge=0.0, le=1.0)


class MethodSummary(BaseModel):
    sample_count: int = Field(ge=0)
    exact_match: float = Field(ge=0.0, le=1.0)
    token_f1: float = Field(ge=0.0, le=1.0)
    evidence_precision: float = Field(ge=0.0, le=1.0)
    evidence_recall: float = Field(ge=0.0, le=1.0)
    evidence_f1: float = Field(ge=0.0, le=1.0)
    categories: dict[str, CategoryMetrics] = Field(default_factory=dict)
    efficiency: EfficiencyMetrics = Field(default_factory=EfficiencyMetrics)


class SampleValidation(BaseModel):
    expected_sample_count: int = Field(ge=0)
    completed_sample_count: int = Field(ge=0)
    missing_sample_ids: list[str] = Field(default_factory=list)
    duplicate_sample_ids: list[str] = Field(default_factory=list)
    valid: bool


class LongMemEvalSummary(BaseModel):
    schema_version: Literal["longmemeval.summary.v1"] = "longmemeval.summary.v1"
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    git_commit: str = Field(min_length=1)
    git_dirty: bool
    config_hash: str = Field(min_length=1)
    dataset_hash: str = Field(min_length=1)
    dataset_path: str
    chat_model_id: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    extraction_prompt_version: str = Field(min_length=1)
    retrieval_policy_name: str = Field(min_length=1)
    router_policy_name: str = Field(min_length=1)
    consolidation_policy_name: str = Field(min_length=1)
    max_input_tokens: int = Field(gt=0)
    max_candidates_per_source: int = Field(ge=1)
    max_items_per_source: int = Field(ge=1)
    sample_validation: SampleValidation
    methods: dict[str, MethodSummary] = Field(default_factory=dict)


def load_config(path: Path) -> LongMemEvalConfig:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return LongMemEvalConfig.model_validate(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the M13 LongMemEval experiment.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--resume-dir", type=Path, default=None)
    parser.add_argument("--sample-ids", nargs="*", default=None)
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.validate_config:
        print(json.dumps(_config_report(config, args.config), indent=2, sort_keys=True))
        return 0

    run_dir = (
        args.resume_dir
        if args.resume_dir is not None
        else _new_run_dir(args.output_root, config.run_id_prefix)
    )
    summary = run_experiment(config, run_dir, sample_ids=args.sample_ids)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def run_experiment(
    config: LongMemEvalConfig,
    run_dir: Path,
    *,
    sample_ids: Sequence[str] | None = None,
) -> LongMemEvalSummary:
    run_dir.mkdir(parents=True, exist_ok=True)
    config_payload = config.model_dump(mode="json")
    config_path = run_dir / "config.json"
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if existing != config_payload:
            raise ValueError(f"run dir {run_dir} was created with a different config")
    else:
        write_json_write_once(config_path, config_payload)

    chat_model, embedding_model = _make_models(config, run_dir)
    records = _apply_sample_ids(_load_records(config), sample_ids)
    expected_sample_ids = [record.sample_id for record in records]

    for record in records:
        _process_sample(record, config, chat_model, embedding_model, run_dir)

    return _write_summary(config, run_dir, expected_sample_ids, chat_model, embedding_model)


def _process_sample(
    record: NormalizedRecord,
    config: LongMemEvalConfig,
    chat_model: ChatModel,
    embedding_model: EmbeddingModel,
    run_dir: Path,
) -> SampleResult:
    sample_path = _sample_path(run_dir, record.sample_id)
    if sample_path.exists():
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        return SampleResult.model_validate(payload)

    user_id = record.sample_id
    question = record.questions[0]
    ordered_record = _order_record(record)
    raw_store, raw_ingestion = _build_memory_store(
        ordered_record, embedding_model, user_id=user_id, apply_etec=False
    )
    etec_store: InMemoryMemoryRepository | None = None
    etec_ingestion: dict[str, Any] = {}
    if _needs_etec(config.methods):
        etec_store, etec_ingestion = _build_memory_store(
            ordered_record, embedding_model, user_id=user_id, apply_etec=True
        )

    methods: dict[str, MethodSampleRecord] = {}
    for method in config.methods:
        if method in _CONTEXT_METHODS:
            record_method = _run_context_method(
                method, question, ordered_record.sessions, config, chat_model
            )
        else:
            store = etec_store if method in _METHOD_APPLIES_ETEC else raw_store
            ingestion = etec_ingestion if method in _METHOD_APPLIES_ETEC else raw_ingestion
            write_latency_ms = float(ingestion.get("write_latency_ms", 0.0))
            record_method = _run_memory_method(
                method,
                question,
                store,
                config,
                chat_model,
                embedding_model,
                user_id=user_id,
                write_latency_ms=write_latency_ms,
            )
        methods[method.value] = record_method

    result = SampleResult(
        dataset=record.dataset,
        sample_id=record.sample_id,
        question_id=question.question_id,
        question_type=question.category,
        category=_category_for(question.category),
        session_count=len(ordered_record.sessions),
        turn_count=sum(len(session.turns) for session in ordered_record.sessions),
        ingestion={
            "raw": raw_ingestion,
            "etec": etec_ingestion if etec_store is not None else None,
        },
        methods=methods,
    )
    write_json_write_once(sample_path, result)
    return result


def _run_context_method(
    method: Method,
    question: NormalizedQuestion,
    sessions: Sequence[NormalizedSession],
    config: LongMemEvalConfig,
    chat_model: ChatModel,
) -> MethodSampleRecord:
    builder = (
        NoMemoryContextBuilder(config.max_input_tokens)
        if method is Method.NO_MEMORY
        else FullContextBuilder(config.max_input_tokens)
    )
    started = perf_counter()
    context = builder.build(question, sessions)
    search_latency_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    response = chat_model.generate([ChatMessage(role="user", content=context.prompt)])
    question_latency_ms = (perf_counter() - started) * 1000
    return _evaluate(
        method=method,
        question=question,
        prediction=response.text,
        evidence=[],
        question_latency_ms=question_latency_ms,
        search_latency_ms=search_latency_ms,
        write_latency_ms=None,
        input_tokens=context.input_tokens,
        output_tokens=response.output_tokens,
        llm_calls=1,
        model_cache_key=response.cache_key,
        retrieval=None,
        context={
            "included_history_turn_ids": list(context.included_history_turn_ids),
            "truncations": [asdict(decision) for decision in context.truncations],
        },
    )


def _run_memory_method(
    method: Method,
    question: NormalizedQuestion,
    store: InMemoryMemoryRepository,
    config: LongMemEvalConfig,
    chat_model: ChatModel,
    embedding_model: EmbeddingModel,
    *,
    user_id: str,
    write_latency_ms: float,
) -> MethodSampleRecord:
    harness = RetrievalHarness(
        store,
        embedding_model,
        max_items_per_source=config.max_items_per_source,
        max_candidates_per_source=config.max_candidates_per_source,
    )
    question_tokens = _count_tokens(f"Question: {question.question}")
    if question_tokens >= config.max_input_tokens:
        fallback = NoMemoryContextBuilder(config.max_input_tokens).build(question, [])
        started = perf_counter()
        response = chat_model.generate([ChatMessage(role="user", content=fallback.prompt)])
        question_latency_ms = (perf_counter() - started) * 1000
        return _evaluate(
            method=method,
            question=question,
            prediction=response.text,
            evidence=[],
            question_latency_ms=question_latency_ms,
            search_latency_ms=0.0,
            write_latency_ms=write_latency_ms,
            input_tokens=fallback.input_tokens,
            output_tokens=response.output_tokens,
            llm_calls=1,
            model_cache_key=response.cache_key,
            retrieval=None,
            context={"fallback": "question_exceeds_budget"},
        )
    budget_tokens = config.max_input_tokens - question_tokens
    started = perf_counter()
    result = harness.retrieve(
        question.question,
        user_id=user_id,
        strategy=_METHOD_STRATEGY[method],
        budget_tokens=budget_tokens,
        reference_time=question.asked_at,
    )
    search_latency_ms = (perf_counter() - started) * 1000
    prompt = _build_prompt(question, result.selected_context)
    started = perf_counter()
    response = chat_model.generate([ChatMessage(role="user", content=prompt)])
    question_latency_ms = (perf_counter() - started) * 1000
    predicted_evidence = _evidence_from_packed_items(result.selected_context)
    return _evaluate(
        method=method,
        question=question,
        prediction=response.text,
        evidence=predicted_evidence,
        question_latency_ms=question_latency_ms,
        search_latency_ms=search_latency_ms,
        write_latency_ms=write_latency_ms,
        input_tokens=_count_tokens(prompt),
        output_tokens=response.output_tokens,
        llm_calls=1,
        model_cache_key=response.cache_key,
        retrieval=_retrieval_payload(result),
        context=None,
    )


def _evaluate(
    *,
    method: Method,
    question: NormalizedQuestion,
    prediction: str,
    evidence: Sequence[EvidencePrediction],
    question_latency_ms: float,
    search_latency_ms: float,
    write_latency_ms: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    llm_calls: int,
    model_cache_key: str | None,
    retrieval: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> MethodSampleRecord:
    answer_metrics = compute_answer_metrics(question.answer, prediction)
    evidence_metrics = compute_evidence_metrics(question.evidence, evidence)
    return MethodSampleRecord(
        method=method,
        prediction=prediction,
        exact_match=answer_metrics.exact_match,
        token_f1=answer_metrics.token_f1,
        evidence_precision=evidence_metrics.precision,
        evidence_recall=evidence_metrics.recall,
        evidence_f1=evidence_metrics.f1,
        predicted_evidence=list(evidence),
        question_latency_ms=question_latency_ms,
        search_latency_ms=search_latency_ms,
        write_latency_ms=write_latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        llm_calls=llm_calls,
        model_cache_key=model_cache_key,
        retrieval=retrieval,
        context=context,
    )


def _build_memory_store(
    record: NormalizedRecord,
    embedding_model: EmbeddingModel,
    *,
    user_id: str,
    apply_etec: bool,
) -> tuple[InMemoryMemoryRepository, dict[str, Any]]:
    request = ExtractionInput.from_normalized_record(record, user_id=user_id)
    started = perf_counter()
    extraction = RuleEventExtractor().extract(request)
    extraction_latency_ms = (perf_counter() - started) * 1000
    candidates = sorted(
        extraction.candidates,
        key=lambda candidate: (
            candidate.memory.event_time or datetime.min.replace(tzinfo=UTC),
            candidate.memory.content,
        ),
    )
    repository = InMemoryMemoryRepository()
    if apply_etec:
        consolidator = ETECConsolidator(embedding_model)
        started = perf_counter()
        actions: Counter[str] = Counter()
        for candidate in candidates:
            applied = consolidator.apply(repository, candidate.memory)
            actions[applied.decision.action.value] += 1
        write_latency_ms = (perf_counter() - started) * 1000
        return repository, {
            "apply_etec": True,
            "extraction_latency_ms": extraction_latency_ms,
            "write_latency_ms": write_latency_ms,
            "candidate_count": len(candidates),
            "memory_count": len(repository.list_for_user(user_id)),
            "actions": dict(actions),
        }

    write_request = MemoryWriteRequest(
        candidates=[
            MemoryWriteCandidate.from_extracted_event(candidate) for candidate in candidates
        ]
    )
    started = perf_counter()
    write_result = MemoryService(repository).write_extracted_events(write_request)
    write_latency_ms = (perf_counter() - started) * 1000
    return repository, {
        "apply_etec": False,
        "extraction_latency_ms": extraction_latency_ms,
        "write_latency_ms": write_latency_ms,
        "candidate_count": len(candidates),
        "memory_count": len(repository.list_for_user(user_id)),
        "write_metrics": write_result.metrics.model_dump(mode="json"),
    }


def _needs_etec(methods: Sequence[Method]) -> bool:
    return any(method in _METHOD_APPLIES_ETEC for method in methods)


def _build_prompt(question: NormalizedQuestion, items: Sequence[PackedItem]) -> str:
    lines = [f"Context: {item.memory.content}" for item in items]
    lines.append(f"Question: {question.question}")
    return "\n".join(lines)


def _evidence_from_packed_items(items: Sequence[PackedItem]) -> list[EvidencePrediction]:
    predicted: list[EvidencePrediction] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        for ref in item.evidence_refs:
            session_id = ref.metadata.get("session_id")
            if session_id is None:
                continue
            session_id_text = str(session_id)
            key = ("longmemeval_session", session_id_text)
            if key in seen:
                continue
            seen.add(key)
            predicted.append(
                EvidencePrediction(
                    source_type="longmemeval_session",
                    source_id=session_id_text,
                    locator="answer_session_ids",
                    quote=ref.quote,
                )
            )
    return predicted


def _retrieval_payload(result: QEMRRetrievalResult) -> dict[str, Any]:
    return {
        "intent": result.intent.value,
        "strategy": result.strategy.value,
        "confidence": result.routing.confidence if result.routing is not None else None,
        "budget_tokens": result.budget_tokens,
        "total_tokens": result.total_tokens,
        "candidate_count": len(result.candidates),
        "exclusion_count": len(result.exclusions),
        "packed_items": [
            {
                "memory_id": str(item.memory.memory_id),
                "content": item.memory.content,
                "final_score": item.final_score,
                "component_scores": item.component_scores,
                "token_count": item.token_count,
                "historical": item.historical,
                "reason": item.reason,
                "evidence_refs": [
                    {
                        "source_type": ref.source_type,
                        "source_id": ref.source_id,
                        "locator": ref.locator,
                        "session_id": ref.metadata.get("session_id"),
                    }
                    for ref in item.evidence_refs
                ],
            }
            for item in result.selected_context
        ],
    }


def _write_summary(
    config: LongMemEvalConfig,
    run_dir: Path,
    expected_sample_ids: Sequence[str],
    chat_model: ChatModel,
    embedding_model: EmbeddingModel,
) -> LongMemEvalSummary:
    samples = _load_sample_results(run_dir)
    validation = _validate_samples(list(expected_sample_ids), [s.sample_id for s in samples])
    methods: dict[str, MethodSummary] = {}
    for method in config.methods:
        methods[method.value] = _summarize_method(method, samples)
    summary = LongMemEvalSummary(
        run_id=run_dir.name,
        git_commit=current_git_commit(),
        git_dirty=_git_is_dirty(),
        config_hash=_hash_json(config.model_dump(mode="json")),
        dataset_hash=_dataset_hash(config.dataset_path),
        dataset_path=str(config.dataset_path),
        chat_model_id=chat_model.model_id,
        embedding_model_id=embedding_model.model_id,
        extraction_prompt_version=EXTRACTION_PROMPT_VERSION,
        retrieval_policy_name=RETRIEVAL_POLICY_NAME,
        router_policy_name=ROUTER_POLICY_NAME,
        consolidation_policy_name=CONSOLIDATION_POLICY_NAME,
        max_input_tokens=config.max_input_tokens,
        max_candidates_per_source=config.max_candidates_per_source,
        max_items_per_source=config.max_items_per_source,
        sample_validation=validation,
        methods=methods,
    )
    _write_combined_artifacts(run_dir, config.methods, samples)
    summary_path = run_dir / "summary.json"
    summary_path.unlink(missing_ok=True)
    write_json_write_once(summary_path, summary)
    return summary


def _load_sample_results(run_dir: Path) -> list[SampleResult]:
    samples_dir = run_dir / "samples"
    samples: list[SampleResult] = []
    if not samples_dir.is_dir():
        return samples
    for path in sorted(samples_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        sample = SampleResult.model_validate(json.loads(path.read_text(encoding="utf-8")))
        samples.append(sample)
    return samples


def _write_combined_artifacts(
    run_dir: Path,
    methods: Sequence[Method],
    samples: Sequence[SampleResult],
) -> None:
    for method in methods:
        pairs = [
            (sample, sample.methods[method.value])
            for sample in samples
            if method.value in sample.methods
        ]
        predictions: list[PredictionRecord] = []
        evaluations: list[SampleEvaluation] = []
        retrievals: list[dict[str, Any]] = []
        for sample, record in pairs:
            predictions.append(
                PredictionRecord(
                    dataset=sample.dataset,
                    sample_id=sample.sample_id,
                    question_id=sample.question_id,
                    prediction=record.prediction,
                    evidence=record.predicted_evidence,
                    latency_ms=record.question_latency_ms,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    metadata={
                        "method": method.value,
                        "question_type": sample.question_type,
                        "category": sample.category,
                        "retrieval": record.retrieval,
                        "model_cache": {"chat_cache_key": record.model_cache_key},
                    },
                )
            )
            evaluations.append(
                SampleEvaluation(
                    dataset=sample.dataset,
                    sample_id=sample.sample_id,
                    question_id=sample.question_id,
                    exact_match=record.exact_match,
                    token_f1=record.token_f1,
                    evidence_precision=record.evidence_precision,
                    evidence_recall=record.evidence_recall,
                    evidence_f1=record.evidence_f1,
                    latency_ms=record.question_latency_ms,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                )
            )
            if record.retrieval is not None:
                retrievals.append(
                    {
                        "dataset": sample.dataset,
                        "sample_id": sample.sample_id,
                        "question_id": sample.question_id,
                        **record.retrieval,
                    }
                )
        method_dir = run_dir / method.value
        _rewrite_jsonl(method_dir / "predictions.jsonl", predictions)
        _rewrite_jsonl(method_dir / "samples.jsonl", evaluations)
        _rewrite_jsonl(method_dir / "retrieval.jsonl", retrievals)


def _summarize_method(method: Method, samples: Sequence[SampleResult]) -> MethodSummary:
    records = [sample.methods[method.value] for sample in samples if method.value in sample.methods]
    category_samples: dict[str, list[MethodSampleRecord]] = {}
    for sample in samples:
        if method.value in sample.methods:
            category_samples.setdefault(sample.category or "unmapped", []).append(
                sample.methods[method.value]
            )

    category_metrics: dict[str, CategoryMetrics] = {}
    for ability in OFFICIAL_ABILITIES:
        ability_records = category_samples.get(ability, [])
        if ability_records:
            category_metrics[ability] = _category_metrics(ability_records)
    unmapped = category_samples.get("unmapped", [])
    if unmapped:
        category_metrics["unmapped"] = _category_metrics(unmapped)

    return MethodSummary(
        sample_count=len(records),
        exact_match=_mean([record.exact_match for record in records]),
        token_f1=_mean([record.token_f1 for record in records]),
        evidence_precision=_mean([record.evidence_precision for record in records]),
        evidence_recall=_mean([record.evidence_recall for record in records]),
        evidence_f1=_mean([record.evidence_f1 for record in records]),
        categories=category_metrics,
        efficiency=_efficiency(method, records),
    )


def _category_metrics(records: Sequence[MethodSampleRecord]) -> CategoryMetrics:
    return CategoryMetrics(
        sample_count=len(records),
        exact_match=_mean([record.exact_match for record in records]),
        token_f1=_mean([record.token_f1 for record in records]),
        evidence_precision=_mean([record.evidence_precision for record in records]),
        evidence_recall=_mean([record.evidence_recall for record in records]),
        evidence_f1=_mean([record.evidence_f1 for record in records]),
    )


def _efficiency(method: Method, records: Sequence[MethodSampleRecord]) -> EfficiencyMetrics:
    write_latencies: list[float] = []
    if method not in _CONTEXT_METHODS:
        write_latencies = [
            record.write_latency_ms for record in records if record.write_latency_ms is not None
        ]
    search_latencies = [record.search_latency_ms for record in records]
    input_tokens = [record.input_tokens for record in records if record.input_tokens is not None]
    llm_calls = [record.llm_calls for record in records]
    return EfficiencyMetrics(
        p50_write_latency_ms=_percentile(write_latencies, 0.50),
        p95_write_latency_ms=_percentile(write_latencies, 0.95),
        p50_search_latency_ms=_percentile(search_latencies, 0.50),
        p95_search_latency_ms=_percentile(search_latencies, 0.95),
        tokens_per_query=_mean(input_tokens) if input_tokens else None,
        llm_calls_per_query=_mean(llm_calls) if llm_calls else None,
    )


def _validate_samples(
    expected_sample_ids: Sequence[str], completed_sample_ids: Sequence[str]
) -> SampleValidation:
    missing = sorted(set(expected_sample_ids) - set(completed_sample_ids))
    duplicates = [
        sample_id
        for sample_id, count in Counter(expected_sample_ids).items()
        if count > 1
    ]
    duplicates.extend(
        sample_id for sample_id, count in Counter(completed_sample_ids).items() if count > 1
    )
    duplicates = sorted(set(duplicates))
    return SampleValidation(
        expected_sample_count=len(expected_sample_ids),
        completed_sample_count=len(completed_sample_ids),
        missing_sample_ids=missing,
        duplicate_sample_ids=duplicates,
        valid=not missing and not duplicates,
    )


def _config_report(config: LongMemEvalConfig, config_path: Path) -> dict[str, Any]:
    report = config.model_dump(mode="json")
    report["config_path"] = str(config_path)
    report["config_hash"] = _hash_json(config.model_dump(mode="json"))
    report["dataset_hash"] = _dataset_hash(config.dataset_path)
    report["git_commit"] = current_git_commit()
    report["extraction_prompt_version"] = EXTRACTION_PROMPT_VERSION
    report["retrieval_policy_name"] = RETRIEVAL_POLICY_NAME
    report["router_policy_name"] = ROUTER_POLICY_NAME
    report["consolidation_policy_name"] = CONSOLIDATION_POLICY_NAME
    if config.live_provider is not None:
        report["live_provider"]["api_key_set"] = bool(
            os.environ.get(config.live_provider.api_key_env)
        )
    return report


def _make_models(config: LongMemEvalConfig, run_dir: Path) -> tuple[ChatModel, EmbeddingModel]:
    cache = FileModelCache(run_dir / "model_cache")
    if config.provider == "deterministic_fake":
        return (
            CachedChatModel(DeterministicFakeChatModel(config.chat_model_id), cache),
            CachedEmbeddingModel(
                DeterministicFakeEmbeddingModel(config.embedding_model_id), cache
            ),
        )
    live = config.live_provider
    if live is None:
        raise ValueError("openai_compatible provider requires explicit live_provider config")
    api_key = os.environ.get(live.api_key_env)
    if not api_key:
        raise RuntimeError(f"missing environment variable {live.api_key_env}")
    live_config = OpenAICompatibleConfig(
        base_url=live.base_url,
        api_key=api_key,
        model=live.chat_model,
        timeout_s=live.timeout_s,
    )
    return (
        CachedChatModel(OpenAICompatibleChatClient(live_config), cache),
        CachedEmbeddingModel(OpenAICompatibleEmbeddingClient(live_config), cache),
    )


def _load_records(config: LongMemEvalConfig) -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    for record in iter_longmemeval_records(config.dataset_path):
        records.append(record)
        if config.sample_limit is not None and len(records) >= config.sample_limit:
            break
    return records


def _apply_sample_ids(
    records: Sequence[NormalizedRecord], sample_ids: Sequence[str] | None
) -> list[NormalizedRecord]:
    if sample_ids is None:
        return list(records)
    wanted = set(sample_ids)
    filtered = [record for record in records if record.sample_id in wanted]
    found = {record.sample_id for record in filtered}
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"sample_ids not found in dataset: {missing}")
    return filtered


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


def _sample_path(run_dir: Path, sample_id: str) -> Path:
    safe_id = _SAFE_ID_RE.sub("_", sample_id)
    return run_dir / "samples" / f"{safe_id}.json"


def _category_for(question_type: str | None) -> str | None:
    if question_type is None:
        return None
    return CATEGORY_BY_QUESTION_TYPE.get(question_type)


def _rewrite_jsonl(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    path.unlink(missing_ok=True)
    write_jsonl_write_once(path, records)


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[int(position)])
    return float(ordered[lower] * (upper - position) + ordered[upper] * (position - lower))


def _count_tokens(text: str) -> int:
    return len(text.split())


def _hash_json(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _dataset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def _new_run_dir(output_root: Path, run_id_prefix: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / f"{run_id_prefix}-{timestamp}"
    run_dir.mkdir()
    return run_dir


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


if __name__ == "__main__":
    raise SystemExit(main())
