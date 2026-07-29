from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

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
    NormalizedEvidenceRef,
    NormalizedRecord,
    iter_locomo_records,
    iter_longmemeval_records,
)

FIXTURES = Path("tests/fixtures")
DEFAULT_OUTPUT_ROOT = Path("artifacts/m03_smoke_eval")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic fixture smoke evaluation.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    run_dir = _new_run_dir(args.output_root)
    summary = run_smoke_eval(run_dir)
    print(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


def run_smoke_eval(output_dir: Path) -> RunSummary:
    records = [
        *iter_longmemeval_records(FIXTURES / "longmemeval/oracle_tiny.json"),
        *iter_locomo_records(FIXTURES / "locomo/locomo_tiny.json"),
    ]
    metadata = RunMetadata(
        run_id=output_dir.name,
        model_id="fixture-oracle",
        config_hash=_hash_json({"mode": "fixture-oracle", "metrics_version": "deterministic-v1"}),
        git_commit=current_git_commit(),
        dataset_fingerprint=_dataset_fingerprint(
            [
                FIXTURES / "longmemeval/oracle_tiny.json",
                FIXTURES / "locomo/locomo_tiny.json",
            ]
        ),
        metadata={"datasets": ["longmemeval", "locomo"]},
    )

    predictions: list[PredictionRecord] = []
    samples: list[SampleEvaluation] = []
    for record in records:
        record_predictions, record_samples = _evaluate_record(record)
        predictions.extend(record_predictions)
        samples.extend(record_samples)

    predictions_path = output_dir / "predictions.jsonl"
    samples_path = output_dir / "samples.jsonl"
    summary_path = output_dir / "summary.json"

    write_jsonl_write_once(predictions_path, predictions)
    write_jsonl_write_once(samples_path, samples)
    summary = _summarize(metadata, samples, predictions_path, samples_path)
    write_json_write_once(summary_path, summary)
    return summary


def _evaluate_record(
    record: NormalizedRecord,
) -> tuple[list[PredictionRecord], list[SampleEvaluation]]:
    predictions: list[PredictionRecord] = []
    samples: list[SampleEvaluation] = []
    for question in record.questions:
        started = perf_counter()
        predicted_evidence = [_to_evidence_prediction(evidence) for evidence in question.evidence]
        prediction_text = question.answer or ""
        latency_ms = (perf_counter() - started) * 1000
        prediction = PredictionRecord(
            dataset=record.dataset,
            sample_id=record.sample_id,
            question_id=question.question_id,
            prediction=prediction_text,
            evidence=predicted_evidence,
            latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=len(prediction_text.split()),
            metadata={"source": "normalized_fixture_answer"},
        )
        answer_metrics = compute_answer_metrics(question.answer, prediction.prediction)
        evidence_metrics = compute_evidence_metrics(question.evidence, prediction.evidence)
        sample = SampleEvaluation(
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
        predictions.append(prediction)
        samples.append(sample)
    return predictions, samples


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


def _to_evidence_prediction(evidence: NormalizedEvidenceRef) -> EvidencePrediction:
    return EvidencePrediction(
        source_type=evidence.source_type,
        source_id=evidence.source_id,
        locator=evidence.locator,
        quote=evidence.quote,
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _hash_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _dataset_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _new_run_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / f"fixture-oracle-{timestamp}"
    run_dir.mkdir()
    return run_dir


if __name__ == "__main__":
    main()
