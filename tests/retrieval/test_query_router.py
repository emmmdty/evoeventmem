from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evoeventmem.router import (
    QueryFeatures,
    QueryIntent,
    QueryRouter,
    QueryRouterService,
    QueryRoutingDecision,
    TemporalConstraint,
    TemporalOperator,
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


TEMPORAL_OPERATORS = [op.value for op in TemporalOperator]


@pytest.mark.parametrize("operator", TEMPORAL_OPERATORS)
def test_temporal_operator_enum_is_complete(operator: str) -> None:
    assert TemporalOperator(operator).value == operator


def test_unconstrained_when_has_no_latest_constraint() -> None:
    decision = QueryRouter().route("When did Caroline move?")
    assert decision.intent is QueryIntent.TEMPORAL
    assert decision.temporal_constraint.operator is TemporalOperator.NONE


def test_decision_exposes_temporal_constraint_and_reason() -> None:
    decision = QueryRouter().route("When did Caroline move?")
    assert isinstance(decision.temporal_constraint, TemporalConstraint)
    assert decision.temporal_constraint.operator is TemporalOperator.NONE
    assert decision.temporal_constraint.lower_bound_utc is None
    assert decision.temporal_constraint.upper_bound_utc is None
    assert decision.reason
    assert decision.rule_hits


def test_at_operator_parses_utc_date_bound() -> None:
    decision = QueryRouter().route("When did Caroline move in 2021?")
    assert decision.intent is QueryIntent.TEMPORAL
    assert decision.temporal_constraint.operator is TemporalOperator.AT
    assert decision.temporal_constraint.lower_bound_utc is not None
    assert decision.temporal_constraint.lower_bound_utc.tzinfo is not None


def test_before_operator_parses_utc_bound() -> None:
    decision = QueryRouter().route("What happened before 2022?")
    assert decision.temporal_constraint.operator is TemporalOperator.BEFORE
    assert decision.temporal_constraint.upper_bound_utc is not None
    assert decision.temporal_constraint.upper_bound_utc.tzinfo is not None


def test_after_operator_parses_utc_bound() -> None:
    decision = QueryRouter().route("What happened after 2020?")
    assert decision.temporal_constraint.operator is TemporalOperator.AFTER
    assert decision.temporal_constraint.lower_bound_utc is not None
    assert decision.temporal_constraint.lower_bound_utc.tzinfo is not None


def test_between_operator_parses_lower_and_upper_utc_bounds() -> None:
    decision = QueryRouter().route("What happened between 2020 and 2022?")
    assert decision.temporal_constraint.operator is TemporalOperator.BETWEEN
    assert decision.temporal_constraint.lower_bound_utc is not None
    assert decision.temporal_constraint.upper_bound_utc is not None
    assert decision.temporal_constraint.lower_bound_utc.tzinfo is not None
    assert decision.temporal_constraint.upper_bound_utc.tzinfo is not None


def test_earliest_operator_applies_within_relevant_pool() -> None:
    decision = QueryRouter().route("When was the first time Caroline visited Lisbon?")
    assert decision.temporal_constraint.operator is TemporalOperator.EARLIEST


def test_latest_operator_applies_within_relevant_pool() -> None:
    decision = QueryRouter().route("When did Caroline last move?")
    assert decision.temporal_constraint.operator is TemporalOperator.LATEST


def test_sequence_operator_is_observable() -> None:
    decision = QueryRouter().route("What order did the meetings happen in?")
    assert decision.temporal_constraint.operator is TemporalOperator.SEQUENCE


def test_duration_operator_is_observable() -> None:
    decision = QueryRouter().route("How long did the trip last?")
    assert decision.temporal_constraint.operator is TemporalOperator.DURATION


def test_relative_date_requires_utc_reference_time() -> None:
    decision = QueryRouter(reference_time=datetime(2024, 6, 1, tzinfo=UTC)).route(
        "What happened last month?"
    )
    assert decision.intent is QueryIntent.TEMPORAL
    assert decision.temporal_constraint.operator is not TemporalOperator.NONE
    assert decision.temporal_constraint.lower_bound_utc is not None or (
        decision.temporal_constraint.upper_bound_utc is not None
    )


def test_non_temporal_query_has_none_temporal_constraint() -> None:
    decision = QueryRouter().route("What is Caroline's favorite color?")
    assert decision.temporal_constraint.operator is TemporalOperator.NONE


def test_temporal_decision_records_matched_spans_and_rule_hits() -> None:
    decision = QueryRouter().route("What happened after 2020?")
    assert decision.temporal_constraint.matched_spans
    assert decision.temporal_constraint.reason
    assert any(
        "after" in span.lower() or "2020" in span
        for span in decision.temporal_constraint.matched_spans
    )


# ---------------------------------------------------------------------------
# S8 Step 1: router rule enhancement coverage.
# Each new regex pattern gets ≥3 cases per the S8 prompt: positive,
# near-synonym negative, and cross-class interference negative.
# ---------------------------------------------------------------------------


class TestS8TemporalReasoningPatterns:
    """S8 Step 1: temporal-reasoning phrasings added to
    ``_TEMPORAL_STRONG_RE``."""

    def test_how_many_weeks_ago_routes_temporal(self) -> None:
        # Positive: "How many weeks ago did I meet my aunt" → TEMPORAL.
        decision = QueryRouter().route("How many weeks ago did I meet my aunt?")
        assert decision.intent is QueryIntent.TEMPORAL
        assert decision.features.strong_temporal_count >= 1

    def test_word_number_weeks_ago_routes_temporal(self) -> None:
        # Positive: "two weeks ago" (word number, no digit) → TEMPORAL.
        decision = QueryRouter().route(
            "I mentioned a sports event two weeks ago. What was the event?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_a_month_ago_routes_temporal(self) -> None:
        # Positive: "a month ago" bare-article form → TEMPORAL.
        decision = QueryRouter().route(
            "What charity event did I participate in a month ago?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_how_long_have_i_been_routes_temporal(self) -> None:
        # Positive: "How long have I been using X" → TEMPORAL.
        decision = QueryRouter().route("How long have I been using my Fitbit?")
        assert decision.intent is QueryIntent.TEMPORAL

    def test_how_long_had_i_been_routes_temporal(self) -> None:
        # Positive: "How long had I been X when Y" → TEMPORAL.
        decision = QueryRouter().route(
            "How long had I been taking guitar lessons when I bought the amp?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_how_many_days_before_routes_temporal(self) -> None:
        # Positive: "How many days before X did I Y" → TEMPORAL.
        decision = QueryRouter().route(
            "How many days before my birthday did I order the gift?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_how_many_days_passed_between_routes_temporal(self) -> None:
        # Positive: "How many days had passed between X and Y" → TEMPORAL.
        decision = QueryRouter().route(
            "How many days had passed between the launch and the post-mortem?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_which_event_first_routes_temporal(self) -> None:
        # Positive: "Which event happened first" → TEMPORAL.
        decision = QueryRouter().route(
            "Which event happened first, my cousin's wedding or the reunion?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_in_the_order_routes_temporal(self) -> None:
        # Positive: "in the order from first to last" → TEMPORAL.
        decision = QueryRouter().route(
            "What is the order of the three trips I took, from earliest to latest?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_how_long_measurement_stays_semantic(self) -> None:
        # Cross-class interference: "How long is my daily commute" is a
        # measurement (strong_fact), not temporal ordering — must stay
        # SEMANTIC, not flip to TEMPORAL when the new how-long patterns
        # are added.
        decision = QueryRouter().route("How long is my daily commute?")
        assert decision.intent is QueryIntent.SEMANTIC

    def test_what_is_caroline_color_stays_semantic(self) -> None:
        # Cross-class interference: plain fact lookup, no temporal cue,
        # must stay SEMANTIC.
        decision = QueryRouter().route("What is Caroline's favorite color?")
        assert decision.intent is QueryIntent.SEMANTIC


class TestS8KnowledgeUpdatePatterns:
    """S8 Step 1: knowledge-update phrasings added to
    ``_KNOWLEDGE_UPDATE_RE``."""

    def test_how_often_routes_temporal(self) -> None:
        # Positive: "How often do I attend X" → TEMPORAL (KU gold).
        decision = QueryRouter().route(
            "How often do I attend yoga classes to help with my anxiety?"
        )
        assert decision.intent is QueryIntent.TEMPORAL
        assert decision.features.has_knowledge_update_cue is True

    def test_have_i_tried_routes_temporal(self) -> None:
        # Positive: "How many Korean restaurants have I tried in my city"
        # → TEMPORAL (KU gold).
        decision = QueryRouter().route(
            "How many Korean restaurants have I tried in my city?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_my_current_routes_temporal(self) -> None:
        # Positive: "What is my current highest score" → TEMPORAL.
        decision = QueryRouter().route(
            "What is my current highest score in Ticket to Ride?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_my_former_routes_temporal(self) -> None:
        # Positive: "my former manager Rachel" → TEMPORAL (KU).
        decision = QueryRouter().route(
            "How many women are on the team led by my former manager Rachel?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_just_started_routes_temporal(self) -> None:
        # Positive: "just started my new role" → TEMPORAL (KU).
        decision = QueryRouter().route(
            "How many engineers do I lead when I just started my new role?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_have_i_since_routes_temporal(self) -> None:
        # Positive: "have I written since I started" → TEMPORAL (KU).
        decision = QueryRouter().route(
            "How many short stories have I written since I started writing regularly?"
        )
        assert decision.intent is QueryIntent.TEMPORAL

    def test_my_previous_occupation_stays_semantic(self) -> None:
        # Cross-class interference: the M11 fixture case
        # ``semantic_my_previous`` contracts "my previous + noun" to
        # SEMANTIC. The S8 KU pattern intentionally excludes "my
        # previous" so this contract holds.
        decision = QueryRouter().route("What was my previous occupation?")
        assert decision.intent is QueryIntent.SEMANTIC

    def test_what_is_caroline_color_stays_semantic(self) -> None:
        # Cross-class interference: plain fact, no KU cue, must stay
        # SEMANTIC.
        decision = QueryRouter().route("What is Caroline's favorite color?")
        assert decision.intent is QueryIntent.SEMANTIC


class TestS8MultiSessionPatterns:
    """S8 Step 1: multi-session aggregation cues (``_MULTI_SESSION_AGGREGATION_RE``)
    promote HYBRID routing."""

    def test_in_total_routes_hybrid(self) -> None:
        # Positive: "How many hours have I spent playing games in total"
        # → HYBRID.
        decision = QueryRouter().route(
            "How many hours have I spent playing games in total?"
        )
        assert decision.intent is QueryIntent.HYBRID
        assert decision.features.has_multi_session_cue is True

    def test_combined_routes_hybrid(self) -> None:
        # Positive: "three road trip destinations combined" → HYBRID.
        decision = QueryRouter().route(
            "How many hours did I spend driving to three road trip destinations combined?"
        )
        assert decision.intent is QueryIntent.HYBRID

    def test_how_many_different_routes_hybrid(self) -> None:
        # Positive: "How many different doctors did I visit" → HYBRID.
        decision = QueryRouter().route("How many different doctors did I visit?")
        assert decision.intent is QueryIntent.HYBRID

    def test_in_the_last_month_routes_hybrid(self) -> None:
        # Positive: "How many plants did I acquire in the last month"
        # → HYBRID (multi-session aggregation, not temporal-anchored).
        decision = QueryRouter().route(
            "How many plants did I acquire in the last month?"
        )
        assert decision.intent is QueryIntent.HYBRID

    def test_in_the_last_two_months_routes_hybrid(self) -> None:
        # Positive: word-number "two months" timeframe → HYBRID.
        decision = QueryRouter().route(
            "How many pieces of jewelry did I acquire in the last two months?"
        )
        assert decision.intent is QueryIntent.HYBRID

    def test_how_many_weeks_ago_stays_temporal(self) -> None:
        # Cross-class interference: "How many weeks ago" is temporal-
        # reasoning (single past event), NOT multi-session aggregation.
        # Must NOT promote to HYBRID.
        decision = QueryRouter().route(
            "How many weeks ago did I meet up with my aunt?"
        )
        assert decision.intent is QueryIntent.TEMPORAL
        assert decision.features.has_multi_session_cue is False

    def test_what_is_caroline_color_stays_semantic(self) -> None:
        # Cross-class interference: plain fact lookup, no aggregation
        # cue, must stay SEMANTIC (not HYBRID fallback).
        decision = QueryRouter().route("What is Caroline's favorite color?")
        assert decision.intent is QueryIntent.SEMANTIC


class TestS8AssistantRecallPattern:
    """S8 Step 1: single-session-assistant recall phrasings
    (``_ASSISTANT_RECALL_RE``) promote SEMANTIC routing and suppress
    spurious TEMPORAL cues from stray day-of-week / "last time" words."""

    def test_previous_chat_remind_me_routes_semantic(self) -> None:
        # Positive: "I'm checking our previous chat ... Can you remind me
        # what was the rotation for Admon on a Sunday?" → SEMANTIC
        # despite "Sunday" (strong temporal) and "what was" (fact).
        decision = QueryRouter().route(
            "I'm checking our previous chat about the shift rotation sheet. "
            "Can you remind me what was the rotation for Admon on a Sunday?"
        )
        assert decision.intent is QueryIntent.SEMANTIC
        assert decision.features.has_assistant_recall_cue is True

    def test_you_recommended_routes_semantic(self) -> None:
        # Positive: "you recommended last time" → SEMANTIC (recall cue
        # suppresses "last time" temporal).
        decision = QueryRouter().route(
            "I'm planning my trip to Amsterdam again and I was wondering, "
            "what was the name of that hostel near the Red Light District "
            "that you recommended last time?"
        )
        assert decision.intent is QueryIntent.SEMANTIC

    def test_follow_up_on_previous_conversation_routes_semantic(self) -> None:
        # Positive: "I wanted to follow up on our previous conversation"
        # → SEMANTIC.
        decision = QueryRouter().route(
            "I wanted to follow up on our previous conversation about "
            "front-end and back-end development. Can you clarify the "
            "framework choice?"
        )
        assert decision.intent is QueryIntent.SEMANTIC

    def test_did_i_tell_you_stays_episodic(self) -> None:
        # Cross-class interference: the M11 fixture case ``episodic_trip``
        # ("Did I tell you about my trip to Japan?") contracts EPISODIC.
        # The assistant-recall pattern does not match "Did I tell you"
        # (it requires "you" before the verb), so EPISODIC is preserved.
        decision = QueryRouter().route("Did I tell you about my trip to Japan?")
        assert decision.intent is QueryIntent.EPISODIC

    def test_when_did_caroline_move_stays_temporal(self) -> None:
        # Cross-class interference: "When did Caroline move to Seattle?"
        # is a temporal-reasoning query (M11 fixture) and must NOT be
        # captured by the assistant-recall SEMANTIC boost.
        decision = QueryRouter().route("When did Caroline move to Seattle?")
        assert decision.intent is QueryIntent.TEMPORAL


# ---------------------------------------------------------------------------
# T6: router dead-code fix — BEFORE/AFTER without year must return NONE
# before DURATION/SEQUENCE/RELATIVE/FIRST/LAST checks.
# ---------------------------------------------------------------------------


def test_before_without_year_returns_none() -> None:
    """'What happened before the concert?' → TemporalOperator.NONE"""
    decision = QueryRouter().route("What happened before the concert?")
    assert decision.temporal_constraint.operator is TemporalOperator.NONE
    assert "temporal_relation_without_date" in decision.temporal_constraint.rule_hits


def test_after_without_year_returns_none() -> None:
    """'What happened after the meeting?' → TemporalOperator.NONE"""
    decision = QueryRouter().route("What happened after the meeting?")
    assert decision.temporal_constraint.operator is TemporalOperator.NONE
    assert "temporal_relation_without_date" in decision.temporal_constraint.rule_hits


def test_before_with_year_returns_before() -> None:
    """'What happened before 2023?' → TemporalOperator.BEFORE"""
    decision = QueryRouter().route("What happened before 2023?")
    assert decision.temporal_constraint.operator is TemporalOperator.BEFORE
    assert decision.temporal_constraint.upper_bound_utc is not None


def test_before_with_stray_first_not_misclassified() -> None:
    """'What was the first thing before the concert?' → NONE, not EARLIEST"""
    decision = QueryRouter().route("What was the first thing before the concert?")
    assert decision.temporal_constraint.operator is TemporalOperator.NONE
    assert decision.temporal_constraint.operator is not TemporalOperator.EARLIEST
