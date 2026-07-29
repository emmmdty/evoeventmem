from __future__ import annotations

import json

import pytest

from benchmarks.common.artifacts import (
    EvidencePrediction,
    PredictionRecord,
    RunMetadata,
    write_json_write_once,
    write_jsonl_write_once,
)
from benchmarks.common.metrics import (
    compute_answer_metrics,
    compute_evidence_metrics,
)


def test_answer_metrics_normalize_case_articles_and_punctuation() -> None:
    metrics = compute_answer_metrics("The Seattle, WA!", "seattle wa")

    assert metrics.exact_match == 1.0
    assert metrics.token_f1 == 1.0


def test_answer_metrics_score_partial_overlap() -> None:
    metrics = compute_answer_metrics("Seattle Washington", "Seattle")

    assert metrics.exact_match == 0.0
    assert metrics.token_f1 == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    ("gold", "prediction", "expected_exact", "expected_f1"),
    [
        ("", "", 1.0, 1.0),
        ("Seattle", "", 0.0, 0.0),
        ("", "Seattle", 0.0, 0.0),
    ],
)
def test_answer_metrics_handle_empty_answers(
    gold: str, prediction: str, expected_exact: float, expected_f1: float
) -> None:
    metrics = compute_answer_metrics(gold, prediction)

    assert metrics.exact_match == expected_exact
    assert metrics.token_f1 == expected_f1


def test_evidence_metrics_deduplicate_and_score_partial_overlap() -> None:
    gold = [
        EvidencePrediction(source_type="turn", source_id="D1:1"),
        EvidencePrediction(source_type="turn", source_id="D1:1"),
        EvidencePrediction(source_type="turn", source_id="D1:2"),
    ]
    predicted = [
        EvidencePrediction(source_type="turn", source_id="D1:1"),
        EvidencePrediction(source_type="turn", source_id="D1:3"),
    ]

    metrics = compute_evidence_metrics(gold, predicted)

    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.5)
    assert metrics.f1 == pytest.approx(0.5)


def test_evidence_metrics_handle_empty_sets() -> None:
    evidence = EvidencePrediction(source_type="turn", source_id="D1")

    assert compute_evidence_metrics([], []).model_dump() == {
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert compute_evidence_metrics([], [evidence]).f1 == 0.0
    assert compute_evidence_metrics([evidence], []).f1 == 0.0


def test_run_artifacts_are_write_once(tmp_path) -> None:
    metadata = RunMetadata(
        run_id="run-test",
        model_id="fixture-oracle",
        config_hash="sha256:config",
        git_commit="abc123",
        dataset_fingerprint="sha256:dataset",
    )
    prediction = PredictionRecord(
        dataset="longmemeval",
        sample_id="sample-1",
        question_id="question-1",
        prediction="Seattle",
        evidence=[EvidencePrediction(source_type="session", source_id="session-new")],
        latency_ms=1.25,
        input_tokens=10,
        output_tokens=1,
    )

    summary_path = tmp_path / "summary.json"
    predictions_path = tmp_path / "predictions.jsonl"

    write_json_write_once(summary_path, metadata.model_dump(mode="json"))
    write_jsonl_write_once(predictions_path, [prediction])

    assert json.loads(summary_path.read_text())["run_id"] == "run-test"
    assert json.loads(predictions_path.read_text().strip())["question_id"] == "question-1"
    with pytest.raises(FileExistsError):
        write_json_write_once(summary_path, metadata.model_dump(mode="json"))
    with pytest.raises(FileExistsError):
        write_jsonl_write_once(predictions_path, [prediction])


def test_jsonl_write_once_does_not_leave_partial_target_on_failure(tmp_path) -> None:
    predictions_path = tmp_path / "predictions.jsonl"

    def broken_records():
        yield {"question_id": "q1"}
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_jsonl_write_once(predictions_path, broken_records())

    assert not predictions_path.exists()
