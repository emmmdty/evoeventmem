"""M13 LongMemEval Small experiment runner (fair raw and event inputs).

Runs six fair, resumable methods on LongMemEval records:

- ``no_memory``: question-only prompt (M04 builder).
- ``full_context``: raw sessions truncated to the shared token budget (M04 builder).
- ``vector_rag``: normalized raw-turn chunks only, retrieved with ``FIXED_VECTOR``.
- ``event_no_etec``: shared extraction snapshot without ETEC, retrieved with ``QEMR``.
- ``etec``: shared extraction snapshot with ETEC, retrieved with ``FIXED_VECTOR``.
- ``full``: shared extraction snapshot with ETEC, retrieved with ``QEMR``.

Fairness contracts (Gate B):

- ``vector_rag`` indexes ``build_raw_turn_corpus`` chunks only; it never
  receives the extraction snapshot. Event methods share exactly ONE
  ``extract_event_snapshot`` per conversation with a deterministic snapshot ID.
- Reader, extractor, and embedding are independent resolved providers from
  ``benchmarks.common.providers``; the runner contains no provider or
  memory-construction duplication.
- Every memory method consumes ``QEMRRetrievalResult.reader_messages`` directly
  (A-owned rendering) under the same complete reader-input token budget
  (``max_input_tokens``) and the same estimator.
- Every packed item carries exact raw-turn spans with raw turn IDs.
- Construction cost (extraction/write) is reported separately from per-query
  cost (search/read).

Manifest and resume: a resolved ``RunManifest`` (with expected sample/question
IDs and provider identities) is written once; resume refuses manifest drift;
per-sample files are immutable (write-once); smoke runs finalize with a
write-once ``FINALIZED.json``. ``--run-dir`` addresses a stable directory;
``--run-dir``/``--resume-dir``/``--output-root`` are mutually exclusive.

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
    ArtifactClass,
    BudgetSpec,
    ConsolidationAction,
    ConsolidationRecord,
    EvidencePrediction,
    EvidenceRecord,
    GitState,
    PolicyVersions,
    PredictionRecord,
    RunManifest,
    SampleEvaluation,
    TokenizerIdentity,
    check_resume,
    current_git_commit,
    finalize_run,
    load_finalized,
    required_hash,
    write_json_write_once,
    write_jsonl_write_once,
    write_manifest,
    write_per_sample,
)
from benchmarks.common.memory_inputs import (
    build_extractor,
    build_raw_turn_corpus,
    extract_event_snapshot,
    materialize_event_store,
    materialize_raw_turn_store,
    provider_identity,
)
from benchmarks.common.metrics import compute_answer_metrics, compute_evidence_metrics
from benchmarks.common.normalization import (
    NormalizedQuestion,
    NormalizedRecord,
    NormalizedSession,
    iter_longmemeval_records,
)
from benchmarks.common.providers import (
    ModelBundle,
    ProviderConfig,
    build_model_bundle,
    cache_for_run,
    resolve_provider_config,
)
from benchmarks.context_baselines import FullContextBuilder, NoMemoryContextBuilder
from evoeventmem.core.ports import ChatMessage
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
from evoeventmem.tokenization import DEFAULT_TOKEN_ESTIMATOR

DEFAULT_OUTPUT_ROOT = Path("artifacts/m13_longmemeval")
EXTRACTION_PROMPT_VERSION = "shared-snapshot.v1"
CONSOLIDATION_POLICY_NAME = "etec.v1"

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

VECTOR_INPUT_KIND = "raw_turn"
EVENT_INPUT_KIND = "event_snapshot"


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
_MEMORY_METHODS = frozenset(
    {Method.VECTOR_RAG, Method.EVENT_NO_ETEC, Method.ETEC, Method.FULL}
)
_CONTEXT_METHODS = frozenset({Method.NO_MEMORY, Method.FULL_CONTEXT})


class LongMemEvalConfig(BaseModel):
    schema_version: Literal["longmemeval.config.v1"] = "longmemeval.config.v1"
    run_id_prefix: str = Field(min_length=1)
    dataset_path: Path
    methods: list[Method] = Field(default_factory=lambda: list(Method))
    provider: Literal["deterministic_fake", "openai_compatible"] = "deterministic_fake"
    max_input_tokens: int = Field(gt=0)
    max_extraction_tokens: int | None = Field(default=None, gt=0)
    max_candidates_per_source: int = Field(ge=1)
    max_items_per_source: int = Field(ge=1)
    sample_limit: int | None = Field(default=None, ge=1)
    providers: ProviderConfig

    @model_validator(mode="before")
    @classmethod
    def assemble_providers(cls, payload: object) -> object:
        if isinstance(payload, dict) and "providers" not in payload:
            return {**payload, "providers": resolve_provider_config(payload)}
        return payload


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


class ConstructionCosts(BaseModel):
    extraction_ms: float = Field(ge=0.0)
    extraction_calls: int = Field(ge=0)
    vector_index_ms: float | None = None
    write_raw_ms: float | None = None
    write_etec_ms: float | None = None


class SampleResult(BaseModel):
    schema_version: Literal["longmemeval.sample.v1"] = "longmemeval.sample.v1"
    dataset: str
    sample_id: str
    question_id: str
    question_type: str | None
    category: str | None
    session_count: int = Field(ge=1)
    turn_count: int = Field(ge=1)
    construction: ConstructionCosts = Field(default_factory=ConstructionCosts)
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
    manifest_hash: str = Field(min_length=1)
    dataset_hash: str = Field(min_length=1)
    dataset_path: str
    reader_model: str = Field(min_length=1)
    extractor_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    tokenizer_name: str = Field(min_length=1)
    tokenizer_version: str = Field(min_length=1)
    extraction_prompt_version: str = Field(min_length=1)
    retrieval_policy_name: str = Field(min_length=1)
    router_policy_name: str = Field(min_length=1)
    consolidation_policy_name: str = Field(min_length=1)
    max_input_tokens: int = Field(gt=0)
    max_candidates_per_source: int = Field(ge=1)
    max_items_per_source: int = Field(ge=1)
    vector_input_kind: str = VECTOR_INPUT_KIND
    extraction_snapshot_ids: list[str] = Field(default_factory=list)
    sample_validation: SampleValidation
    methods: dict[str, MethodSummary] = Field(default_factory=dict)


def load_config(path: Path) -> LongMemEvalConfig:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return LongMemEvalConfig.model_validate(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the M13 LongMemEval experiment.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--resume-dir", type=Path, default=None)
    parser.add_argument("--sample-ids", nargs="*", default=None)
    parser.add_argument("--validate-config", action="store_true")
    parser.add_argument(
        "--extraction-only",
        action="store_true",
        help=(
            "Stop after writing per-sample extraction snapshots; skip "
            "materialization/retrieval/reader. Used for reachability smoke "
            "runs that only need the extraction_snapshot.json artifact."
        ),
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.validate_config:
        print(json.dumps(_config_report(config, args.config), indent=2, sort_keys=True))
        return 0

    run_dir = _resolve_run_dir(args)
    summary = run_experiment(
        config,
        run_dir,
        sample_ids=args.sample_ids,
        extraction_only=args.extraction_only,
    )
    if args.extraction_only:
        print(
            "extraction-only: per-sample snapshots and extraction_snapshot.json "
            "written; retrieval/reader/finalize skipped."
        )
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def _resolve_run_dir(args: argparse.Namespace) -> Path:
    provided = [
        option
        for option, value in (
            ("--run-dir", args.run_dir),
            ("--resume-dir", args.resume_dir),
            ("--output-root", args.output_root),
        )
        if value is not None
    ]
    if len(provided) > 1:
        raise ValueError(
            "--run-dir, --resume-dir, and --output-root are mutually exclusive; "
            f"provided: {provided}"
        )
    if args.run_dir is not None:
        return args.run_dir
    if args.resume_dir is not None:
        return args.resume_dir
    output_root = args.output_root or DEFAULT_OUTPUT_ROOT
    return _new_run_dir(output_root, load_config(args.config).run_id_prefix)


def run_experiment(
    config: LongMemEvalConfig,
    run_dir: Path,
    *,
    sample_ids: Sequence[str] | None = None,
    extractor: Any | None = None,
    extraction_only: bool = False,
) -> LongMemEvalSummary:
    """Run (or resume) an experiment and finalize it once complete.

    A first run and an identical resume address the same directory. Completed
    samples are immutable; a finalized run is validated and returned without
    mutation; manifest drift refuses the run.

    When ``extraction_only`` is set, each sample stops after the extraction
    snapshot is written; materialization, retrieval, reader, and the finalize
    marker are skipped. The returned summary still reports the snapshot IDs and
    the run root still receives ``extraction_snapshot.json`` so that downstream
    reachability/stat tools can read it.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    existing_manifest = (
        RunManifest.model_validate(json.loads((run_dir / "manifest.json").read_text()))
        if (run_dir / "manifest.json").exists()
        else None
    )
    if sample_ids is None and existing_manifest is not None:
        sample_ids = list(existing_manifest.expected_sample_ids)
    records = _apply_sample_ids(_load_records(config, sample_ids), sample_ids)
    expected_sample_ids = [record.sample_id for record in records]
    expected_question_ids = [record.questions[0].question_id for record in records]
    manifest = _build_manifest(
        config,
        run_dir,
        expected_sample_ids=expected_sample_ids,
        expected_question_ids=expected_question_ids,
    )

    if _is_finalized(run_dir):
        check_resume(run_dir, manifest)
        load_finalized(run_dir)
        return _load_stored_summary(run_dir)

    if (run_dir / "manifest.json").exists():
        check_resume(run_dir, manifest)
    else:
        write_manifest(run_dir, manifest)

    bundle = build_model_bundle(config.providers, cache_for_run(run_dir))
    extractor_impl = extractor if extractor is not None else build_extractor(bundle)
    failed_samples: list[str] = []
    for record in records:
        try:
            _process_sample(
                record,
                config,
                bundle,
                extractor_impl,
                run_dir,
                extraction_only=extraction_only,
            )
        except Exception as exc:
            import traceback
            print(f"WARN: sample {record.sample_id} failed: {exc}", flush=True)
            traceback.print_exc()
            failed_samples.append(record.sample_id)
    if failed_samples:
        print(f"WARN: {len(failed_samples)} samples failed: {failed_samples}", flush=True)

    summary = _write_summary(config, run_dir, manifest, expected_sample_ids, bundle)
    completion_counts = {
        "samples": len(records),
        "extraction_snapshots": len(summary.extraction_snapshot_ids),
    }
    if not extraction_only:
        if summary.sample_validation.valid:
            finalize_run(run_dir, manifest, completion_counts=completion_counts)
        else:
            validation = summary.sample_validation
            print(
                "WARN: skipping finalize; sample validation failed "
                f"(completed {validation.completed_sample_count}/"
                f"{validation.expected_sample_count}, "
                f"missing={validation.missing_sample_ids}); "
                "rerun the same command to retry missing samples",
                flush=True,
            )
    return summary


