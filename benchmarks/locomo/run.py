"""M14 LoCoMo main experiment runner (no oracle extraction inputs).

Runs seven fair, resumable methods on LoCoMo records:

- ``no_memory``: question-only prompt (M04 builder).
- ``full_context``: raw sessions truncated to the shared token budget (M04 builder).
- ``session_summary``: official per-session summaries (LoCoMo ``session_summary``
  metadata) truncated to the shared token budget; the LoCoMo paper's session
  summary baseline, used with no retrieval.
- ``vector_rag``: normalized raw-dialogue chunks only, retrieved with ``FIXED_VECTOR``.
- ``event_no_etec``: shared extraction snapshot without ETEC, retrieved with ``QEMR``.
- ``etec``: shared extraction snapshot with ETEC, retrieved with ``FIXED_VECTOR``.
- ``full``: shared extraction snapshot with ETEC, retrieved with ``QEMR``.

Oracle-leakage contracts (Gate B):

- Official ``event_summary`` content is a structural TARGET only; it is ABSENT
  from the extraction input (``extract_event_snapshot`` clears summaries and
  observations). Gold summaries are never reintroduced to preserve a score.
- Normalized raw turns preserve official ``dia_id`` as ``raw_turn_id``; predicted
  evidence comes only from packed raw-turn references and maps to official QA
  evidence IDs (``locomo_dialogue``).
- ``vector_rag`` indexes raw-dialogue chunks only; event methods share exactly
  ONE extraction snapshot per conversation; the snapshot never reaches the
  vector baseline.
- Reader, extractor, and embedding are independent resolved providers; memory
  methods consume ``QEMRRetrievalResult.reader_messages`` under the same
  complete reader-input token budget. The official reader format directive is
  appended only to context methods (no retrieval budget applies to them).
- Structural precision/coverage/F1 compares independently extracted events to
  official summaries per session by a declared session-level matching policy
  and is labeled a structural proxy, never "extraction accuracy".

Manifest/resume contract matches LongMemEval: resolved manifest, expected
sample/question IDs, immutable per-sample files, manifest-drift refusal, smoke
finalization, and a mutually exclusive ``--run-dir``/``--resume-dir``/
``--output-root`` CLI.

Usage::

    uv run python -m benchmarks.locomo.run --config configs/locomo/smoke.toml
    uv run python -m benchmarks.locomo.run \\
        --config configs/locomo/main.toml --validate-config
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
    iter_locomo_records,
)
from benchmarks.common.providers import (
    ModelBundle,
    ProviderConfig,
    build_model_bundle,
    cache_for_run,
    resolve_provider_config,
)
from benchmarks.context_baselines import (
    ContextBuildResult,
    FullContextBuilder,
    NoMemoryContextBuilder,
    TruncationDecision,
)
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

DEFAULT_OUTPUT_ROOT = Path("artifacts/m14_locomo")
EXTRACTION_PROMPT_VERSION = "shared-snapshot.v1"
CONSOLIDATION_POLICY_NAME = "etec.v1"

REFERENCE_TIME_SOURCE = "last_session_timestamp"
EVIDENCE_MAPPING = "official_dia_ids_from_turn_refs"
STRUCTURAL_PROXY_LABEL = "structural_proxy"
STRUCTURAL_MATCHING_POLICY = "session_level_token_f1_ge_{threshold}"
# LoCoMo's official protocol instructs readers to replicate the exact wording
# when feasible; this directive is appended identically to every CONTEXT
# method's prompt (fairness). Memory methods consume A's rendered reader
# messages without an appended directive so the complete reader input stays
# identical to the budgeted ``total_input_tokens_estimate``.
READER_FORMAT_DIRECTIVE = "Answer with only the exact answer, no explanation."

VECTOR_INPUT_KIND = "raw_turn"
EVENT_INPUT_KIND = "event_snapshot"

# Official LoCoMo QA categories (paper Table 2 names), keyed by the numeric
# label stored in ``qa.category``.
LOCOMO_CATEGORY_BY_ID: dict[int, str] = {
    1: "single-hop",
    2: "temporal-reasoning",
    3: "open-domain-knowledge",
    4: "multi-hop-reasoning",
    5: "adversarial",
}


class Method(StrEnum):
    NO_MEMORY = "no_memory"
    FULL_CONTEXT = "full_context"
    SESSION_SUMMARY = "session_summary"
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
_CONTEXT_METHODS = frozenset({Method.NO_MEMORY, Method.FULL_CONTEXT, Method.SESSION_SUMMARY})


class LocomoConfig(BaseModel):
    schema_version: Literal["locomo.config.v1"] = "locomo.config.v1"
    run_id_prefix: str = Field(min_length=1)
    dataset_path: Path
    methods: list[Method] = Field(default_factory=lambda: list(Method))
    provider: Literal["deterministic_fake", "openai_compatible"] = "deterministic_fake"
    max_input_tokens: int = Field(gt=0)
    max_candidates_per_source: int = Field(ge=1)
    max_items_per_source: int = Field(ge=1)
    sample_limit: int | None = Field(default=None, ge=1)
    structural_match_f1_threshold: float = Field(default=0.5, gt=0.0, le=1.0)
    providers: ProviderConfig

    @model_validator(mode="before")
    @classmethod
    def assemble_providers(cls, payload: object) -> object:
        if isinstance(payload, dict) and "providers" not in payload:
            return {**payload, "providers": resolve_provider_config(payload)}
        return payload


class MethodSampleRecord(BaseModel):
    schema_version: Literal["locomo.method-sample.v1"] = "locomo.method-sample.v1"
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


class QuestionRecord(BaseModel):
    question_id: str = Field(min_length=1)
    question_type: str | None
    category: str | None
    reference_time: str | None
    methods: dict[str, MethodSampleRecord] = Field(default_factory=dict)


class ConstructionCosts(BaseModel):
    extraction_ms: float = Field(ge=0.0)
    extraction_calls: int = Field(ge=0)
    vector_index_ms: float | None = None
    write_raw_ms: float | None = None
    write_etec_ms: float | None = None


class SampleResult(BaseModel):
    schema_version: Literal["locomo.sample.v1"] = "locomo.sample.v1"
    dataset: str
    sample_id: str
    session_count: int = Field(ge=1)
    turn_count: int = Field(ge=1)
    question_count: int = Field(ge=1)
    reference_time: str | None
    construction: ConstructionCosts = Field(default_factory=ConstructionCosts)
    ingestion: dict[str, Any] = Field(default_factory=dict)
    questions: dict[str, QuestionRecord] = Field(default_factory=dict)
    event_structure: dict[str, EventStructureMetrics] = Field(default_factory=dict)


class EventStructureMetrics(BaseModel):
    schema_version: Literal["locomo.event-structure.v1"] = "locomo.event-structure.v1"
    metric_kind: Literal["structural_proxy"] = "structural_proxy"
    matching_policy: str = Field(min_length=1)
    session_count: int = Field(ge=0)
    official_event_count: int = Field(ge=0)
    extracted_event_count: int = Field(ge=0)
    matched_official_count: int = Field(ge=0)
    matched_extracted_count: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    precision: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)


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


class QuestionValidation(BaseModel):
    expected_question_count: int = Field(ge=0)
    completed_question_count: int = Field(ge=0)
    missing_question_ids: list[str] = Field(default_factory=list)
    duplicate_question_ids: list[str] = Field(default_factory=list)
    valid: bool


class LocomoSummary(BaseModel):
    schema_version: Literal["locomo.summary.v1"] = "locomo.summary.v1"
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
    reader_thinking: str = Field(min_length=1)
    reader_format_directive: str = Field(min_length=1)
    extraction_prompt_version: str = Field(min_length=1)
    retrieval_policy_name: str = Field(min_length=1)
    router_policy_name: str = Field(min_length=1)
    consolidation_policy_name: str = Field(min_length=1)
    reference_time_source: str = Field(min_length=1)
    evidence_mapping: str = Field(min_length=1)
    structural_proxy_label: str = Field(min_length=1)
    structural_matching_policy: str = Field(min_length=1)
    structural_match_f1_threshold: float = Field(gt=0.0, le=1.0)
    max_input_tokens: int = Field(gt=0)
    max_candidates_per_source: int = Field(ge=1)
    max_items_per_source: int = Field(ge=1)
    vector_input_kind: str = VECTOR_INPUT_KIND
    extraction_snapshot_ids: list[str] = Field(default_factory=list)
    sample_validation: SampleValidation
    question_validation: QuestionValidation
    methods: dict[str, MethodSummary] = Field(default_factory=dict)
    event_structure: dict[str, EventStructureMetrics] = Field(default_factory=dict)


class SessionSummaryContextBuilder:
    """Builds the Session Summary baseline prompt from official summaries."""

    def __init__(self, max_input_tokens: int) -> None:
        self.max_input_tokens = max_input_tokens

    def build(
        self,
        question: NormalizedQuestion,
        record: NormalizedRecord,
    ) -> ContextBuildResult:
        summaries = _ordered_session_summaries(record)
        question_line = f"Question: {question.question}"
        question_tokens = _count_tokens(question_line)
        if question_tokens > self.max_input_tokens:
            return _fit_question_only(question.question_id, question_line, self.max_input_tokens)

        remaining_tokens = self.max_input_tokens - question_tokens
        accepted: list[tuple[str, str]] = []
        truncations: list[TruncationDecision] = []
        for session_id, text, token_count in reversed(summaries):
            if token_count <= remaining_tokens:
                accepted.append((session_id, text))
                remaining_tokens -= token_count
            else:
                truncations.append(
                    TruncationDecision(
                        source_type="session_summary",
                        source_id=session_id,
                        reason="context_budget_exceeded",
                        token_count=token_count,
                        max_input_tokens=self.max_input_tokens,
                    )
                )
        accepted.reverse()
        prompt_lines = [f"{session_id}_summary: {text}" for session_id, text in accepted]
        prompt_lines.append(question_line)
        prompt = "\n".join(prompt_lines)
        return ContextBuildResult(
            prompt=prompt,
            input_tokens=_count_tokens(prompt),
            included_history_turn_ids=tuple(session_id for session_id, _ in accepted),
            truncations=tuple(reversed(truncations)),
        )


def load_config(path: Path) -> LocomoConfig:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return LocomoConfig.model_validate(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the M14 LoCoMo experiment.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--resume-dir", type=Path, default=None)
    parser.add_argument("--sample-ids", nargs="*", default=None)
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.validate_config:
        print(json.dumps(_config_report(config, args.config), indent=2, sort_keys=True))
        return 0

    run_dir = _resolve_run_dir(args)
    summary = run_experiment(config, run_dir, sample_ids=args.sample_ids)
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
    config: LocomoConfig,
    run_dir: Path,
    *,
    sample_ids: Sequence[str] | None = None,
    extractor: Any | None = None,
) -> LocomoSummary:
    run_dir.mkdir(parents=True, exist_ok=True)
    records = _apply_sample_ids(_load_records(config), sample_ids)
    expected_sample_ids = [record.sample_id for record in records]
    expected_question_ids = [
        question.question_id for record in records for question in record.questions
    ]
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
    for record in records:
        _process_sample(record, config, bundle, extractor_impl, run_dir)

    summary = _write_summary(config, run_dir, manifest, expected_sample_ids, bundle)
    completion_counts = {
        "samples": len(records),
        "extraction_snapshots": len(summary.extraction_snapshot_ids),
    }
    finalize_run(run_dir, manifest, completion_counts=completion_counts)
    return summary


def _process_sample(
    record: NormalizedRecord,
    config: LocomoConfig,
    bundle: ModelBundle,
    extractor: Any,
    run_dir: Path,
) -> SampleResult:
    sample_path = _sample_path(run_dir, record.sample_id)
    if sample_path.exists():
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        return SampleResult.model_validate(payload)

    user_id = record.sample_id
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
    )
    extraction_ms = (perf_counter() - started) * 1000

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
    vector_index_ms = (perf_counter() - started) * 1000

    snapshot_path = _snapshot_path(run_dir, record.sample_id)
    if not snapshot_path.exists():
        write_per_sample(
            run_dir,
            f"samples/{snapshot_path.name}",
            snapshot,
        )

    reference_time = _reference_time(ordered_record)
    questions: dict[str, QuestionRecord] = {}
    for question in ordered_record.questions:
        methods: dict[str, MethodSampleRecord] = {}
        for method in config.methods:
            if method is Method.SESSION_SUMMARY:
                record_method = _run_session_summary_method(
                    method, question, ordered_record, config, bundle
                )
            elif method in _CONTEXT_METHODS:
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
                    reference_time=reference_time,
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
        questions[question.question_id] = QuestionRecord(
            question_id=question.question_id,
            question_type=question.category,
            category=_category_for(question.category),
            reference_time=reference_time.isoformat() if reference_time else None,
            methods=methods,
        )

    structure: dict[str, EventStructureMetrics] = {
        "raw": _event_structure_metrics(
            ordered_record, raw_store.list_for_user(user_id), config
        )
    }
    if _needs_etec(config.methods):
        structure["etec"] = _event_structure_metrics(
            ordered_record, etec_store.list_for_user(user_id), config
        )

    result = SampleResult(
        dataset=record.dataset,
        sample_id=record.sample_id,
        session_count=len(ordered_record.sessions),
        turn_count=sum(len(session.turns) for session in ordered_record.sessions),
        question_count=len(questions),
        reference_time=reference_time.isoformat() if reference_time else None,
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
        questions=questions,
        event_structure=structure,
    )
    write_json_write_once(sample_path, result)
    return result


def _run_context_method(
    method: Method,
    question: NormalizedQuestion,
    sessions: Sequence[NormalizedSession],
    config: LocomoConfig,
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
    prompt = _apply_reader_directive(context.prompt)
    started = perf_counter()
    response = bundle.reader.generate([ChatMessage(role="user", content=prompt)])
    question_latency_ms = (perf_counter() - started) * 1000
    return _evaluate(
        method=method,
        question=question,
        prediction=response.text,
        evidence=[],
        question_latency_ms=question_latency_ms,
        search_latency_ms=search_latency_ms,
        write_latency_ms=None,
        input_tokens=_count_tokens(prompt),
        output_tokens=response.output_tokens,
        llm_calls=1,
        model_cache_key=response.cache_key,
        retrieval=None,
        context={
            "included_history_turn_ids": list(context.included_history_turn_ids),
            "truncations": [asdict(decision) for decision in context.truncations],
        },
    )


def _run_session_summary_method(
    method: Method,
    question: NormalizedQuestion,
    record: NormalizedRecord,
    config: LocomoConfig,
    bundle: ModelBundle,
) -> MethodSampleRecord:
    builder = SessionSummaryContextBuilder(config.max_input_tokens)
    started = perf_counter()
    context = builder.build(question, record)
    search_latency_ms = (perf_counter() - started) * 1000
    prompt = _apply_reader_directive(context.prompt)
    started = perf_counter()
    response = bundle.reader.generate([ChatMessage(role="user", content=prompt)])
    question_latency_ms = (perf_counter() - started) * 1000
    return _evaluate(
        method=method,
        question=question,
        prediction=response.text,
        evidence=[],
        question_latency_ms=question_latency_ms,
        search_latency_ms=search_latency_ms,
        write_latency_ms=None,
        input_tokens=_count_tokens(prompt),
        output_tokens=response.output_tokens,
        llm_calls=1,
        model_cache_key=response.cache_key,
        retrieval=None,
        context={
            "baseline": "official_session_summary",
            "included_history_turn_ids": list(context.included_history_turn_ids),
            "truncations": [asdict(decision) for decision in context.truncations],
        },
    )


def _run_memory_method(
    method: Method,
    question: NormalizedQuestion,
    store: Any,
    config: LocomoConfig,
    bundle: ModelBundle,
    *,
    user_id: str,
    reference_time: datetime | None,
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
        reference_time=reference_time,
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


def _needs_etec(methods: Sequence[Method]) -> bool:
    return any(method in _METHOD_APPLIES_ETEC for method in methods)


def _reader_thinking(config: LocomoConfig) -> str:
    if config.provider == "deterministic_fake":
        return "n/a"
    thinking = config.providers.reader.thinking
    return "disabled" if thinking == "disabled" else "enabled"


def _apply_reader_directive(prompt: str) -> str:
    return f"{prompt}\n{READER_FORMAT_DIRECTIVE}"


def _evidence_from_packed_items(items: Sequence[PackedItem]) -> list[EvidencePrediction]:
    predicted: list[EvidencePrediction] = []
    seen: set[str] = set()
    for item in items:
        for ref in item.evidence_refs:
            raw_turn_id = ref.metadata.get("raw_turn_id")
            if raw_turn_id is None:
                continue
            dia_id = str(raw_turn_id)
            if dia_id in seen:
                continue
            seen.add(dia_id)
            predicted.append(
                EvidencePrediction(
                    source_type="locomo_dialogue",
                    source_id=dia_id,
                    locator="qa.evidence",
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


def _event_structure_metrics(
    record: NormalizedRecord,
    memories: Sequence[Any],
    config: LocomoConfig,
) -> EventStructureMetrics:
    matching_policy = STRUCTURAL_MATCHING_POLICY.format(
        threshold=_format_threshold(config.structural_match_f1_threshold)
    )
    official_by_session: dict[str, list[str]] = {
        summary.session_id: [
            event
            for speaker, events in sorted(summary.events.items())
            for event in events
            if event.strip()
        ]
        for summary in record.event_summaries
    }
    extracted_by_session: dict[str, list[str]] = {}
    for memory in memories:
        session_id = memory.session_id
        if session_id is None:
            continue
        extracted_by_session.setdefault(session_id, []).append(str(memory.content))

    sessions = sorted(set(official_by_session) | set(extracted_by_session))
    official_count = 0
    extracted_count = 0
    matched_official = 0
    matched_extracted = 0
    for session_id in sessions:
        official_events = official_by_session.get(session_id, [])
        extracted_events = extracted_by_session.get(session_id, [])
        official_count += len(official_events)
        extracted_count += len(extracted_events)
        for official in official_events:
            if _any_token_f1_match(official, extracted_events, config):
                matched_official += 1
        for extracted in extracted_events:
            if _any_token_f1_match(extracted, official_events, config):
                matched_extracted += 1
    coverage = matched_official / official_count if official_count else 0.0
    precision = matched_extracted / extracted_count if extracted_count else 0.0
    return EventStructureMetrics(
        metric_kind=STRUCTURAL_PROXY_LABEL,
        matching_policy=matching_policy,
        session_count=len(sessions),
        official_event_count=official_count,
        extracted_event_count=extracted_count,
        matched_official_count=matched_official,
        matched_extracted_count=matched_extracted,
        coverage=coverage,
        precision=precision,
        f1=_harmonic_mean(coverage, precision),
    )


def _format_threshold(threshold: float) -> str:
    return f"{threshold:g}"


def _any_token_f1_match(
    text: str,
    candidates: Sequence[str],
    config: LocomoConfig,
) -> bool:
    return any(
        compute_answer_metrics(text, candidate).token_f1
        >= config.structural_match_f1_threshold
        for candidate in candidates
    )


def _build_manifest(
    config: LocomoConfig,
    run_dir: Path,
    *,
    expected_sample_ids: Sequence[str],
    expected_question_ids: Sequence[str],
) -> RunManifest:
    return RunManifest(
        run_id=run_dir.name,
        artifact_class=_artifact_class(config),
        dataset="locomo",
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
            "structural_proxy_label": STRUCTURAL_PROXY_LABEL,
            "structural_matching_policy": STRUCTURAL_MATCHING_POLICY.format(
                threshold=_format_threshold(config.structural_match_f1_threshold)
            ),
            "reference_time_source": REFERENCE_TIME_SOURCE,
            "evidence_mapping": EVIDENCE_MAPPING,
        },
    )


def _write_summary(
    config: LocomoConfig,
    run_dir: Path,
    manifest: RunManifest,
    expected_sample_ids: Sequence[str],
    bundle: ModelBundle,
) -> LocomoSummary:
    samples = _load_sample_results(run_dir)
    sample_ids = [sample.sample_id for sample in samples]
    sample_validation = _validate_samples(list(expected_sample_ids), sample_ids)
    expected_question_ids = [
        question.question_id for sample in samples for question in sample.questions.values()
    ]
    completed_question_ids = [
        question.question_id
        for sample in samples
        for question in sample.questions.values()
    ]
    question_validation = _validate_question_ids(
        expected_question_ids, completed_question_ids
    )

    methods: dict[str, MethodSummary] = {}
    for method in config.methods:
        methods[method.value] = _summarize_method(method, samples)

    event_structure: dict[str, EventStructureMetrics] = {}
    if samples:
        for mode in ("raw", "etec"):
            mode_samples = [sample for sample in samples if mode in sample.event_structure]
            if mode_samples:
                event_structure[mode] = _aggregate_event_structure(
                    mode_samples, mode, config
                )

    snapshot_ids = [
        str(sample.ingestion.get("event", {}).get("snapshot_id"))
        for sample in samples
        if sample.ingestion.get("event", {}).get("snapshot_id")
    ]
    summary = LocomoSummary(
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
        reader_thinking=_reader_thinking(config),
        reader_format_directive=READER_FORMAT_DIRECTIVE,
        extraction_prompt_version=EXTRACTION_PROMPT_VERSION,
        retrieval_policy_name=RETRIEVAL_POLICY_NAME,
        router_policy_name=ROUTER_POLICY_NAME,
        consolidation_policy_name=CONSOLIDATION_POLICY_NAME,
        reference_time_source=REFERENCE_TIME_SOURCE,
        evidence_mapping=EVIDENCE_MAPPING,
        structural_proxy_label=STRUCTURAL_PROXY_LABEL,
        structural_matching_policy=STRUCTURAL_MATCHING_POLICY.format(
            threshold=_format_threshold(config.structural_match_f1_threshold)
        ),
        structural_match_f1_threshold=config.structural_match_f1_threshold,
        max_input_tokens=config.max_input_tokens,
        max_candidates_per_source=config.max_candidates_per_source,
        max_items_per_source=config.max_items_per_source,
        vector_input_kind=VECTOR_INPUT_KIND,
        extraction_snapshot_ids=sorted(snapshot_ids),
        sample_validation=sample_validation,
        question_validation=question_validation,
        methods=methods,
        event_structure=event_structure,
    )
    _write_combined_artifacts(run_dir, config.methods, samples)
    _write_run_root_artifacts(run_dir, samples)
    summary_path = run_dir / "summary.json"
    summary_path.unlink(missing_ok=True)
    write_json_write_once(summary_path, summary)
    return summary


def _aggregate_event_structure(
    samples: Sequence[SampleResult],
    mode: str,
    config: LocomoConfig,
) -> EventStructureMetrics:
    official_count = 0
    extracted_count = 0
    matched_official = 0
    matched_extracted = 0
    session_count = 0
    for sample in samples:
        structure = sample.event_structure.get(mode)
        if structure is None:
            continue
        official_count += structure.official_event_count
        extracted_count += structure.extracted_event_count
        matched_official += structure.matched_official_count
        matched_extracted += structure.matched_extracted_count
        session_count += structure.session_count
    coverage = matched_official / official_count if official_count else 0.0
    precision = matched_extracted / extracted_count if extracted_count else 0.0
    return EventStructureMetrics(
        metric_kind=STRUCTURAL_PROXY_LABEL,
        matching_policy=STRUCTURAL_MATCHING_POLICY.format(
            threshold=_format_threshold(config.structural_match_f1_threshold)
        ),
        session_count=session_count,
        official_event_count=official_count,
        extracted_event_count=extracted_count,
        matched_official_count=matched_official,
        matched_extracted_count=matched_extracted,
        coverage=coverage,
        precision=precision,
        f1=_harmonic_mean(coverage, precision),
    )


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
        for question in sample.questions.values():
            for method_name, record in sorted(question.methods.items()):
                if record.retrieval is None:
                    continue
                retrieval_rows.append(
                    {
                        "dataset": sample.dataset,
                        "sample_id": sample.sample_id,
                        "question_id": question.question_id,
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
                                question_id=question.question_id,
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
            (sample, question, question.methods[method.value])
            for sample in samples
            for question in sample.questions.values()
            if method.value in question.methods
        ]
        predictions: list[PredictionRecord] = []
        evaluations: list[SampleEvaluation] = []
        retrievals: list[dict[str, Any]] = []
        for sample, question, record in pairs:
            predictions.append(
                PredictionRecord(
                    dataset=sample.dataset,
                    sample_id=sample.sample_id,
                    question_id=question.question_id,
                    prediction=record.prediction,
                    evidence=record.predicted_evidence,
                    latency_ms=record.question_latency_ms,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    metadata={
                        "method": method.value,
                        "question_type": question.question_type,
                        "category": question.category,
                        "retrieval": record.retrieval,
                        "model_cache": {"chat_cache_key": record.model_cache_key},
                    },
                )
            )
            evaluations.append(
                SampleEvaluation(
                    dataset=sample.dataset,
                    sample_id=sample.sample_id,
                    question_id=question.question_id,
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
                        "question_id": question.question_id,
                        **record.retrieval,
                    }
                )
        method_dir = run_dir / method.value
        _rewrite_jsonl(method_dir / "predictions.jsonl", predictions)
        _rewrite_jsonl(method_dir / "samples.jsonl", evaluations)
        _rewrite_jsonl(method_dir / "retrieval.jsonl", retrievals)


def _summarize_method(method: Method, samples: Sequence[SampleResult]) -> MethodSummary:
    records = [
        question.methods[method.value]
        for sample in samples
        for question in sample.questions.values()
        if method.value in question.methods
    ]
    category_records: dict[str, list[MethodSampleRecord]] = {}
    for sample in samples:
        for question in sample.questions.values():
            if method.value in question.methods:
                category_records.setdefault(question.category or "unmapped", []).append(
                    question.methods[method.value]
                )

    category_metrics: dict[str, CategoryMetrics] = {}
    for category in LOCOMO_CATEGORY_BY_ID.values():
        category_records_for_ability = category_records.get(category, [])
        if category_records_for_ability:
            category_metrics[category] = _category_metrics(category_records_for_ability)
    unmapped = category_records.get("unmapped", [])
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
        sample_id for sample_id, count in Counter(expected_sample_ids).items() if count > 1
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


def _validate_question_ids(
    expected_question_ids: Sequence[str],
    completed_question_ids: Sequence[str],
) -> QuestionValidation:
    missing = sorted(set(expected_question_ids) - set(completed_question_ids))
    duplicates = [
        question_id for question_id, count in Counter(expected_question_ids).items() if count > 1
    ]
    duplicates.extend(
        question_id for question_id, count in Counter(completed_question_ids).items() if count > 1
    )
    duplicates = sorted(set(duplicates))
    return QuestionValidation(
        expected_question_count=len(expected_question_ids),
        completed_question_count=len(completed_question_ids),
        missing_question_ids=missing,
        duplicate_question_ids=duplicates,
        valid=not missing and not duplicates,
    )


def _config_report(config: LocomoConfig, config_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "config_path": str(config_path),
        "config_hash": _hash_json(config.model_dump(mode="json")),
        "dataset_hash": _dataset_hash(config.dataset_path),
        "git_commit": current_git_commit(),
        "extraction_prompt_version": EXTRACTION_PROMPT_VERSION,
        "retrieval_policy_name": RETRIEVAL_POLICY_NAME,
        "router_policy_name": ROUTER_POLICY_NAME,
        "consolidation_policy_name": CONSOLIDATION_POLICY_NAME,
        "reader_thinking": _reader_thinking(config),
        "reader_format_directive": READER_FORMAT_DIRECTIVE,
        "reference_time_source": REFERENCE_TIME_SOURCE,
        "evidence_mapping": EVIDENCE_MAPPING,
        "structural_proxy_label": STRUCTURAL_PROXY_LABEL,
        "structural_matching_policy": STRUCTURAL_MATCHING_POLICY.format(
            threshold=_format_threshold(config.structural_match_f1_threshold)
        ),
        "structural_match_f1_threshold": config.structural_match_f1_threshold,
        "providers": config.providers.redacted(),
    }
    return report


def _load_records(config: LocomoConfig) -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    for record in iter_locomo_records(config.dataset_path):
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


def _reference_time(record: NormalizedRecord) -> datetime | None:
    ordered = sorted(record.sessions, key=lambda session: (session.timestamp, session.session_id))
    if not ordered:
        return None
    return ordered[-1].timestamp


def _sample_path(run_dir: Path, sample_id: str) -> Path:
    safe_id = _SAFE_ID_RE.sub("_", sample_id)
    return run_dir / "samples" / f"{safe_id}.json"


def _snapshot_path(run_dir: Path, sample_id: str) -> Path:
    safe_id = _SAFE_ID_RE.sub("_", sample_id)
    return run_dir / "samples" / f"{safe_id}.extraction_snapshot.json"


def _ordered_session_summaries(record: NormalizedRecord) -> list[tuple[str, str, int]]:
    raw_summaries = record.metadata.get("session_summary", {})
    if not isinstance(raw_summaries, dict):
        raw_summaries = {}
    ordered: list[tuple[str, str, int]] = []
    for session in sorted(record.sessions, key=lambda item: (item.timestamp, item.session_id)):
        session_id = session.session_id
        summary = raw_summaries.get(f"{session_id}_summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        ordered.append((session_id, summary, _count_tokens(summary)))
    return ordered


def _category_for(question_type: str | None) -> str | None:
    if question_type is None:
        return None
    try:
        category_id = int(question_type)
    except ValueError:
        return None
    return LOCOMO_CATEGORY_BY_ID.get(category_id)


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


def _harmonic_mean(left: float, right: float) -> float:
    if left == 0.0 or right == 0.0:
        return 0.0
    return 2 * left * right / (left + right)


def _fit_question_only(
    question_id: str, question_line: str, max_input_tokens: int
) -> ContextBuildResult:
    tokens = question_line.split()
    if len(tokens) <= max_input_tokens:
        prompt = question_line
        truncations: tuple[TruncationDecision, ...] = ()
    else:
        prompt = " ".join(tokens[:max_input_tokens])
        truncations = (
            TruncationDecision(
                source_type="question",
                source_id=question_id,
                reason="context_budget_exceeded",
                token_count=len(tokens) - max_input_tokens,
                max_input_tokens=max_input_tokens,
            ),
        )
    return ContextBuildResult(
        prompt=prompt,
        input_tokens=_count_tokens(prompt),
        included_history_turn_ids=(),
        truncations=truncations,
    )


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


def _load_stored_summary(run_dir: Path) -> LocomoSummary:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"finalized run has no stored summary: {summary_path}")
    return LocomoSummary.model_validate_json(summary_path.read_text())


def _new_run_dir(output_root: Path, run_id_prefix: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / f"{run_id_prefix}-{timestamp}"
    run_dir.mkdir()
    return run_dir


def _artifact_class(config: LocomoConfig) -> ArtifactClass:
    if config.provider == "deterministic_fake":
        return ArtifactClass.SMOKE
    return ArtifactClass.PUBLICATION


def _scope(config: LocomoConfig) -> str:
    if config.sample_limit is not None:
        return f"sample_limit={config.sample_limit}"
    return "full"


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


if __name__ == "__main__":
    raise SystemExit(main())
