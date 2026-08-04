from __future__ import annotations

import json
from pathlib import Path

import pytest

from evoeventmem.router import (
    QueryFeatures,
    QueryIntent,
    QueryRouter,
    QueryRouterService,
    QueryRoutingDecision,
)

FIXTURE_PATH = Path("tests/fixtures/router/m11_query_router_fixture.json")
ALL_LABELS = list(QueryIntent)
MIN_FIXTURE_MACRO_F1 = 0.8


def _fixture_cases() -> list[dict[str, str]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "query-router.annotations.v1"
    cases = payload["cases"]
    assert cases, "router fixture must contain at least one case"
    for case in cases:
        QueryIntent(case["expected_intent"])
    return cases


@pytest.fixture(scope="module")
def router() -> QueryRouter:
    return QueryRouter()


@pytest.mark.parametrize("label", ALL_LABELS)
def test_label_has_positive_and_negative_fixture_cases(label: QueryIntent) -> None:
    cases = _fixture_cases()
    positive = [case for case in cases if case["expected_intent"] == label.value]
    negative = [case for case in cases if case["expected_intent"] != label.value]
    assert positive, f"{label.value} has no positive fixture case"
    assert negative, f"{label.value} has no negative fixture case"


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["case_id"])
def test_fixture_case_routes_to_expected_intent(
    router: QueryRouter,
    case: dict[str, str],
) -> None:
    decision = router.route(case["query"])
    assert decision.intent.value == case["expected_intent"], (
        f"query {case['query']!r} routed to {decision.intent.value} "
        f"but expected {case['expected_intent']}"
    )


def test_no_memory_negative_cases(router: QueryRouter) -> None:
    for query in (
        "What is Caroline's favorite color?",
        "When did Caroline move to Seattle?",
        "How do I write a test?",
    ):
        assert router.route(query).intent is not QueryIntent.NO_MEMORY


def test_semantic_negative_cases(router: QueryRouter) -> None:
    for query in (
        "When did Caroline move to Seattle?",
        "Did I tell you about my trip?",
        "How do I install the package?",
    ):
        assert router.route(query).intent is not QueryIntent.SEMANTIC


def test_temporal_negative_cases(router: QueryRouter) -> None:
    for query in (
        "What is Caroline's favorite color?",
        "Who is Caroline related to?",
        "Hello!",
    ):
        assert router.route(query).intent is not QueryIntent.TEMPORAL


def test_graph_negative_cases(router: QueryRouter) -> None:
    for query in (
        "What is Caroline's favorite color?",
        "When did Caroline move to Seattle?",
        "Did I tell you about my trip?",
    ):
        assert router.route(query).intent is not QueryIntent.GRAPH


def test_episodic_negative_cases(router: QueryRouter) -> None:
    for query in (
        "What is Caroline's favorite color?",
        "Who is Caroline related to?",
        "How do I create a memory?",
    ):
        assert router.route(query).intent is not QueryIntent.EPISODIC


def test_procedural_negative_cases(router: QueryRouter) -> None:
    for query in (
        "What is Caroline's favorite color?",
        "When did Caroline move to Seattle?",
        "Hello!",
    ):
        assert router.route(query).intent is not QueryIntent.PROCEDURAL


def test_hybrid_negative_cases(router: QueryRouter) -> None:
    for query in (
        "What is Caroline's favorite color?",
        "Hello!",
        "Who is Caroline related to?",
    ):
        assert router.route(query).intent is not QueryIntent.HYBRID


def test_unknown_query_falls_back_to_hybrid_with_observable_reason(
    router: QueryRouter,
) -> None:
    decision = router.route("What do you think about the current situation?")
    assert decision.intent is QueryIntent.HYBRID
    assert "no_entity_no_memory_cue" in decision.rule_hits
    assert "hybrid fallback" in decision.reason.lower()


def test_low_confidence_query_falls_back_to_hybrid_with_observable_reason(
    router: QueryRouter,
) -> None:
    decision = router.route("What is the weather like?")
    assert decision.intent is QueryIntent.HYBRID
    assert "low_confidence_fallback" in decision.rule_hits
    assert "hybrid" in decision.reason.lower()
    assert decision.confidence < router.MIN_COMMIT_CONFIDENCE


def test_empty_query_falls_back_to_hybrid(router: QueryRouter) -> None:
    decision = router.route("   ")
    assert decision.intent is QueryIntent.HYBRID
    assert "empty_query" in decision.rule_hits
    assert decision.confidence == 0.0


def test_committed_routes_have_confidence_at_or_above_threshold(
    router: QueryRouter,
) -> None:
    for case in _fixture_cases():
        if QueryIntent(case["expected_intent"]) is QueryIntent.HYBRID:
            continue
        decision = router.route(case["query"])
        assert decision.confidence >= router.MIN_COMMIT_CONFIDENCE


