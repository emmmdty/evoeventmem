"""S1b real-data reachability test: do the four-gate SUPERSEDE conditions
ever co-occur on real LongMemEval extraction output?

Gate contract (consolidation.py:869-886):

    not multi_valued                                   (i.e. _is_multi_valued is False)
    AND _same_fact_slot(source, target) is True
    AND not _same_fact_value(source, target)
    AND _intervals_overlap(source_start, source_end, target_start, target_end) is True

If the four gates co-occur on at least one pair within the 5-question
extraction snapshot, the test PASSES — SUPERSEDE is empirically reachable on
real LLM output, and S2 has reason to expect SUPERSEDE > 0.

If the four gates do NOT co-occur, the test is XFAIL with a printed
breakdown of which gate blocked the most pairs (R3 = multi_valued=True
over-flagging is the *expected* blocker per the S1b scope note; S1b
explicitly does NOT fix R3). This is a real negative result, not a failure —
S2 measures the R3 block rate on 50 questions and decides whether to pivot.

This test does NOT claim SUPERSEDE > 0 empirically — 5 questions are too
small for a statistically meaningful trigger-rate claim. It only checks
reachability (does any pair satisfy all four gates?).
"""

from __future__ import annotations

import json
import os
from itertools import combinations
from pathlib import Path

import pytest

from evoeventmem.consolidation import (
    _interval,
    _intervals_overlap,
    _is_multi_valued,
    _same_fact_slot,
    _same_fact_value,
)
from evoeventmem.domain.models import MemoryRecord

# S1c: snapshot path is parameterized via the EEM_S1B_SNAPSHOT_PATH env
# var so S1c can point the same reachability test at the v3 snapshot
# (`runs/s1c/smoke5/extraction_snapshot.json`) without modifying the
# reachability logic. Defaults to the S1b path for backwards compat.
SNAPSHOT_PATH = Path(
    os.environ.get(
        "EEM_S1B_SNAPSHOT_PATH",
        "runs/s1b/smoke5/extraction_snapshot.json",
    )
)


def _load_real_events() -> list[tuple[str, list[MemoryRecord]]]:
    """Return per-sample lists of MemoryRecord parsed from the combined snapshot.

    Pairs are only enumerated within a sample (same conversation_id); cross-
    sample pairs are not SUPERSEDE candidates because they belong to different
    users / conversations.
    """
    if not SNAPSHOT_PATH.exists():
        pytest.skip(
            f"smoke snapshot not generated at {SNAPSHOT_PATH}; run "
            "`uv run python -m benchmarks.longmemeval.run --config "
            "configs/longmemeval/smoke5-mimo.toml --sample-ids e47becba 118b2229 "
            "51a45a95 58bf7951 1e043500 --extraction-only --run-dir "
            "runs/s1b/smoke5` (S1b) or `runs/s1c/smoke5` (S1c). To point "
            "this test at a non-default snapshot, set "
            "EEM_S1B_SNAPSHOT_PATH=<path>."
        )
    payload = json.loads(SNAPSHOT_PATH.read_bytes())
    if not isinstance(payload, list):
        pytest.fail(f"unexpected snapshot shape: {type(payload).__name__}")
    per_sample: list[tuple[str, list[MemoryRecord]]] = []
    for snapshot in payload:
        sample_id = (
            snapshot.get("conversation_id")
            or snapshot.get("snapshot_id")
            or "<unknown>"
        )
        raw_events = snapshot.get("events") or []
        if not isinstance(raw_events, list):
            continue
        memories: list[MemoryRecord] = []
        for raw in raw_events:
            if not isinstance(raw, dict):
                continue
            try:
                memories.append(MemoryRecord.model_validate(raw))
            except Exception:
                # Skip events that fail strict validation; reachability is
                # about whether *any* pair hits the gates, not full coverage.
                continue
        if len(memories) >= 2:
            per_sample.append((sample_id, memories))
    return per_sample


