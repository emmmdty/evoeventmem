from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evoeventmem.router import QueryIntent, QueryRouter, TemporalOperator

DEV_FIXTURE_PATH = Path("tests/fixtures/router/m11_query_router_fixture.json")
EVAL_FIXTURE_PATH = Path("tests/fixtures/router/m11_query_router_eval.json")
MIN_MACRO_F1 = 0.7
MIN_OPERATOR_ACCURACY = 0.7
CONFIDENCE_BINS = [(0.0, 0.4), (0.4, 0.7), (0.7, 1.0)]


def _load(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cases"], f"{path} must contain cases"
    return payload["cases"]


def _queries(path: Path) -> set[str]:
    return {str(case["query"]).lower() for case in _load(path)}


def _case_ids(path: Path) -> set[str]:
    return {str(case["case_id"]) for case in _load(path)}


def test_evaluation_fixture_is_distinct_from_development_fixture() -> None:
    dev_queries = _queries(DEV_FIXTURE_PATH)
    eval_queries = _queries(EVAL_FIXTURE_PATH)
    assert eval_queries.isdisjoint(dev_queries), (
        "evaluation fixture must not reuse development fixture queries"
    )
    dev_ids = _case_ids(DEV_FIXTURE_PATH)
    eval_ids = _case_ids(EVAL_FIXTURE_PATH)
    assert eval_ids.isdisjoint(dev_ids), "case ids must not collide"


def test_every_label_and_operator_has_positive_and_negative_cases() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    for intent in QueryIntent:
        positive = [c for c in cases if c["expected_intent"] == intent.value]
        negative = [c for c in cases if c["expected_intent"] != intent.value]
        assert positive, f"{intent.value} has no positive eval case"
        assert negative, f"{intent.value} has no negative eval case"
    for operator in TemporalOperator:
        positive = [c for c in cases if c["expected_temporal_operator"] == operator.value]
        negative = [c for c in cases if c["expected_temporal_operator"] != operator.value]
        assert positive, f"{operator.value} has no positive operator case"
        assert negative, f"{operator.value} has no negative operator case"


def test_evaluation_fixture_contains_ambiguous_and_adversarial_negatives() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    assert any(c["ambiguity"] for c in cases), "fixture must contain ambiguous cases"
    assert any(c["adversarial"] for c in cases), "fixture must contain adversarial cases"


def test_router_macro_f1_meets_threshold() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    router = QueryRouter()
    predictions = [router.route(str(case["query"])) for case in cases]
    expected = [str(case["expected_intent"]) for case in cases]
    predicted = [decision.intent.value for decision in predictions]
    report = _evaluate(expected, predicted)
    print(f"macro-F1: {report.macro_f1:.3f}")
    print("per-label F1:", {k: round(v, 3) for k, v in report.per_label_f1.items()})
    print(
        "confusion matrix:\n"
        + json.dumps(report.confusion_matrix, indent=2, sort_keys=True)
    )
    assert report.macro_f1 >= MIN_MACRO_F1


def test_temporal_operator_accuracy_meets_threshold() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    router = QueryRouter()
    correct = 0
    total = 0
    for case in cases:
        decision = router.route(str(case["query"]))
        total += 1
        if decision.temporal_constraint.operator.value == str(
            case["expected_temporal_operator"]
        ):
            correct += 1
    accuracy = correct / total
    print(f"temporal operator accuracy: {accuracy:.3f} ({correct}/{total})")
    assert accuracy >= MIN_OPERATOR_ACCURACY


def test_fallback_rate_is_reported_and_bounded() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    router = QueryRouter()
    hybrid_fallbacks = 0
    for case in cases:
        decision = router.route(str(case["query"]))
        if (
            decision.intent is QueryIntent.HYBRID
            and "low_confidence_fallback" in decision.rule_hits
        ):
            hybrid_fallbacks += 1
    fallback_rate = hybrid_fallbacks / len(cases)
    print(f"fallback rate: {fallback_rate:.3f} ({hybrid_fallbacks}/{len(cases)})")
    assert fallback_rate <= 0.35, "fallback rate must stay bounded on the eval fixture"


def test_confidence_bins_are_populated() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    router = QueryRouter()
    bins = {f"{low:.1f}-{high:.1f}": 0 for low, high in CONFIDENCE_BINS}
    for case in cases:
        decision = router.route(str(case["query"]))
        for low, high in CONFIDENCE_BINS:
            if low <= decision.confidence < high:
                bins[f"{low:.1f}-{high:.1f}"] += 1
                break
        else:
            bins[f"{CONFIDENCE_BINS[-1][0]:.1f}-{CONFIDENCE_BINS[-1][1]:.1f}"] += 1
    print("confidence bins:", bins)
    for label, count in bins.items():
        assert count > 0, f"confidence bin {label} is empty"


def test_evaluation_decision_fields_are_observable() -> None:
    cases = _load(EVAL_FIXTURE_PATH)
    router = QueryRouter()
    for case in cases:
        decision = router.route(str(case["query"]))
        assert decision.reason
        assert decision.rule_hits
        assert decision.temporal_constraint.operator is not None


@dataclass
class _Report:
    macro_f1: float
    per_label_f1: dict[str, float]
    confusion_matrix: dict[str, dict[str, int]]


def _evaluate(expected: list[str], predicted: list[str]) -> _Report:
    labels = sorted({*expected, *predicted})
    confusion: dict[str, dict[str, int]] = {
        label: {other: 0 for other in labels} for label in labels
    }
    for gold, pred in zip(expected, predicted, strict=True):
        confusion[gold][pred] += 1
    per_label_f1: dict[str, float] = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        per_label_f1[label] = (
            (2 * precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
    macro_f1 = sum(per_label_f1.values()) / len(labels)
    return _Report(macro_f1=macro_f1, per_label_f1=per_label_f1, confusion_matrix=confusion)