def _process_sample(
    record: NormalizedRecord,
    config: LongMemEvalConfig,
    bundle: ModelBundle,
    extractor: Any,
    run_dir: Path,
    *,
    extraction_only: bool = False,
) -> SampleResult:
    sample_path = _sample_path(run_dir, record.sample_id)
    if sample_path.exists():
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        return SampleResult.model_validate(payload)

    user_id = record.sample_id
    question = record.questions[0]
    ordered_record = _order_record(record)
    extractor_identity = provider_identity(
        bundle.resolved.extractor, version=EXTRACTION_PROMPT_VERSION
    )

    started = perf_counter()
    corpus = build_raw_turn_corpus(ordered_record)
    snapshot = extract_event_snapshot(
        ordered_record,
        extractor,
        user_id=user_id,
        extractor_identity=extractor_identity,
        max_tokens=config.max_extraction_tokens,
    )
    extraction_ms = (perf_counter() - started) * 1000

    snapshot_path = _snapshot_path(run_dir, record.sample_id)
    if not snapshot_path.exists():
        write_per_sample(
            run_dir,
            f"samples/{snapshot_path.name}",
            snapshot,
        )

    if extraction_only:
        # Stop after writing the per-sample extraction snapshot. Skip
        # materialization, retrieval, and the reader. The minimal SampleResult
        # keeps the snapshot metadata that ``_write_summary`` and
        # ``_write_run_root_artifacts`` rely on (sample_id + ingestion.event)
        # so the combined ``extraction_snapshot.json`` is still assembled.
        result = SampleResult(
            dataset=record.dataset,
            sample_id=record.sample_id,
            question_id=question.question_id,
            question_type=question.category,
            category=_category_for(question.category),
            session_count=len(ordered_record.sessions),
            turn_count=sum(len(session.turns) for session in ordered_record.sessions),
            construction=ConstructionCosts(
                extraction_ms=extraction_ms,
                extraction_calls=1,
            ),
            ingestion={
                "event": {
                    "input_kind": EVENT_INPUT_KIND,
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_file": f"samples/{snapshot_path.name}",
                    "snapshot_hash": required_hash(snapshot_path),
                    "raw_turn_count": snapshot.raw_turn_count,
                    "event_count": snapshot.event_count,
                    "rejection_count": len(snapshot.rejections),
                    "extractor_model": snapshot.extractor.model_id,
                },
            },
            methods={},
        )
        write_json_write_once(sample_path, result)
        return result

    started = perf_counter()
    raw_store, raw_ingestion = materialize_event_store(
        snapshot, apply_etec=False, user_id=user_id
    )
    write_raw_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    etec_store, etec_ingestion = materialize_event_store(
        snapshot,
        apply_etec=True,
        embedding_model=bundle.embedding,
        user_id=user_id,
    )
    write_etec_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    vector_store, vector_ingestion = materialize_raw_turn_store(corpus, user_id=user_id)
    # S4b: pre-warm the embedding cache at index-build time so the per-query
    # ``search_latency_ms`` measures the true retrieval cost (query embed +
    # cosine similarity over cached chunk vectors) instead of the one-time
    # corpus-embedding cost. The v1 baseline lazily embedded every chunk on
    # every retrieve() call through ``_dense_candidates``, producing the
    # observed ~437s p50 search latency on the v1 test50-mimo run; batching
    # alone (CachedEmbeddingModel S4b fix) brought it to ~70s but the
    # server-side serial processing (~128ms/text × ~500 chunks) kept it above
    # the S4b 30s target. Moving the embedding cost to write time mirrors
    # what ``materialize_event_store`` already does for the event methods and
    # brings vector_rag p50 search latency under 1s. The pre-warm call only
    # fires when ``vector_rag`` is in the configured methods so other method
    # subsets don't pay the cost.
    if Method.VECTOR_RAG in set(config.methods):
        bundle.embedding.embed_texts([chunk.content for chunk in corpus.chunks])
    vector_index_ms = (perf_counter() - started) * 1000

    methods: dict[str, MethodSampleRecord] = {}
    for method in config.methods:
        if method in _CONTEXT_METHODS:
            record_method = _run_context_method(
                method, question, ordered_record.sessions, config, bundle
            )
        else:
            record_method = _run_memory_method(
                method,
                question,
                _store_for(method, vector_store, raw_store, etec_store),
                config,
                bundle,
                user_id=user_id,
                input_kind=(
                    VECTOR_INPUT_KIND
                    if method is Method.VECTOR_RAG
                    else EVENT_INPUT_KIND
                ),
                snapshot_id=(
                    None if method is Method.VECTOR_RAG else snapshot.snapshot_id
                ),
                write_latency_ms=_write_latency_for(
                    method,
                    vector_index_ms,
                    write_raw_ms,
                    write_etec_ms,
                ),
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
        construction=ConstructionCosts(
            extraction_ms=extraction_ms,
            extraction_calls=1,
            vector_index_ms=vector_index_ms,
            write_raw_ms=write_raw_ms,
            write_etec_ms=write_etec_ms,
        ),
        ingestion={
            "raw_turn": vector_ingestion,
            "event": {
                "input_kind": EVENT_INPUT_KIND,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_file": f"samples/{snapshot_path.name}",
                "snapshot_hash": required_hash(snapshot_path),
                "raw_turn_count": snapshot.raw_turn_count,
                "event_count": snapshot.event_count,
                "rejection_count": len(snapshot.rejections),
                "extractor_model": snapshot.extractor.model_id,
            },
            "raw": raw_ingestion,
            "etec": etec_ingestion,
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
    bundle: ModelBundle,
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
    response = bundle.reader.generate([ChatMessage(role="user", content=context.prompt)])
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
    store: Any,
    config: LongMemEvalConfig,
    bundle: ModelBundle,
    *,
    user_id: str,
    input_kind: str,
    snapshot_id: str | None,
    write_latency_ms: float,
) -> MethodSampleRecord:
    harness = RetrievalHarness(
        store,
        bundle.embedding,
        max_items_per_source=config.max_items_per_source,
        max_candidates_per_source=config.max_candidates_per_source,
    )
    started = perf_counter()
    result = harness.retrieve(
        question.question,
        user_id=user_id,
        strategy=_METHOD_STRATEGY[method],
        budget_tokens=config.max_input_tokens,
        reference_time=question.asked_at,
    )
    search_latency_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    response = bundle.reader.generate(result.reader_messages)
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
        input_tokens=result.budget.total_input_tokens_estimate,
        output_tokens=response.output_tokens,
        llm_calls=1,
        model_cache_key=response.cache_key,
        retrieval=_retrieval_payload(result),
        context={
            "input_kind": input_kind,
            **( {} if snapshot_id is None else {"snapshot_id": snapshot_id} ),
            "reader_source": "qemr_reader_messages",
            "estimator_name": result.estimator_name,
            "estimator_version": result.estimator_version,
        },
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


def _store_for(
    method: Method,
    vector_store: Any,
    raw_store: Any,
    etec_store: Any,
) -> Any:
    if method is Method.VECTOR_RAG:
        return vector_store
    if method in _METHOD_APPLIES_ETEC:
        return etec_store
    return raw_store


def _write_latency_for(
    method: Method,
    vector_index_ms: float,
    write_raw_ms: float,
    write_etec_ms: float,
) -> float:
    if method is Method.VECTOR_RAG:
        return vector_index_ms
    if method in _METHOD_APPLIES_ETEC:
        return write_etec_ms
    return write_raw_ms


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
        "content_tokens": result.budget.content_tokens,
        "prompt_overhead_tokens": result.budget.prompt_overhead_tokens,
        "total_input_tokens_estimate": result.budget.total_input_tokens_estimate,
        "packing_bound": _packing_bound(result),
        "candidate_count": len(result.candidates),
        "exclusion_count": len(result.exclusions),
        "source_failures": [
            {
                "source": failure.source.value,
                "reason_code": failure.reason_code,
                "degraded_policy": failure.degraded_policy.value,
                "duration_ms": failure.duration_ms,
            }
            for failure in result.source_failures
        ],
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
                        "quote": ref.quote,
                        "session_id": ref.metadata.get("session_id"),
                        "raw_turn_id": ref.metadata.get("raw_turn_id"),
                    }
                    for ref in item.evidence_refs
                ],
            }
            for item in result.selected_context
        ],
    }


