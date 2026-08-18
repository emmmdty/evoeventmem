from __future__ import annotations

from datetime import UTC, datetime

from benchmarks.mechanism.gold import GoldAction, GoldPair
from benchmarks.mechanism.probes import (
    Probe,
    ProbeRetrievalResult,
    build_probes,
    compute_m4_for_probe_arm,
)


def _pair(
    *,
    question_id: str = "q1",
    t_old: datetime = datetime(2022, 6, 1, tzinfo=UTC),
    t_q: datetime = datetime(2023, 6, 1, tzinfo=UTC),
    gold_action: GoldAction = GoldAction.SUPERSEDE,
    old_value: str = "Austin",
    new_value: str = "Seattle",
    old_value_turn_ids: list[str] | None = None,
) -> GoldPair:
    return GoldPair(
        question_id=question_id,
        subject="me",
        attribute="city",
        old_value=old_value,
        old_value_turn_ids=(
            old_value_turn_ids
            if old_value_turn_ids is not None
            else ["session-old:0"]
        ),
        new_value=new_value,
        new_value_turn_ids=["session-new:0"],
        t_q=t_q,
        t_old=t_old,
        gold_action=gold_action,
    )


def test_build_probes_now_past_between() -> None:
    """A pair with a 2-year gap produces all three probe kinds."""
    pair = _pair(
        t_old=datetime(2021, 6, 1, tzinfo=UTC),
        t_q=datetime(2023, 6, 1, tzinfo=UTC),
    )
    probes = build_probes([pair])
    kinds = sorted(p.kind for p in probes)
    assert kinds == ["between", "now", "past"]
    now = next(p for p in probes if p.kind == "now")
    assert now.expected_operator == "NONE"
    assert now.gold_inside_window_turn_ids == ["session-new:0"]
    assert now.gold_outside_window_turn_ids == ["session-old:0"]
    past = next(p for p in probes if p.kind == "past")
    assert past.expected_operator == "BEFORE"
    assert past.query == "What was me's city before 2022?"
    assert past.gold_inside_window_turn_ids == ["session-old:0"]
    assert past.gold_outside_window_turn_ids == ["session-new:0"]
    between = next(p for p in probes if p.kind == "between")
    assert between.expected_operator == "BETWEEN"
    assert between.query == "What was me's city between 2021 and 2023?"
    assert sorted(between.gold_inside_window_turn_ids) == [
        "session-new:0",
        "session-old:0",
    ]


def test_build_probes_same_year_only_now() -> None:
    """A pair within the same year produces only a now probe (no past/between)."""
    pair = _pair(
        t_old=datetime(2023, 3, 1, tzinfo=UTC),
        t_q=datetime(2023, 6, 1, tzinfo=UTC),
    )
    probes = build_probes([pair])
    assert len(probes) == 1
    assert probes[0].kind == "now"


def test_build_probes_skips_add_without_old_side() -> None:
    """ADD-without-old-value pairs (e.g. 22d2cb42) produce no probes."""
    pair = _pair(
        gold_action=GoldAction.ADD,
        old_value="",
        old_value_turn_ids=[],
        t_old=datetime(2023, 6, 1, tzinfo=UTC),
    )
    probes = build_probes([pair])
    assert probes == []


def test_compute_m4_exclusion_hit_and_contamination() -> None:
    """M4 ExclusionHit detects temporal_interval_excluded; Contamination counts
    packed items carrying gold_outside_window sessions."""
    probe = Probe(
        probe_id="past-q1",
        question_id="q1",
        kind="past",
        query="What was me's city before 2022?",
        reference_time=datetime(2023, 6, 1, tzinfo=UTC),
        expected_operator="BEFORE",
        gold_inside_window_turn_ids=["session-old:0"],
        gold_outside_window_turn_ids=["session-new:0"],
        subject="me",
        attribute="city",
    )
    result = ProbeRetrievalResult(
        arm="full",
        probe_id="past-q1",
        question_id="q1",
        packed_items=[
            {
                "evidence_refs": [
                    {"raw_turn_id": "session-old:0", "session_id": "session-old"}
                ],
                "historical": True,
            },
            {
                "evidence_refs": [
                    {"raw_turn_id": "session-new:0", "session_id": "session-new"}
                ],
                "historical": False,
            },
        ],
        exclusions=[
            {"memory_id": "m1", "reason": "temporal_interval_excluded", "details": {}},
            {"memory_id": "m2", "reason": "budget_exceeded", "details": {}},
        ],
        packing_bound=True,
        reader_calls=0,
        extractor_calls=0,
    )
    m4 = compute_m4_for_probe_arm(result, probe)
    assert m4["exclusion_hit"] == 1
    assert m4["contamination"] == 0.5  # 1 of 2 items carries outside evidence
    assert m4["valid_retention"] == 1  # inside evidence is in packed
    assert m4["total_packed"] == 2
    assert m4["historical_packed_count"] == 1
    assert m4["temporal_interval_exclusion_count"] == 1


def test_compute_m4_no_exclusion_no_contamination() -> None:
    """When no temporal exclusion and no outside evidence, ExclusionHit=0 and
    Contamination=0."""
    probe = Probe(
        probe_id="now-q1",
        question_id="q1",
        kind="now",
        query="What is me's city now?",
        reference_time=datetime(2023, 6, 1, tzinfo=UTC),
        expected_operator="NONE",
        gold_inside_window_turn_ids=["session-new:0"],
        gold_outside_window_turn_ids=["session-old:0"],
        subject="me",
        attribute="city",
    )
    result = ProbeRetrievalResult(
        arm="full",
        probe_id="now-q1",
        question_id="q1",
        packed_items=[
            {
                "evidence_refs": [
                    {"raw_turn_id": "session-new:0", "session_id": "session-new"}
                ],
                "historical": False,
            },
        ],
        exclusions=[],
        packing_bound=False,
        reader_calls=0,
        extractor_calls=0,
    )
    m4 = compute_m4_for_probe_arm(result, probe)
    assert m4["exclusion_hit"] == 0
    assert m4["contamination"] == 0.0
    assert m4["valid_retention"] == 1
    assert m4["total_packed"] == 1
