"""Unit tests for the S8 Step 3a stratified-sample allocation logic.

Tests the pure ``largest_remainder_allocation`` function with fakes (no
I/O) and the manifest builder against a tiny synthetic dataset fixture
to verify the integer-sum invariant and the per-stratum ±1 bound.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.longmemeval.stratified_sample import (
    build_manifest,
    largest_remainder_allocation,
    stratified_sample,
)


def test_largest_remainder_sums_exactly_to_n() -> None:
    strata = {"a": 133, "b": 133, "c": 78, "d": 70, "e": 56, "f": 30}
    alloc = largest_remainder_allocation(100, strata)
    assert sum(alloc.values()) == 100
    assert set(alloc) == set(strata)


def test_largest_remainder_matches_expected_500_distribution() -> None:
    # The S8 spec's expected n=100 allocation (largest remainder on the
    # 500 distribution). Documented in docs/S8-stratified-validation-
    # prompt.md §背景.
    strata = {
        "multi-session": 133,
        "temporal-reasoning": 133,
        "knowledge-update": 78,
        "single-session-user": 70,
        "single-session-assistant": 56,
        "single-session-preference": 30,
    }
    alloc = largest_remainder_allocation(100, strata)
    # Proportional ideals: 26.6, 26.6, 15.6, 14.0, 11.2, 6.0.
    # Floor: 26 + 26 + 15 + 14 + 11 + 6 = 98. Remainder 2 → awarded to
    # the two largest fractional remainders (0.6 ties: multi-session and
    # temporal-reasoning, alphabetically multi-session first).
    assert sum(alloc.values()) == 100
    # Each stratum within ±1 of the ideal floor.
    for qtype, size in strata.items():
        ideal = 100 * size / 500
        assert abs(alloc[qtype] - ideal) <= 1.0, (
            f"{qtype}: alloc={alloc[qtype]} vs ideal={ideal:.1f}"
        )


def test_largest_remainder_is_deterministic_across_runs() -> None:
    strata = {"a": 133, "b": 133, "c": 78, "d": 70, "e": 56, "f": 30}
    first = largest_remainder_allocation(100, strata)
    second = largest_remainder_allocation(100, strata)
    assert first == second


def test_largest_remainder_handles_zero_strata() -> None:
    assert largest_remainder_allocation(100, {}) == {}
    assert largest_remainder_allocation(100, {"a": 0, "b": 0}) == {
        "a": 0,
        "b": 0,
    }


def test_largest_remainder_rejects_negative_n() -> None:
    with pytest.raises(ValueError):
        largest_remainder_allocation(-1, {"a": 10})


def test_largest_remainder_n_zero_returns_all_zero() -> None:
    alloc = largest_remainder_allocation(0, {"a": 10, "b": 20})
    assert alloc == {"a": 0, "b": 0}


def _write_synthetic_dataset(path: Path) -> Path:
    """Write a tiny LongMemEval-format dataset for manifest tests."""
    records = []
    # 6 question_types, varying sizes — enough to exercise allocation.
    for qtype, count in [
        ("temporal-reasoning", 8),
        ("knowledge-update", 4),
        ("multi-session", 8),
        ("single-session-user", 4),
        ("single-session-assistant", 3),
        ("single-session-preference", 3),
    ]:
        for i in range(count):
            records.append(
                {
                    "question_id": f"{qtype}-{i:03d}",
                    "question_type": qtype,
                    "question": f"q{i}?",
                    "answer": "a",
                    "question_date": "2024-01-01",
                    "haystack_session_ids": ["session-0"],
                    "haystack_dates": ["2024-01-01"],
                    "haystack_sessions": [
                        [{"role": "user", "content": "hello"}],
                    ],
                    "answer_session_ids": ["session-0"],
                }
            )
    payload = json.dumps(records, indent=2)
    path.write_text(payload, encoding="utf-8")
    return path


def test_build_manifest_allocation_sums_to_n(tmp_path: Path) -> None:
    dataset = _write_synthetic_dataset(tmp_path / "synthetic.json")
    manifest = build_manifest(20, dataset, seed=42)
    assert manifest["allocation_sums_to_n"] is True
    assert sum(manifest["allocation"].values()) == 20
    assert len(manifest["sample_ids"]) == 20


def test_build_manifest_is_reproducible_with_same_seed(tmp_path: Path) -> None:
    dataset = _write_synthetic_dataset(tmp_path / "synthetic.json")
    first = build_manifest(20, dataset, seed=42)
    second = build_manifest(20, dataset, seed=42)
    assert first["sample_ids"] == second["sample_ids"]
    assert first["allocation"] == second["allocation"]


def test_build_manifest_different_seed_changes_draw_but_not_allocation(
    tmp_path: Path,
) -> None:
    dataset = _write_synthetic_dataset(tmp_path / "synthetic.json")
    seed42 = build_manifest(20, dataset, seed=42)
    seed99 = build_manifest(20, dataset, seed=99)
    assert seed42["allocation"] == seed99["allocation"]
    # The within-stratum draw must differ with a different seed (extremely
    # unlikely to coincide on a 20-item draw).
    assert seed42["sample_ids"] != seed99["sample_ids"]
    # But the stratum-level counts (per question_type) must be identical.
    from collections import Counter

    def by_type(manifest: dict) -> Counter:
        c: Counter[str] = Counter()
        for qid in manifest["sample_ids"]:
            c[qid.rsplit("-", 1)[0]] += 1
        return c

    assert by_type(seed42) == by_type(seed99)


def test_stratified_sample_returns_sorted_unique_ids(tmp_path: Path) -> None:
    dataset = _write_synthetic_dataset(tmp_path / "synthetic.json")
    ids = stratified_sample(15, dataset, seed=7)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_build_manifest_dataset_hash_is_stable(tmp_path: Path) -> None:
    dataset = _write_synthetic_dataset(tmp_path / "synthetic.json")
    first = build_manifest(10, dataset, seed=1)
    second = build_manifest(10, dataset, seed=1)
    assert first["dataset_hash"] == second["dataset_hash"]
    assert first["dataset_hash"].startswith("sha256:")
