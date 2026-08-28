#!/usr/bin/env python3
"""Analyze T1 validation results.

Compares current results with S8 baseline and shows per-category breakdown.

Usage:
    uv run python scripts/t1_analyze.py --run-dir runs/t1-validation/t1-validate-5-xxx
    uv run python scripts/t1_analyze.py --compare-s8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")


def load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    """Load summary.json from a run directory."""
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text())
    return None


def load_s8_baseline() -> dict[str, Any]:
    """Load S8 100q baseline results."""
    summary_path = Path("runs/publication/s8-stratified100/summary.json")
    return json.loads(summary_path.read_text())


def analyze_results(summary: dict[str, Any], baseline: dict[str, Any] | None) -> None:
    """Analyze and compare results."""
    print("=" * 70)
    print("T1 Validation Results")
    print("=" * 70)

    methods = summary.get("methods", {})

    print("\nPer-Method EM:")
    for method, data in sorted(methods.items()):
        em = data.get("exact_match", 0)
        n = data.get("sample_count", 0)
        print(f"  {method:<20} EM={em:.3f}  (n={n})")

    if "etec" in methods and "vector_rag" in methods:
        etec_em = methods["etec"].get("exact_match", 0)
        vecrag_em = methods["vector_rag"].get("exact_match", 0)
        delta = etec_em - vecrag_em
        print(f"\nETEC vs VecRAG: delta={delta:+.3f}")

    if baseline:
        print("\n" + "=" * 70)
        print("Comparison with S8 Baseline")
        print("=" * 70)

        base_methods = baseline.get("methods", {})
        for method in ["etec", "vector_rag", "event_no_etec"]:
            if method in methods and method in base_methods:
                curr_em = methods[method].get("exact_match", 0)
                base_em = base_methods[method].get("exact_match", 0)
                delta = curr_em - base_em
                print(
                    f"{method:<20} current={curr_em:.3f} "
                    f"baseline={base_em:.3f} delta={delta:+.3f}"
                )

        if "etec" in methods and "etec" in base_methods:
            etec_curr = methods["etec"].get("exact_match", 0)
            vr_curr = methods.get("vector_rag", {}).get("exact_match", 0)
            curr_delta = etec_curr - vr_curr
            etec_base = base_methods["etec"].get("exact_match", 0)
            vr_base = base_methods.get("vector_rag", {}).get("exact_match", 0)
            base_delta = etec_base - vr_base
            improvement = curr_delta - base_delta
            print(f"\nETEC improvement over baseline: {improvement:+.3f}")


def find_latest_run(output_root: str) -> Path | None:
    """Find the latest run directory."""
    output_path = Path(output_root)
    if not output_path.exists():
        return None

    run_dirs = sorted(
        [d for d in output_path.iterdir() if d.is_dir() and d.name.startswith("t1-")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return run_dirs[0] if run_dirs else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze T1 validation results")
    parser.add_argument("--run-dir", type=Path, help="Run directory to analyze")
    parser.add_argument("--compare-s8", action="store_true", help="Compare with S8 baseline")
    parser.add_argument("--output-root", default="runs/t1-validation", help="Output root directory")
    args = parser.parse_args()

    run_dir = args.run_dir
    if run_dir is None:
        run_dir = find_latest_run(args.output_root)

    if run_dir is None:
        print("No run directory found")
        sys.exit(1)

    print(f"Analyzing: {run_dir}")

    summary = load_run_summary(run_dir)
    if summary is None:
        print("No summary.json found")
        sys.exit(1)

    baseline = load_s8_baseline() if args.compare_s8 else None
    analyze_results(summary, baseline)


if __name__ == "__main__":
    main()