def _packing_bound(result: QEMRRetrievalResult) -> bool:
    return any(
        exclusion.reason == "budget_exceeded" for exclusion in result.exclusions
    )


def _build_manifest(
    config: LongMemEvalConfig,
    run_dir: Path,
    *,
    expected_sample_ids: Sequence[str],
    expected_question_ids: Sequence[str],
) -> RunManifest:
    return RunManifest(
        run_id=run_dir.name,
        artifact_class=_artifact_class(config),
        dataset="longmemeval",
        dataset_path=str(config.dataset_path),
        dataset_hash=_dataset_hash(config.dataset_path),
        scope=_scope(config),
        methods=[method.value for method in config.methods],
        reader=provider_identity(config.providers.reader),
        extractor=provider_identity(
            config.providers.extractor, version=EXTRACTION_PROMPT_VERSION
        ),
        embedding=provider_identity(config.providers.embedding),
        tokenizer=TokenizerIdentity(
            name=DEFAULT_TOKEN_ESTIMATOR.name,
            version=DEFAULT_TOKEN_ESTIMATOR.version,
        ),
        policies=PolicyVersions(
            extraction=EXTRACTION_PROMPT_VERSION,
            router=ROUTER_POLICY_NAME,
            retrieval=RETRIEVAL_POLICY_NAME,
            consolidation=CONSOLIDATION_POLICY_NAME,
        ),
        budget=BudgetSpec(
            input_tokens=config.max_input_tokens,
            max_items_per_source=config.max_items_per_source,
            max_candidates_per_source=config.max_candidates_per_source,
        ),
        git=GitState(
            commit=current_git_commit(),
            dirty=_git_is_dirty(),
            dirty_diff_hash=_dirty_diff_hash() if _git_is_dirty() else None,
        ),
        config_hash=_hash_json(config.model_dump(mode="json")),
        expected_sample_ids=list(expected_sample_ids),
        expected_question_ids=list(expected_question_ids),
        metadata={
            "vector_input_kind": VECTOR_INPUT_KIND,
            "reader_message_source": "qemr_reader_messages",
        },
    )