def test_decision_is_deterministic(router: QueryRouter) -> None:
    for case in _fixture_cases():
        first = router.route(case["query"])
        second = router.route(case["query"])
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_mixed_temporal_relation_query_routes_temporal() -> None:
    decision = QueryRouter().route("When did the relationship start?")
    assert decision.intent is QueryIntent.TEMPORAL
    assert decision.features.has_relation_cue is True


def test_name_phrase_query_routes_semantic() -> None:
    decision = QueryRouter().route("What is the last name of the CEO?")
    assert decision.intent is QueryIntent.SEMANTIC
    assert decision.features.has_name_phrase is True


def test_weak_temporal_cues_do_not_beat_episodic_cue() -> None:
    decision = QueryRouter().route("What happened during our last call?")
    assert decision.intent is QueryIntent.EPISODIC


class _FakeEntityLexicon:
    def __init__(self, names: set[str]) -> None:
        self._names = {name.casefold() for name in names}

    def contains(self, name: str) -> bool:
        return name.casefold() in self._names


def test_entity_lexicon_enables_entity_detection_for_lowercase_names() -> None:
    query = "What is yamada favorite color?"
    without_lexicon = QueryRouter().route(query)
    assert without_lexicon.features.has_entity is False
    assert without_lexicon.intent is QueryIntent.HYBRID

    with_lexicon = QueryRouter(entity_lexicon=_FakeEntityLexicon({"yamada"})).route(query)
    assert with_lexicon.features.has_entity is True
    assert with_lexicon.intent is QueryIntent.SEMANTIC


def test_confidence_is_bounded_evidence_strength(router: QueryRouter) -> None:
    temporal = router.route("When did Caroline move to Seattle?")
    single_cue = router.route("What happened first in the meeting?")
    assert 0.1 <= temporal.confidence <= 1.0
    assert temporal.confidence > single_cue.confidence
    assert temporal.features.strong_temporal_count >= 1


def test_decision_features_and_provenance_are_observable(router: QueryRouter) -> None:
    decision = router.route("When did Caroline move to Seattle?")
    assert decision.policy_name == router.POLICY_NAME
    assert isinstance(decision.features, QueryFeatures)
    assert decision.features.has_temporal_cue is True
    assert decision.features.has_entity is True
    assert decision.rule_hits
    assert decision.reason


def test_service_records_and_persists_decisions() -> None:
    service = QueryRouterService()
    queries = ["Hello!", "What is Caroline's favorite color?"]
    for query in queries:
        service.route(query)
    decisions = service.list_decisions()
    assert [decision.query for decision in decisions] == queries
    assert all(
        decision.intent is not QueryIntent.HYBRID and decision.confidence > 0.0
        for decision in decisions
    )

    payload = service.export_jsonl()
    assert [record["query"] for record in payload] == queries
    for record in payload:
        assert record["policy_name"] == QueryRouter.POLICY_NAME
        assert record["rule_hits"]
        assert record["reason"]
        json.dumps(record, sort_keys=True)
        QueryRoutingDecision.model_validate(record)


def test_macro_f1_fixture_report(router: QueryRouter) -> None:
    cases = _fixture_cases()
    predicted = [router.route(case["query"]) for case in cases]
    assert [decision.intent.value for decision in predicted] == [
        case["expected_intent"] for case in cases
    ]
    macro_f1 = _macro_f1(
        [case["expected_intent"] for case in cases],
        [decision.intent.value for decision in predicted],
    )
    print(f"router fixture macro-F1 over {len(cases)} cases: {macro_f1:.3f}")
    assert macro_f1 >= MIN_FIXTURE_MACRO_F1


def _macro_f1(
    expected: list[str],
    predicted: list[str],
) -> float:
    labels = sorted({*expected, *predicted})
    label_f1s: list[float] = []
    for label in labels:
        expected_binary = [item == label for item in expected]
        predicted_binary = [item == label for item in predicted]
        label_f1s.append(_binary_f1(expected_binary, predicted_binary))
    return sum(label_f1s) / len(label_f1s)


def _binary_f1(
    expected: list[bool],
    predicted: list[bool],
) -> float:
    true_positive = sum(1 for gold, pred in zip(expected, predicted, strict=True) if gold and pred)
    false_positive = sum(
        1 for gold, pred in zip(expected, predicted, strict=True) if not gold and pred
    )
    false_negative = sum(
        1 for gold, pred in zip(expected, predicted, strict=True) if gold and not pred
    )
    denominator = 2 * true_positive + false_positive + false_negative
    if denominator == 0:
        return 0.0
    return (2 * true_positive) / denominator
