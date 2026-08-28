from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.analysis.bootstrap import paired_bootstrap_ci


def compute_cohens_d(group1: Sequence[float], group2: Sequence[float]) -> float:
    if len(group1) < 2 or len(group2) < 2:
        raise ValueError("each group must have at least two observations")
    n1, n2 = len(group1), len(group2)
    mean1 = sum(group1) / n1
    mean2 = sum(group2) / n2
    var1 = sum((x - mean1) ** 2 for x in group1) / (n1 - 1)
    var2 = sum((x - mean2) ** 2 for x in group2) / (n2 - 1)
    pooled_std = math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
    if pooled_std == 0.0:
        return 0.0
    return (mean1 - mean2) / pooled_std


def sample_size_recommendation(effect_size: float, ci_width: float) -> dict[str, Any]:
    if ci_width <= 0:
        raise ValueError("ci_width must be positive")
    abs_d = abs(effect_size)
    if abs_d == 0.0:
        return {
            "effect_size": effect_size,
            "ci_width": ci_width,
            "recommended_n": None,
            "interpretation": "zero_effect_size",
        }
    z = 1.96
    n_per_group = math.ceil((8 + abs_d**2) * z**2 / (4 * ci_width**2))
    n_per_group = max(n_per_group, 2)
    if abs_d < 0.2:
        label = "negligible"
    elif abs_d < 0.5:
        label = "small"
    elif abs_d < 0.8:
        label = "medium"
    else:
        label = "large"
    return {
        "effect_size": effect_size,
        "ci_width": ci_width,
        "recommended_n_per_group": n_per_group,
        "interpretation": label,
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _scan_results(results_dir: Path) -> dict[str, list[dict[str, Any]]]:
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for jsonl_path in sorted(results_dir.rglob("*.jsonl")):
        rows = _load_jsonl(jsonl_path)
        for row in rows:
            method = row.get("method")
            if method is not None:
                by_method[method].append(row)
    return dict(by_method)


def _bootstrap_ci_for_values(
    values: Sequence[float], *, n_boot: int = 10_000, seed: int = 0
) -> tuple[float, float, float]:
    deltas = [v - 0.0 for v in values]
    result = paired_bootstrap_ci(deltas, n_boot=n_boot, seed=seed)
    return result.estimate, result.ci_low, result.ci_high


def _pairwise_d_with_ci(
    group1: Sequence[float],
    group2: Sequence[float],
    *,
    n_boot: int = 10_000,
    seed: int = 0,
) -> dict[str, Any]:
    d = compute_cohens_d(group1, group2)
    paired = [a - b for a, b in zip(group1, group2, strict=True)]
    if paired:
        ci = paired_bootstrap_ci(paired, n_boot=n_boot, seed=seed)
        return {
            "cohens_d": d,
            "ci_low": ci.ci_low,
            "ci_high": ci.ci_high,
            "n": len(paired),
        }
    return {"cohens_d": d, "ci_low": float("nan"), "ci_high": float("nan"), "n": 0}


def run_small_sample_analysis(
    results_dir: Path,
    *,
    metric: str = "exact_match",
    n_boot: int = 10_000,
    seed: int = 0,
    etec_name: str = "etec",
) -> dict[str, Any]:
    by_method = _scan_results(results_dir)
    if not by_method:
        return {"error": "no_results_found", "results_dir": str(results_dir)}

    per_method: dict[str, dict[str, Any]] = {}
    for method, rows in sorted(by_method.items()):
        values = [float(row.get(metric, 0.0)) for row in rows]
        if not values:
            continue
        mean_val = sum(values) / len(values)
        ci_est, ci_low, ci_high = _bootstrap_ci_for_values(values, n_boot=n_boot, seed=seed)
        per_method[method] = {
            "method": method,
            "n": len(values),
            "mean": mean_val,
            "ci_estimate": ci_est,
            "ci_low": ci_low,
            "ci_high": ci_high,
        }

    pairwise: list[dict[str, Any]] = []
    etec_values: list[float] | None = None
    if etec_name in per_method:
        etec_rows = by_method[etec_name]
        etec_values = [float(row.get(metric, 0.0)) for row in etec_rows]

    for method, rows in sorted(by_method.items()):
        if method == etec_name:
            continue
        other_values = [float(row.get(metric, 0.0)) for row in rows]
        if etec_values is not None and len(etec_values) == len(other_values):
            pair_result = _pairwise_d_with_ci(
                etec_values, other_values, n_boot=n_boot, seed=seed
            )
            pair_result["comparison"] = f"{etec_name}_vs_{method}"
            pair_result["left"] = etec_name
            pair_result["right"] = method
            rec = sample_size_recommendation(pair_result["cohens_d"], ci_width=0.1)
            pair_result["sample_size_rec"] = rec
            pairwise.append(pair_result)

    return {
        "metric": metric,
        "results_dir": str(results_dir),
        "per_method": per_method,
        "pairwise": pairwise,
    }


def _print_summary(analysis: dict[str, Any]) -> None:
    if "error" in analysis:
        print(f"error: {analysis['error']} ({analysis['results_dir']})", file=sys.stderr)
        return

    metric = analysis["metric"]
    print(f"Small Sample Performance Analysis (metric={metric})")
    print("=" * 60)

    print("\nPer-method summary:")
    print(f"  {'method':<25} {'n':>5} {'mean':>8} {'ci_low':>8} {'ci_high':>8}")
    print(f"  {'-'*25} {'-'*5} {'-'*8} {'-'*8} {'-'*8}")
    for info in analysis["per_method"].values():
        print(
            f"  {info['method']:<25} {info['n']:>5} {info['mean']:>8.4f} "
            f"{info['ci_low']:>8.4f} {info['ci_high']:>8.4f}"
        )

    if analysis["pairwise"]:
        print("\nPairwise Cohen's d (ETEC vs baselines):")
        print(
            f"  {'comparison':<30} {'d':>8} {'ci_low':>8} {'ci_high':>8} {'n':>5}"
        )
        print(f"  {'-'*30} {'-'*8} {'-'*8} {'-'*8} {'-'*5}")
        for pair in analysis["pairwise"]:
            print(
                f"  {pair['comparison']:<30} {pair['cohens_d']:>8.3f} "
                f"{pair['ci_low']:>8.3f} {pair['ci_high']:>8.3f} {pair['n']:>5}"
            )
        print("\nSample size recommendations (target CI width=0.1):")
        for pair in analysis["pairwise"]:
            rec = pair["sample_size_rec"]
            n_rec = rec.get("recommended_n_per_group", "N/A")
            print(
                f"  {pair['comparison']}: d={rec['effect_size']:.3f} "
                f"({rec['interpretation']}), recommended n_per_group={n_rec}"
            )
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Small sample performance analysis with bootstrap CIs and effect sizes."
    )
    parser.add_argument("results_dir", type=Path, help="Directory containing JSONL result files")
    parser.add_argument(
        "--metric", default="exact_match", help="Metric column to analyze (default: exact_match)"
    )
    parser.add_argument("--n-boot", type=int, default=10_000, help="Bootstrap iterations")
    parser.add_argument("--seed", type=int, default=0, help="RNG seed for bootstrap")
    parser.add_argument(
        "--etec-name", default="etec", help="Method name for ETEC (default: etec)"
    )
    args = parser.parse_args(argv)

    if not args.results_dir.is_dir():
        print(f"error: results_dir does not exist: {args.results_dir}", file=sys.stderr)
        return 1

    analysis = run_small_sample_analysis(
        args.results_dir,
        metric=args.metric,
        n_boot=args.n_boot,
        seed=args.seed,
        etec_name=args.etec_name,
    )
    _print_summary(analysis)
    print(json.dumps(analysis, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