def _write_summary(
    config: LongMemEvalConfig,
    run_dir: Path,
    manifest: RunManifest,
    expected_sample_ids: Sequence[str],
    bundle: ModelBundle,
) -> LongMemEvalSummary:
    samples = _load_sample_results(run_dir)
    validation = _validate_samples(list(expected_sample_ids), [s.sample_id for s in samples])
    methods: dict[str, MethodSummary] = {}
    for method in config.methods:
        methods[method.value] = _summarize_method(method, samples)
    snapshot_ids = [
        str(sample.ingestion.get("event", {}).get("snapshot_id"))
        for sample in samples
        if sample.ingestion.get("event", {}).get("snapshot_id")
    ]
    summary = LongMemEvalSummary(
        run_id=run_dir.name,
        git_commit=current_git_commit(),
        git_dirty=_git_is_dirty(),
        config_hash=_hash_json(config.model_dump(mode="json")),
        manifest_hash=manifest.manifest_hash(),
        dataset_hash=_dataset_hash(config.dataset_path),
        dataset_path=str(config.dataset_path),
        reader_model=bundle.resolved.reader.model_id,
        extractor_model=bundle.resolved.extractor.model_id,
        embedding_model=bundle.resolved.embedding.model_id,
        tokenizer_name=DEFAULT_TOKEN_ESTIMATOR.name,
        tokenizer_version=DEFAULT_TOKEN_ESTIMATOR.version,
        extraction_prompt_version=EXTRACTION_PROMPT_VERSION,
        retrieval_policy_name=RETRIEVAL_POLICY_NAME,
        router_policy_name=ROUTER_POLICY_NAME,
        consolidation_policy_name=CONSOLIDATION_POLICY_NAME,
        max_input_tokens=config.max_input_tokens,
        max_candidates_per_source=config.max_candidates_per_source,
        max_items_per_source=config.max_items_per_source,
        vector_input_kind=VECTOR_INPUT_KIND,
        extraction_snapshot_ids=sorted(snapshot_ids),
        sample_validation=validation,
        methods=methods,
    )
    _write_combined_artifacts(run_dir, config.methods, samples)
    _write_run_root_artifacts(run_dir, samples)
    summary_path = run_dir / "summary.json"
    summary_path.unlink(missing_ok=True)
    write_json_write_once(summary_path, summary)
    return summary


