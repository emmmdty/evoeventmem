"""S8 Step 3a: stratified sample for the n=100 final validation.

The 50-question v2 slice was a degenerate single-class sample (all
``single-session-user``, 14.0% of the 500-question distribution). The S8
final validation uses an n=100 stratified sample drawn from the full 500
LongMemEval-S distribution so every ``question_type`` is represented
proportionally. This is the project's final benchmark sample per
``docs/S8-stratified-validation-prompt.md`` §背景 — the 500-question run
is downgraded to optional future-work (METHODOLOGY_CHANGE.md already
concedes 500q expected non-significance).

Algorithm: largest remainder method (Hamilton's method) on the 500-
question distribution. This guarantees the integer allocation sums to
exactly ``N`` and stays within ±1 question of the proportional ideal for
every category. The random draw within each category uses a fixed seed so
the manifest is reproducible — the manifest is the pre-registered sample
design (committed to git; only IDs and allocation, no question content).

CLI::

    uv run python -m benchmarks.longmemeval.stratified_sample \\
        --n 100 --seed 42 \\
        --output configs/longmemeval/stratified100.toml.inc
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.common.normalization import iter_longmemeval_records

DEFAULT_DATASET = Path("data/raw/longmemeval/longmemeval_s_cleaned.json")

# LongMemEval-S 500-question distribution (verified by Step 0 baseline).
# Used as the default stratification proportions. The exact count is
# derived from the dataset at runtime; this dict documents the expected
# source distribution and is the pre-registered stratification frame.
EXPECTED_500_DISTRIBUTION: dict[str, int] = {
    "multi-session": 133,
    "temporal-reasoning": 133,
    "knowledge-update": 78,
    "single-session-user": 70,
    "single-session-assistant": 56,
    "single-session-preference": 30,
}


def _dataset_hash(dataset_path: Path) -> str:
    """Return the sha256 of the dataset file (stratification source)."""
    return f"sha256:{hashlib.sha256(dataset_path.read_bytes()).hexdigest()}"


def _dataset_distribution(dataset_path: Path) -> dict[str, list[str]]:
    """Return ``{question_type: [question_id, ...]}`` from the dataset."""
    by_type: dict[str, list[str]] = defaultdict(list)
    for record in iter_longmemeval_records(dataset_path):
        for question in record.questions:
            qtype = question.category or "unknown"
            by_type[qtype].append(question.question_id)
    for qtype in by_type:
        by_type[qtype].sort()  # deterministic order before sampling
    return dict(by_type)


def largest_remainder_allocation(
    n: int,
    strata_sizes: dict[str, int],
) -> dict[str, int]:
    """Allocate ``n`` items across strata using the largest remainder method.

    Pure function (no I/O) so it can be unit-tested with fakes. Guarantees:
    - ``sum(allocations) == n`` (exact)
    - Each stratum gets ``floor(proportional_ideal)`` or
      ``floor(proportional_ideal) + 1`` (within ±1 of the ideal).
    - Strata are filled by largest remainder of the fractional part;
      ties are broken alphabetically by stratum name for determinism.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    total = sum(strata_sizes.values())
    if total == 0:
        return {qtype: 0 for qtype in strata_sizes}
    # Proportional ideal (real) per stratum.
    ideals = {qtype: n * size / total for qtype, size in strata_sizes.items()}
    # Floor allocation.
    floor_alloc = {qtype: int(ideal) for qtype, ideal in ideals.items()}
    awarded = sum(floor_alloc.values())
    remainder = n - awarded
    if remainder < 0:
        # Rounding overshoot (rare); strip from the largest strata.
        order = sorted(strata_sizes, key=lambda q: (-strata_sizes[q], q))
        i = 0
        while remainder < 0:
            qtype = order[i % len(order)]
            if floor_alloc[qtype] > 0:
                floor_alloc[qtype] -= 1
                remainder += 1
            i += 1
        return floor_alloc
    # Award remaining seats by largest fractional remainder; tie-break
    # alphabetically so the result is deterministic across runs.
    remainders = [
        (qtype, ideals[qtype] - floor_alloc[qtype])
        for qtype in strata_sizes
    ]
    remainders.sort(key=lambda item: (-item[1], item[0]))
    for i in range(remainder):
        qtype = remainders[i % len(remainders)][0]
        floor_alloc[qtype] += 1
    return floor_alloc


