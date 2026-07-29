from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from benchmarks.common.artifacts import (
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

DEFAULT_OUTPUT_ROOT = Path("artifacts/m04_context_baselines")


class DatasetConfig(BaseModel):
    name: Literal["longmemeval", "locomo"]
    path: Path


class ContextBaselineConfig(BaseModel):
    baseline: Literal["no_memory", "full_context"]
    run_id_prefix: str = Field(min_length=1)
    model_id: str = Field(default="deterministic-local-fake", min_length=1)
    max_input_tokens: int = Field(gt=0)
    datasets: list[DatasetConfig] = Field(min_length=1)


@dataclass(frozen=True)
class TruncationDecision:
    source_type: str
    source_id: str
    reason: str
    token_count: int
    max_input_tokens: int


@dataclass(frozen=True)
class ContextBuildResult:
    prompt: str
    input_tokens: int
    included_history_turn_ids: tuple[str, ...]
    truncations: tuple[TruncationDecision, ...]


@dataclass(frozen=True)
class ChatResult:
    text: str
    output_tokens: int


class NoMemoryContextBuilder:
    def __init__(self, max_input_tokens: int) -> None:
        self.max_input_tokens = max_input_tokens

    def build(
        self,
        question: NormalizedQuestion,
        history: Iterable[NormalizedSession],
    ) -> ContextBuildResult:
        del history
        question_line = f"Question: {question.question}"
        return _fit_question_only(question.question_id, question_line, self.max_input_tokens)


class FullContextBuilder:
    def __init__(self, max_input_tokens: int) -> None:
        self.max_input_tokens = max_input_tokens

    def build(
        self,
        question: NormalizedQuestion,
        history: Iterable[NormalizedSession],
    ) -> ContextBuildResult:
        question_line = f"Question: {question.question}"
        question_tokens = _count_tokens(question_line)
        if question_tokens > self.max_input_tokens:
            return _fit_question_only(question.question_id, question_line, self.max_input_tokens)

        remaining_tokens = self.max_input_tokens - question_tokens
        accepted: list[_HistoryLine] = []
        truncations: list[TruncationDecision] = []
        history_lines = _history_lines(history)
        for line in reversed(history_lines):
            if line.token_count <= remaining_tokens:
                accepted.append(line)
                remaining_tokens -= line.token_count
            else:
                truncations.append(
                    TruncationDecision(
                        source_type="turn",
                        source_id=line.turn_id,
                        reason="context_budget_exceeded",
                        token_count=line.token_count,
                        max_input_tokens=self.max_input_tokens,
                    )
                )

        accepted.reverse()
        prompt_lines = [line.text for line in accepted]
        prompt_lines.append(question_line)
        prompt = "\n".join(prompt_lines)
        return ContextBuildResult(
            prompt=prompt,
            input_tokens=_count_tokens(prompt),
            included_history_turn_ids=tuple(line.turn_id for line in accepted),
            truncations=tuple(reversed(truncations)),
        )


class DeterministicFixtureChatModel:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def generate(self, prompt: str) -> ChatResult:
        normalized_prompt = prompt.lower()
        if "moved to seattle" in normalized_prompt:
            text = "Seattle"
        elif "live in austin" in normalized_prompt:
            text = "Austin"
        elif "support group yesterday" in normalized_prompt:
            text = "7 May 2023"
        else:
            text = ""
        return ChatResult(text=text, output_tokens=_count_tokens(text))


@dataclass(frozen=True)
class _HistoryLine:
    turn_id: str
    text: str
    token_count: int


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic M04 context baselines.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    config = load_context_baseline_config(args.config)
    run_dir = _new_run_dir(args.output_root, config.run_id_prefix)
    summary = run_context_baseline(config, run_dir)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


def load_context_baseline_config(path: Path) -> ContextBaselineConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ContextBaselineConfig.model_validate(payload)


def run_context_baseline(config: ContextBaselineConfig, output_dir: Path) -> RunSummary:
    records = _load_records(config.datasets)
    metadata = RunMetadata(
        run_id=output_dir.name,
        model_id=config.model_id,
        config_hash=_hash_json(config.model_dump(mode="json")),
        git_commit=current_git_commit(),
        dataset_fingerprint=_dataset_fingerprint([dataset.path for dataset in config.datasets]),
        metadata={
            "baseline": config.baseline,
            "datasets": [dataset.name for dataset in config.datasets],
            "max_input_tokens": config.max_input_tokens,
        },
    )
    builder = _make_builder(config.baseline, config.max_input_tokens)
    model = DeterministicFixtureChatModel(config.model_id)

    predictions: list[PredictionRecord] = []
    samples: list[SampleEvaluation] = []
    for record in records:
        for question in record.questions:
            context = builder.build(question, record.sessions)
            started = perf_counter()
            chat_result = model.generate(context.prompt)
            latency_ms = (perf_counter() - started) * 1000
            prediction = PredictionRecord(
                dataset=record.dataset,
                sample_id=record.sample_id,
                question_id=question.question_id,
                prediction=chat_result.text,
                evidence=[],
                latency_ms=latency_ms,
                input_tokens=context.input_tokens,
                output_tokens=chat_result.output_tokens,
                metadata={
                    "baseline": config.baseline,
                    "context": {
                        "included_history_turn_ids": list(context.included_history_turn_ids),
                        "truncations": [asdict(decision) for decision in context.truncations],
                    },
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

    predictions_path = output_dir / "predictions.jsonl"
    samples_path = output_dir / "samples.jsonl"
    summary_path = output_dir / "summary.json"
    write_jsonl_write_once(predictions_path, predictions)
    write_jsonl_write_once(samples_path, samples)
    summary = _summarize(metadata, samples, predictions_path, samples_path)
    write_json_write_once(summary_path, summary)
    return summary


def _make_builder(
    baseline: Literal["no_memory", "full_context"], max_input_tokens: int
) -> NoMemoryContextBuilder | FullContextBuilder:
    if baseline == "no_memory":
        return NoMemoryContextBuilder(max_input_tokens)
    return FullContextBuilder(max_input_tokens)


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


def _history_lines(history: Iterable[NormalizedSession]) -> list[_HistoryLine]:
    lines: list[_HistoryLine] = []
    sorted_sessions = sorted(history, key=lambda session: (session.timestamp, session.session_id))
    for session in sorted_sessions:
        sorted_turns = sorted(
            session.turns,
            key=lambda turn: (turn.timestamp or session.timestamp, turn.turn_id),
        )
        for turn in sorted_turns:
            timestamp = (turn.timestamp or session.timestamp).isoformat()
            text = f"{session.session_id} {timestamp} {turn.speaker}: {turn.content}"
            lines.append(
                _HistoryLine(turn_id=turn.turn_id, text=text, token_count=_count_tokens(text))
            )
    return lines


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
    samples: list[SampleEvaluation],
    predictions_path: Path,
    samples_path: Path,
) -> RunSummary:
    sample_count = len(samples)
    total_input_tokens = sum(sample.input_tokens or 0 for sample in samples)
    total_output_tokens = sum(sample.output_tokens or 0 for sample in samples)
    return RunSummary(
        metadata=metadata,
        sample_count=sample_count,
        exact_match=_mean([sample.exact_match for sample in samples]),
        token_f1=_mean([sample.token_f1 for sample in samples]),
        evidence_precision=_mean([sample.evidence_precision for sample in samples]),
        evidence_recall=_mean([sample.evidence_recall for sample in samples]),
        evidence_f1=_mean([sample.evidence_f1 for sample in samples]),
        total_latency_ms=sum(sample.latency_ms for sample in samples),
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        predictions_path=str(predictions_path),
        samples_path=str(samples_path),
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


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
    main()