def _write_run_root_artifacts(run_dir: Path, samples: Sequence[SampleResult]) -> None:
    snapshots = [
        json.loads(path.read_text(encoding="utf-8"))
        for sample in samples
        if (path := _snapshot_path(run_dir, sample.sample_id)).exists()
    ]
    _rewrite_json(run_dir / "extraction_snapshot.json", snapshots)

    evidence_rows: list[EvidenceRecord] = []
    retrieval_rows: list[dict[str, Any]] = []
    consolidation_rows: list[ConsolidationRecord] = []
    for sample in samples:
        for method_name, record in sorted(sample.methods.items()):
            if record.retrieval is None:
                continue
            retrieval_rows.append(
                {
                    "dataset": sample.dataset,
                    "sample_id": sample.sample_id,
                    "question_id": sample.question_id,
                    "method": method_name,
                    **record.retrieval,
                }
            )
            for item in record.retrieval["packed_items"]:
                for ref in item["evidence_refs"]:
                    raw_turn_id = ref.get("raw_turn_id")
                    if raw_turn_id is None:
                        continue
                    evidence_rows.append(
                        EvidenceRecord(
                            question_id=sample.question_id,
                            raw_turn_id=str(raw_turn_id),
                            span=str(ref.get("locator") or ""),
                            exact=True,
                        )
                    )
        etec_ingestion = sample.ingestion.get("etec") or {}
        actions = etec_ingestion.get("actions") or {}
        if actions:
            dominant = max(actions, key=lambda action: (actions[action], action))
            consolidation_rows.append(
                ConsolidationRecord(
                    sample_id=sample.sample_id,
                    action=_map_consolidation_action(dominant),
                    evidence=[],
                )
            )
    _rewrite_jsonl(run_dir / "retrieval.jsonl", retrieval_rows)
    _rewrite_jsonl(run_dir / "evidence.jsonl", evidence_rows)
    _rewrite_jsonl(run_dir / "consolidation.jsonl", consolidation_rows)


