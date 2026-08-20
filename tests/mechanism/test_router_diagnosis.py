"""Unit tests for the S3 Step 1 router-diagnosis confusion-matrix logic.

Tests the pure ``confusion_matrix`` function with fakes; does not exercise
the live router (that is covered by the CLI run on the real v2 run dir).
"""

from __future__ import annotations

import pytest

from benchmarks.mechanism.router_diagnosis import confusion_matrix
from evoeventmem.router import QueryIntent


def test_confusion_matrix_perfect_classification() -> None:
    gold = [QueryIntent.SEMANTIC, QueryIntent.TEMPORAL, QueryIntent.SEMANTIC]
    pred = [QueryIntent.SEMANTIC, QueryIntent.TEMPORAL, QueryIntent.SEMANTIC]
    result = confusion_matrix(gold, pred)
    assert result["n"] == 3
    assert result["correct"] == 3
    assert result["accuracy"] == 1.0
    assert result["per_class"]["semantic"]["precision"] == 1.0
    assert result["per_class"]["semantic"]["recall"] == 1.0
    assert result["per_class"]["semantic"]["f1"] == 1.0


def test_confusion_matrix_misclassification() -> None:
    gold = [QueryIntent.SEMANTIC, QueryIntent.TEMPORAL]
    pred = [QueryIntent.TEMPORAL, QueryIntent.SEMANTIC]
    result = confusion_matrix(gold, pred)
    assert result["n"] == 2
    assert result["correct"] == 0
    assert result["accuracy"] == 0.0
    # semantic: 0 TP, 1 FP, 1 FN
    assert result["per_class"]["semantic"]["precision"] == 0.0
    assert result["per_class"]["semantic"]["recall"] == 0.0
    # temporal: 0 TP, 1 FP, 1 FN
    assert result["per_class"]["temporal"]["precision"] == 0.0
    assert result["per_class"]["temporal"]["recall"] == 0.0


def test_confusion_matrix_partial_misroute() -> None:
    # 2 semantic gold (1 correct, 1 routed to hybrid), 1 temporal gold (correct)
    gold = [QueryIntent.SEMANTIC, QueryIntent.SEMANTIC, QueryIntent.TEMPORAL]
    pred = [QueryIntent.SEMANTIC, QueryIntent.HYBRID, QueryIntent.TEMPORAL]
    result = confusion_matrix(gold, pred)
    assert result["correct"] == 2
    assert result["accuracy"] == pytest.approx(2 / 3)
    assert result["per_class"]["semantic"]["recall"] == 0.5
    assert result["per_class"]["semantic"]["precision"] == 1.0
    assert result["per_class"]["temporal"]["recall"] == 1.0
    assert result["per_class"]["temporal"]["precision"] == 1.0
    # hybrid: 0 TP, 1 FP, 0 FN -> precision 0
    assert result["per_class"]["hybrid"]["precision"] == 0.0
    # cell counts
    assert result["matrix"]["semantic"]["hybrid"] == 1
    assert result["matrix"]["semantic"]["semantic"] == 1


def test_confusion_matrix_single_class_gold() -> None:
    # Degenerate single-class case (50-question slice: all gold = semantic).
    gold = [QueryIntent.SEMANTIC] * 4
    pred = [
        QueryIntent.SEMANTIC,
        QueryIntent.SEMANTIC,
        QueryIntent.HYBRID,
        QueryIntent.TEMPORAL,
    ]
    result = confusion_matrix(gold, pred)
    assert result["n"] == 4
    assert result["correct"] == 2
    assert result["accuracy"] == 0.5
    assert result["per_class"]["semantic"]["support"] == 4
    assert result["per_class"]["semantic"]["recall"] == 0.5
    assert result["per_class"]["semantic"]["precision"] == 1.0


def test_confusion_matrix_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        confusion_matrix(
            [QueryIntent.SEMANTIC, QueryIntent.TEMPORAL],
            [QueryIntent.SEMANTIC],
        )
