from __future__ import annotations

import json

from benchmarks.analysis.taxonomy import (
    FailureCategory,
    answer_tokens,
    build_review_rows,
    classify_failure,
    evidence_mapping_gap,
    gold_token_recall,
    write_review_sheet,
)


def _classify(**kwargs) -> FailureCategory:
    defaults = {
        "method": "full",
        "category": "single-hop",
        "gold_answer": "blue house",
        "prediction": "green car",
        "context_text": "blue house words",
        "context_truncated": False,
    }
    defaults.update(kwargs)
    return classify_failure(**defaults)


def test_adversarial_is_classified_before_recoverability() -> None:
    assert (
        _classify(category="adversarial", gold_answer=None, prediction="I do not know")
        is FailureCategory.ADVERSARIAL_NO_GOLD
    )


def test_recoverable_answer_wrong_prediction() -> None:
    assert (
        _classify(context_text="the answer is blue house here")
        is FailureCategory.ANSWER_RECOVERABLE_WRONG
    )


def test_unrecoverable_answer() -> None:
    assert (
        _classify(context_text="completely unrelated tokens")
        is FailureCategory.ANSWER_NOT_RECOVERABLE
    )


def test_empty_prediction() -> None:
    assert _classify(prediction="") is FailureCategory.EMPTY_PREDICTION


def test_no_gold_answer() -> None:
    assert _classify(gold_answer=None) is FailureCategory.NO_GOLD_ANSWER


def test_no_memory_baseline() -> None:
    assert _classify(method="no_memory") is FailureCategory.NO_MEMORY_BASELINE


def test_context_truncation() -> None:
    assert (
        _classify(context_truncated=True)
        is FailureCategory.CONTEXT_BUDGET_TRUNCATION
    )


def test_gold_token_recall() -> None:
    assert gold_token_recall("blue house", "blue house here") == 1.0
    assert gold_token_recall("blue house", "nothing related") == 0.0
    assert gold_token_recall(None, "anything") is None


def test_answer_tokens_normalize_punctuation_and_articles() -> None:
    assert answer_tokens("The Blue, house!") == ["blue", "house"]


def test_evidence_mapping_gap() -> None:
    assert evidence_mapping_gap(["D0:1"], []) is True
    assert evidence_mapping_gap([], []) is False
    assert evidence_mapping_gap(["D0:1"], ["D0:1"]) is False


def test_build_review_rows_emits_only_failures() -> None:
    questions = [
        {
            "question_id": f"q{index}",
            "sample_id": "s1",
            "category": "single-hop",
            "gold_answer": "blue house",
            "prediction": "green car" if index % 2 else "blue house",
            "exact_match": 0.0 if index % 2 else 1.0,
            "gold_evidence": ["D0:1"],
            "predicted_evidence": [],
            "context_text": "blue house words",
            "context_truncated": False,
        }
        for index in range(20)
    ]
    rows = build_review_rows(
        run_id="run-1", config_hash="sha256:c", method="full", questions=questions
    )
    assert len(rows) == 10
    assert all(row["failure_category"] for row in rows)
    assert all(row["evidence_mapping_gap"] is True for row in rows)


def test_build_review_rows_counts_at_least_fifty() -> None:
    questions = [
        {
            "question_id": f"q{index}",
            "sample_id": "s1",
            "category": "single-hop",
            "gold_answer": "blue house",
            "prediction": "green car",
            "exact_match": 0.0,
            "gold_evidence": ["D0:1"],
            "predicted_evidence": [],
            "context_text": "unrelated text",
            "context_truncated": False,
        }
        for index in range(60)
    ]
    rows = build_review_rows(
        run_id="run-1", config_hash="sha256:c", method="full", questions=questions
    )
    assert len(rows) == 60
    assert all(
        row["failure_category"] == FailureCategory.ANSWER_NOT_RECOVERABLE.value
        for row in rows
    )


def test_write_review_sheet_writes_jsonl(tmp_path) -> None:
    rows = [
        {
            "run_id": "run-1",
            "config_hash": "sha256:c",
            "method": "full",
            "question_id": "q1",
            "failure_category": "answer_recoverable_wrong_prediction",
        }
    ]
    path = tmp_path / "review.jsonl"
    write_review_sheet(rows, path)
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert loaded == rows