def stratified_sample(
    n: int,
    dataset_path: Path,
    *,
    seed: int = 42,
) -> list[str]:
    """Draw ``n`` question IDs stratified by ``question_type``.

    Uses the largest remainder method for allocation, then a seeded
    deterministic shuffle within each stratum. Returns a sorted list of
    question IDs (sorted for reproducibility — the draw order within a
    stratum does not affect the benchmark).
    """
    import random

    by_type = _dataset_distribution(dataset_path)
    strata_sizes = {qtype: len(ids) for qtype, ids in by_type.items()}
    allocation = largest_remainder_allocation(n, strata_sizes)
    rng = random.Random(seed)
    selected: list[str] = []
    for qtype, count in allocation.items():
        pool = list(by_type.get(qtype, []))
        # Seeded shuffle + take first ``count``. Sorting the pool first
        # keeps the draw deterministic across runs (Random(seed) is
        # already deterministic, but sorting guards against dict-order
        # drift if the dataset loader changes).
        pool.sort()
        rng.shuffle(pool)
        selected.extend(pool[:count])
    selected.sort()
    return selected


def build_manifest(
    n: int,
    dataset_path: Path,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Build the stratified-sample manifest (pre-registered sample design).

    The manifest contains only IDs and allocation metadata — no question
    content. It is committed to git as evidence of the pre-registered
    sample design.
    """
    by_type = _dataset_distribution(dataset_path)
    strata_sizes = {qtype: len(ids) for qtype, ids in by_type.items()}
    allocation = largest_remainder_allocation(n, strata_sizes)
    selected_ids = stratified_sample(n, dataset_path, seed=seed)
    return {
        "schema_version": "stratified-sample.manifest.v1",
        "n": n,
        "seed": seed,
        "dataset": str(dataset_path),
        "dataset_hash": _dataset_hash(dataset_path),
        "source_distribution": strata_sizes,
        "allocation": allocation,
        "allocation_sums_to_n": sum(allocation.values()) == n,
        "sample_ids": selected_ids,
    }


def _format_manifest_text(manifest: dict[str, Any]) -> str:
    """Format the manifest as a TOML-include / JSON-hybrid text file.

    The manifest is written as JSON (one canonical form) so the
    router-diagnosis ``--sample-ids-file`` loader and the run.py
    ``--sample-ids`` consumer can both parse it.
    """
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "S8 Step 3a: draw a stratified n-sample from LongMemEval-S "
            "(largest remainder method, seeded deterministic draw)."
        )
    )
    parser.add_argument(
        "--n",
        type=int,
        default=100,
        help="Sample size (default: 100, the S8 final validation size).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for the within-stratum shuffle (default: 42).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"LongMemEval cleaned JSON (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Write the manifest to this path. Default: stdout. The "
            "manifest is JSON (sample_ids list + allocation metadata); "
            "commit it to git as the pre-registered sample design."
        ),
    )
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        print(f"error: dataset {args.dataset} not found", file=sys.stderr)
        return 1

    manifest = build_manifest(args.n, args.dataset, seed=args.seed)
    if manifest["allocation_sums_to_n"] is not True:
        print(
            f"error: allocation {manifest['allocation']} does not sum to "
            f"{args.n} (sum={sum(manifest['allocation'].values())})",
            file=sys.stderr,
        )
        return 1
    text = _format_manifest_text(manifest)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(
            f"stratified sample manifest written to {args.output} "
            f"(n={args.n}, seed={args.seed})",
            file=sys.stderr,
        )
        print(
            f"allocation: {manifest['allocation']}",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