def _map_consolidation_action(raw_action: str) -> ConsolidationAction:
    try:
        return ConsolidationAction(raw_action.lower())
    except ValueError:
        return ConsolidationAction.KEEP


def _load_sample_results(run_dir: Path) -> list[SampleResult]:
    samples_dir = run_dir / "samples"
    samples: list[SampleResult] = []
    if not samples_dir.is_dir():
        return samples
    for path in sorted(samples_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        if ".extraction_snapshot.json" in path.name:
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
    report: dict[str, Any] = {
        "config_path": str(config_path),
        "config_hash": _hash_json(config.model_dump(mode="json")),
        "dataset_hash": _dataset_hash(config.dataset_path),
        "git_commit": current_git_commit(),
        "extraction_prompt_version": EXTRACTION_PROMPT_VERSION,
        "retrieval_policy_name": RETRIEVAL_POLICY_NAME,
        "router_policy_name": ROUTER_POLICY_NAME,
        "consolidation_policy_name": CONSOLIDATION_POLICY_NAME,
        "vector_input_kind": VECTOR_INPUT_KIND,
        "providers": config.providers.redacted(),
    }
    return report


def _load_records(
    config: LongMemEvalConfig, sample_ids: Sequence[str] | None = None
) -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    for record in iter_longmemeval_records(config.dataset_path):
        records.append(record)
        if (
            sample_ids is None
            and config.sample_limit is not None
            and len(records) >= config.sample_limit
        ):
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


def _snapshot_path(run_dir: Path, sample_id: str) -> Path:
    safe_id = _SAFE_ID_RE.sub("_", sample_id)
    return run_dir / "samples" / f"{safe_id}.extraction_snapshot.json"


def _category_for(question_type: str | None) -> str | None:
    if question_type is None:
        return None
    return CATEGORY_BY_QUESTION_TYPE.get(question_type)


def _rewrite_jsonl(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    path.unlink(missing_ok=True)
    write_jsonl_write_once(path, records)


def _rewrite_json(path: Path, payload: Any) -> None:
    path.unlink(missing_ok=True)
    write_json_write_once(path, payload)


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


def _dirty_diff_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return _hash_json({"diff": result.stdout})


def _is_finalized(run_dir: Path) -> bool:
    return (run_dir / "finalized" / "FINALIZED.json").exists()


def _load_stored_summary(run_dir: Path) -> LongMemEvalSummary:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"finalized run has no stored summary: {summary_path}")
    return LongMemEvalSummary.model_validate_json(summary_path.read_text())


def _new_run_dir(output_root: Path, run_id_prefix: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / f"{run_id_prefix}-{timestamp}"
    run_dir.mkdir()
    return run_dir


def _artifact_class(config: LongMemEvalConfig) -> ArtifactClass:
    if config.provider == "deterministic_fake":
        return ArtifactClass.SMOKE
    return ArtifactClass.PUBLICATION


def _scope(config: LongMemEvalConfig) -> str:
    if config.sample_limit is not None:
        return f"sample_limit={config.sample_limit}"
    return "full"


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


if __name__ == "__main__":
    raise SystemExit(main())