def _gate_breakdown(source: MemoryRecord, target: MemoryRecord) -> dict[str, bool]:
    """Compute the four SUPERSEDE gates for one (source, target) pair.

    Each gate value is the boolean that must hold for SUPERSEDE to fire on
    this pair (before the 0.7 contradiction-score threshold, which S1b does
    not check — see the module docstring).
    """
    multi_valued_false = not _is_multi_valued(source, target)
    same_slot = _same_fact_slot(source, target)
    distinct_value = not _same_fact_value(source, target)
    source_start, source_end = _interval(source)
    target_start, target_end = _interval(target)
    intervals_overlap = False
    if source_start is not None and target_start is not None:
        intervals_overlap = _intervals_overlap(
            source_start, source_end, target_start, target_end
        )
    return {
        "multi_valued_false": multi_valued_false,
        "same_fact_slot": same_slot,
        "distinct_fact_value": distinct_value,
        "intervals_overlap": intervals_overlap,
    }


def test_four_gate_supersede_is_reachable_on_real_extraction_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    per_sample = _load_real_events()

    total_pairs = 0
    all_four_pairs = 0
    first_three_pairs = 0
    gate_pass_counts = {
        "multi_valued_false": 0,
        "same_fact_slot": 0,
        "distinct_fact_value": 0,
        "intervals_overlap": 0,
    }
    gate_blocked_after_first_three = {"multi_valued_false": 0}
    sample_breakdown: list[dict[str, object]] = []

    for sample_id, memories in per_sample:
        sample_pairs = 0
        sample_all_four = 0
        sample_first_three = 0
        sample_blocked_by_mv = 0
        for source, target in combinations(memories, 2):
            gates = _gate_breakdown(source, target)
            total_pairs += 1
            sample_pairs += 1
            for gate_name, passed in gates.items():
                if passed:
                    gate_pass_counts[gate_name] += 1
            first_three = (
                gates["same_fact_slot"]
                and gates["distinct_fact_value"]
                and gates["intervals_overlap"]
            )
            if first_three:
                first_three_pairs += 1
                sample_first_three += 1
                if not gates["multi_valued_false"]:
                    gate_blocked_after_first_three["multi_valued_false"] += 1
                    sample_blocked_by_mv += 1
            if all(gates.values()):
                all_four_pairs += 1
                sample_all_four += 1
        sample_breakdown.append(
            {
                "sample_id": sample_id,
                "events": len(memories),
                "pairs": sample_pairs,
                "first_three_gates": sample_first_three,
                "all_four_gates": sample_all_four,
                "blocked_by_multi_valued": sample_blocked_by_mv,
            }
        )

    print("\n=== S1b four-gate reachability breakdown ===")
    print(f"per_sample groups: {len(per_sample)}")
    print(f"total enumerated pairs: {total_pairs}")
    print(
        "gate pass counts "
        "(multi_valued_false / same_fact_slot / distinct_fact_value / intervals_overlap): "
        f"{gate_pass_counts['multi_valued_false']} / "
        f"{gate_pass_counts['same_fact_slot']} / "
        f"{gate_pass_counts['distinct_fact_value']} / "
        f"{gate_pass_counts['intervals_overlap']}"
    )
    print(
        "pairs passing the first three gates (slot + distinct value + interval overlap): "
        f"{first_three_pairs}"
    )
    print(
        "pairs passing the first three gates but blocked by multi_valued=True (R3): "
        f"{gate_blocked_after_first_three['multi_valued_false']}"
    )
    print(
        "pairs passing ALL four gates (SUPERSEDE-reachable on real data): "
        f"{all_four_pairs}"
    )
    print("--- per sample ---")
    for row in sample_breakdown:
        print(
            f"  {row['sample_id']}: events={row['events']} pairs={row['pairs']} "
            f"first_three={row['first_three_gates']} all_four={row['all_four_gates']} "
            f"blocked_by_mv={row['blocked_by_multi_valued']}"
        )
    out = capsys.readouterr().out
    assert "=== S1b four-gate reachability breakdown ===" in out

    if all_four_pairs > 0:
        return

    # XFAIL path: no pair satisfied all four gates on 5 questions. Per the
    # S1b scope note this is an expected negative result — S1b does NOT fix
    # R3 (multi_valued over-flagging) and does NOT tune thresholds. S2 will
    # measure the block rate on 50 questions and decide whether to pivot.
    pytest.xfail(
        "S1b reachability: 0 pairs satisfied all four SUPERSEDE gates on 5 "
        f"questions (out of {total_pairs} enumerated pairs; "
        f"{first_three_pairs} passed the first three gates; "
        f"{gate_blocked_after_first_three['multi_valued_false']} were blocked "
        "by multi_valued=True). This is an expected R3-block / "
        "interval-block result; S2 measures the rate on 50 questions."
    )
