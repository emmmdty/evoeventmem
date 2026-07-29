from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from benchmarks.common.artifacts import (
    EvidencePrediction,
    PredictionRecord,
    RunMetadata,
    RunSummary,
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
    iter_locomo_records,
    iter_longmemeval_records,
)
from evoeventmem.core.ports import (
    ChatMessage,
    ChatModel,
    EmbeddingModel,
    Reranker,
)
from evoeventmem.infra.openai_compatible import (
    OpenAICompatibleChatClient,
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingClient,
)
from evoeventmem.models.cache import CachedChatModel, CachedEmbeddingModel, FileModelCache
from evoeventmem.models.fakes import DeterministicFakeChatModel, DeterministicFakeEmbeddingModel

DEFAULT_OUTPUT_ROOT = Path("artifacts/m05_vector_baseline")


class DatasetConfig(BaseModel):
    name: Literal["longmemeval", "locomo"]
    path: Path


class LiveProviderConfig(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    chat_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    timeout_s: float = Field(default=30.0, gt=0)


class VectorBaselineConfig(BaseModel):
    baseline: Literal["vector_rag"]
    run_id_prefix: str = Field(min_length=1)
    provider: Literal["deterministic_fake", "openai_compatible"] = "deterministic_fake"
    chat_model_id: str = Field(default="deterministic-local-fake", min_length=1)
    embedding_model_id: str = Field(default="deterministic-local-embedding", min_length=1)
    max_input_tokens: int = Field(gt=0)
    chunk_token_limit: int = Field(gt=0)
    top_k: int = Field(gt=0)
    live_provider: LiveProviderConfig | None = None
    datasets: list[DatasetConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def require_explicit_live_provider(self) -> VectorBaselineConfig:
        if self.provider == "openai_compatible" and self.live_provider is None:
            raise ValueError("openai_compatible provider requires explicit live_provider config")
        return self


@dataclass(frozen=True)
class VectorChunk:
    chunk_id: str
    source_session_id: str
    source_turn_id: str
    text: str
    token_count: int
    position: int


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: str
    source_session_id: str
    source_turn_id: str
    text: str
    score: float
    token_count: int
    position: int


@dataclass(frozen=True)
class RetrievalRecord:
    dataset: str
    sample_id: str
    question_id: str
    query: str
    selected_context: list[dict[str, Any]]


def load_vector_baseline_config(path: Path) -> VectorBaselineConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return VectorBaselineConfig.model_validate(payload)


def run_vector_baseline(config: VectorBaselineConfig, output_dir: Path) -> RunSummary:
    records = _load_records(config.datasets)
    cache = FileModelCache(output_dir / "model_cache")
    chat_model, embedding_model = _make_models(config, cache)
    metadata = RunMetadata(
        run_id=output_dir.name,
        model_id=chat_model.model_id,
        config_hash=_hash_json(config.model_dump(mode="json")),
        git_commit=current_git_commit(),
        dataset_fingerprint=_dataset_fingerprint([dataset.path for dataset in config.datasets]),
        metadata={
            "baseline": config.baseline,
            "provider": config.provider,
            "datasets": [dataset.name for dataset in config.datasets],
            "max_input_tokens": config.max_input_tokens,
            "chunk_token_limit": config.chunk_token_limit,
            "top_k": config.top_k,
            "embedding_model_id": embedding_model.model_id,
        },
    )

    predictions: list[PredictionRecord] = []
    samples: list[SampleEvaluation] = []
    retrieval_records: list[RetrievalRecord] = []
    for record in records:
        index = VectorIndex.from_sessions(
            record.sessions,
            embedding_model,
            chunk_token_limit=config.chunk_token_limit,
        )
        for question in record.questions:
            selected = index.search(question.question, top_k=config.top_k)
            selected = _pack_context(question, selected, config.max_input_tokens)
            prompt = _build_prompt(question, selected)
            started = perf_counter()
            chat_result = chat_model.generate([ChatMessage(role="user", content=prompt)])
            latency_ms = (perf_counter() - started) * 1000
            selected_payload = [_selected_context_payload(chunk) for chunk in selected]
            predicted_evidence = _evidence_from_selected(record.dataset, selected)
            retrieval_records.append(
                RetrievalRecord(
                    dataset=record.dataset,
                    sample_id=record.sample_id,
                    question_id=question.question_id,
                    query=question.question,
                    selected_context=selected_payload,
                )
            )
            prediction = PredictionRecord(
                dataset=record.dataset,
                sample_id=record.sample_id,
                question_id=question.question_id,
                prediction=chat_result.text,
                evidence=predicted_evidence,
                latency_ms=latency_ms,
                input_tokens=chat_result.input_tokens,
                output_tokens=chat_result.output_tokens,
                metadata={
                    "baseline": config.baseline,
                    "retrieval": {"selected_context": selected_payload},
                    "model_cache": {"chat_cache_key": chat_result.cache_key},
                },
            )
            answer_metrics = compute_answer_metrics(question.answer, prediction.prediction)
            evidence_metrics = compute_evidence_metrics(question.evidence, prediction.evidence)
            samples.append(
                SampleEvaluation(
                    dataset=record.dataset,
                    sample_id=record.sample_id,
                    question_id=question.question_id,
                    exact_match=answer_metrics.exact_match,
                    token_f1=answer_metrics.token_f1,
                    evidence_precision=evidence_metrics.precision,
                    evidence_recall=evidence_metrics.recall,
                    evidence_f1=evidence_metrics.f1,
                    latency_ms=prediction.latency_ms,
                    input_tokens=prediction.input_tokens,
                    output_tokens=prediction.output_tokens,
                )
            )
            predictions.append(prediction)

    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "predictions.jsonl"
    samples_path = output_dir / "samples.jsonl"
    retrieval_path = output_dir / "retrieval.jsonl"
    summary_path = output_dir / "summary.json"
    write_jsonl_write_once(predictions_path, predictions)
    write_jsonl_write_once(samples_path, samples)
    write_jsonl_write_once(retrieval_path, [asdict(record) for record in retrieval_records])
    summary = _summarize(metadata, samples, predictions_path, samples_path, retrieval_path)
    write_json_write_once(summary_path, summary)
    return summary


class VectorIndex:
    def __init__(
        self,
        chunks: Sequence[VectorChunk],
        vectors: Sequence[tuple[float, ...]],
        embedding_model: EmbeddingModel,
        reranker: Reranker | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self._vectors = list(vectors)
        self._embedding_model = embedding_model
        self._reranker = reranker

    @classmethod
    def from_sessions(
        cls,
        sessions: Iterable[NormalizedSession],
        embedding_model: EmbeddingModel,
        chunk_token_limit: int,
        reranker: Reranker | None = None,
    ) -> VectorIndex:
        chunks = _chunks_from_sessions(sessions, chunk_token_limit)
        vectors = [
            response.vector
            for response in embedding_model.embed_texts([chunk.text for chunk in chunks])
        ]
        return cls(chunks, vectors, embedding_model, reranker)

    def search(self, query: str, top_k: int) -> list[ScoredChunk]:
        query_vector = self._embedding_model.embed_texts([query])[0].vector
        scored = [
            ScoredChunk(
                chunk_id=chunk.chunk_id,
                source_session_id=chunk.source_session_id,
                source_turn_id=chunk.source_turn_id,
                text=chunk.text,
                score=_cosine_similarity(query_vector, vector),
                token_count=chunk.token_count,
                position=chunk.position,
            )
            for chunk, vector in zip(self._chunks, self._vectors, strict=True)
        ]
        if self._reranker is not None:
            rerank_scores = self._reranker.score(query, [chunk.text for chunk in scored])
            scored = [
                ScoredChunk(
                    chunk_id=chunk.chunk_id,
                    source_session_id=chunk.source_session_id,
                    source_turn_id=chunk.source_turn_id,
                    text=chunk.text,
                    score=rerank_score,
                    token_count=chunk.token_count,
                    position=chunk.position,
                )
                for chunk, rerank_score in zip(scored, rerank_scores, strict=True)
            ]
        return sorted(
            scored,
            key=lambda chunk: (-chunk.score, chunk.position, chunk.chunk_id),
        )[:top_k]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the vector RAG baseline.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)

    config = load_vector_baseline_config(args.config)
    run_dir = _new_run_dir(args.output_root, config.run_id_prefix)
    summary = run_vector_baseline(config, run_dir)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def _make_models(
    config: VectorBaselineConfig, cache: FileModelCache
) -> tuple[ChatModel, EmbeddingModel]:
    if config.provider == "deterministic_fake":
        chat_model: ChatModel = DeterministicFakeChatModel(config.chat_model_id)
        embedding_model: EmbeddingModel = DeterministicFakeEmbeddingModel(config.embedding_model_id)
    else:
        if config.live_provider is None:
            raise ValueError("openai_compatible provider requires explicit live_provider config")
        chat_model = OpenAICompatibleChatClient(
            OpenAICompatibleConfig(
                base_url=config.live_provider.base_url,
                api_key=config.live_provider.api_key,
                model=config.live_provider.chat_model,
                timeout_s=config.live_provider.timeout_s,
            )
        )
        embedding_model = OpenAICompatibleEmbeddingClient(
            OpenAICompatibleConfig(
                base_url=config.live_provider.base_url,
                api_key=config.live_provider.api_key,
                model=config.live_provider.embedding_model,
                timeout_s=config.live_provider.timeout_s,
            )
        )
    return CachedChatModel(chat_model, cache), CachedEmbeddingModel(embedding_model, cache)


def _chunks_from_sessions(
    sessions: Iterable[NormalizedSession], chunk_token_limit: int
) -> list[VectorChunk]:
    chunks: list[VectorChunk] = []
    position = 0
    sorted_sessions = sorted(sessions, key=lambda session: (session.timestamp, session.session_id))
    for session in sorted_sessions:
        sorted_turns = sorted(
            session.turns,
            key=lambda turn: (turn.timestamp or session.timestamp, turn.turn_id),
        )
        for turn in sorted_turns:
            timestamp = (turn.timestamp or session.timestamp).isoformat()
            prefix = f"{session.session_id} {timestamp} {turn.speaker}:"
            token_chunks = _split_tokens(turn.content, chunk_token_limit)
            for chunk_index, token_chunk in enumerate(token_chunks):
                text = f"{prefix} {' '.join(token_chunk)}"
                chunks.append(
                    VectorChunk(
                        chunk_id=f"{turn.turn_id}:chunk-{chunk_index}",
                        source_session_id=session.session_id,
                        source_turn_id=turn.turn_id,
                        text=text,
                        token_count=_count_tokens(text),
                        position=position,
                    )
                )
                position += 1
    return chunks


def _split_tokens(text: str, chunk_token_limit: int) -> list[list[str]]:
    tokens = text.split()
    if not tokens:
        return []
    return [
        tokens[index : index + chunk_token_limit]
        for index in range(0, len(tokens), chunk_token_limit)
    ]


def _pack_context(
    question: NormalizedQuestion, chunks: Sequence[ScoredChunk], max_input_tokens: int
) -> list[ScoredChunk]:
    question_tokens = _count_tokens(f"Question: {question.question}")
    remaining_tokens = max(max_input_tokens - question_tokens, 0)
    selected: list[ScoredChunk] = []
    for chunk in chunks:
        if chunk.token_count <= remaining_tokens:
            selected.append(chunk)
            remaining_tokens -= chunk.token_count
    return selected


def _build_prompt(question: NormalizedQuestion, chunks: Sequence[ScoredChunk]) -> str:
    lines = [f"Context: {chunk.text}" for chunk in chunks]
    lines.append(f"Question: {question.question}")
    return "\n".join(lines)


def _selected_context_payload(chunk: ScoredChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "source_session_id": chunk.source_session_id,
        "source_turn_id": chunk.source_turn_id,
        "score": chunk.score,
        "text": chunk.text,
        "token_count": chunk.token_count,
    }


def _evidence_from_selected(
    dataset: str,
    chunks: Sequence[ScoredChunk],
) -> list[EvidencePrediction]:
    evidence: list[EvidencePrediction] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        if dataset == "longmemeval":
            source_type = "longmemeval_session"
            source_id = chunk.source_session_id
            locator = "answer_session_ids"
        elif dataset == "locomo":
            source_type = "locomo_dialogue"
            source_id = chunk.source_turn_id
            locator = "qa.evidence"
        else:
            source_type = "retrieved_context"
            source_id = chunk.source_turn_id
            locator = "retrieved_context"
        key = (source_type, source_id)
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            EvidencePrediction(
                source_type=source_type,
                source_id=source_id,
                locator=locator,
                quote=chunk.text,
            )
        )
    return evidence


def _load_records(datasets: list[DatasetConfig]) -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    for dataset in datasets:
        if dataset.name == "longmemeval":
            records.extend(iter_longmemeval_records(dataset.path))
        else:
            records.extend(iter_locomo_records(dataset.path))
    return records


def _summarize(
    metadata: RunMetadata,
    samples: Sequence[SampleEvaluation],
    predictions_path: Path,
    samples_path: Path,
    retrieval_path: Path,
) -> RunSummary:
    summary_metadata = metadata.model_copy(
        update={"metadata": {**metadata.metadata, "retrieval_path": str(retrieval_path)}}
    )
    return RunSummary(
        metadata=summary_metadata,
        sample_count=len(samples),
        exact_match=_mean([sample.exact_match for sample in samples]),
        token_f1=_mean([sample.token_f1 for sample in samples]),
        evidence_precision=_mean([sample.evidence_precision for sample in samples]),
        evidence_recall=_mean([sample.evidence_recall for sample in samples]),
        evidence_f1=_mean([sample.evidence_f1 for sample in samples]),
        total_latency_ms=sum(sample.latency_ms for sample in samples),
        total_input_tokens=sum(sample.input_tokens or 0 for sample in samples),
        total_output_tokens=sum(sample.output_tokens or 0 for sample in samples),
        predictions_path=str(predictions_path),
        samples_path=str(samples_path),
    )


def _mean(values: Sequence[float | int]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    )
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _count_tokens(text: str) -> int:
    return len(text.split())


def _hash_json(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _dataset_fingerprint(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _new_run_dir(output_root: Path, run_id_prefix: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / f"{run_id_prefix}-{timestamp}"
    run_dir.mkdir()
    return run_dir


if __name__ == "__main__":
    raise SystemExit(main())
