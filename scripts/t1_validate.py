#!/usr/bin/env python3
"""T1 selective supersede validation script.

Runs benchmark on stratified samples and compares with S8 baseline.
Supports incremental scaling: n=5, 10, 20, 50, 100.

Usage:
    uv run python scripts/t1_validate.py --n 5
    uv run python scripts/t1_validate.py --n 10 --compare-s8
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

from benchmarks.longmemeval.run import load_config


def select_stratified_samples(
    dataset_path: str,
    n: int,
    seed: int = 42,
) -> list[dict]:
    """Stratified random sampling with minimum per-category protection."""
    import random

    rng = random.Random(seed)
    dataset = json.loads(Path(dataset_path).read_text())

    by_category: dict[str, list[dict]] = defaultdict(list)
    for sample in dataset:
        cat = sample.get("question_type", "unknown")
        by_category[cat].append(sample)

    selected: list[dict] = []

    # Priority categories for T1 validation
    priority_cats = ["temporal-reasoning", "knowledge-update"]

    # Allocate: at least 1 from each priority category
    for cat in priority_cats:
        if cat in by_category and by_category[cat]:
            selected.append(rng.choice(by_category[cat]))

    # Fill remaining from all categories proportionally
    remaining = n - len(selected)
    if remaining > 0:
        pool = [s for s in dataset if s["question_id"] not in {s["question_id"] for s in selected}]
        if pool:
            extra = rng.sample(pool, min(remaining, len(pool)))
            selected.extend(extra)

    seen = set()
    unique = []
    for s in selected:
        if s["question_id"] not in seen:
            seen.add(s["question_id"])
            unique.append(s)

    return unique[:n]


def create_config(
    samples: list[dict],
    run_id: str,
    methods: list[str],
) -> str:
    """Create TOML config for selected samples."""
    sample_ids = [s["question_id"] for s in samples]
    methods_str = ", ".join(f'"{m}"' for m in methods)
    sample_ids_str = ", ".join(f'"{sid}"' for sid in sample_ids)

    return f'''schema_version = "longmemeval.config.v1"
run_id_prefix = "{run_id}"
dataset_path = "data/raw/longmemeval/longmemeval_s_cleaned.json"
methods = [{methods_str}]
provider = "openai_compatible"
max_input_tokens = 4096
max_extraction_tokens = 262144
max_candidates_per_source = 128
max_items_per_source = 8
sample_limit = {len(samples)}
sample_ids = [{sample_ids_str}]

[reader]
provider = "openai_compatible"
model_id = "mimo-v2.5"
base_url = "https://opencode.ai/zen/go/v1"
api_key_env = "OPENAI_API_KEY"
timeout_s = 120
thinking = "disabled"

[extractor]
provider = "openai_compatible"
model_id = "mimo-v2.5"
base_url = "https://opencode.ai/zen/go/v1"
api_key_env = "OPENAI_API_KEY"
timeout_s = 180
thinking = "disabled"
max_tokens = 65536

[embedding]
provider = "openai_compatible"
model_id = "qwen3-embedding-0.6b"
base_url = "http://127.0.0.1:11436/v1"
api_key_env = "EMBEDDING_API_KEY"
timeout_s = 60
'''


def run_benchmark(
    config_path: str,
    output_root: str,
) -> dict[str, Any]:
    """Run benchmark and return summary."""
    from benchmarks.longmemeval.run import _resolve_run_dir, run_experiment

    class Args:
        pass

    args_obj = Args()
    args_obj.config = Path(config_path)
    args_obj.output_root = Path(output_root)
    args_obj.run_dir = None
    args_obj.resume_dir = None

    config = load_config(Path(config_path))
    run_dir = _resolve_run_dir(args_obj)

    summary = run_experiment(config, run_dir)
    return summary.model_dump(mode="json")


def load_s8_baseline() -> dict[str, Any]:
    """Load S8 100q baseline results."""
    summary_path = Path("runs/publication/s8-stratified100/summary.json")
    return json.loads(summary_path.read_text())


def compare_results(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> None:
    """Compare current results with baseline."""
    print("\n" + "=" * 70)
    print("Results")
    print("=" * 70)

    methods = current.get("methods", {})
    print("\nPer-Method EM:")
    for method, data in sorted(methods.items()):
        em = data.get("exact_match", 0)
        n = data.get("sample_count", 0)
        print(f"  {method:<20} EM={em:.3f}  (n={n})")

    if baseline:
        print("\nComparison with S8 baseline:")
        for method in ["etec", "vector_rag"]:
            if method in methods and method in baseline.get("methods", {}):
                curr_em = methods[method].get("exact_match", 0)
                base_em = baseline["methods"][method].get("exact_match", 0)
                delta = curr_em - base_em
                print(
                    f"  {method:<20} current={curr_em:.3f} "
                    f"baseline={base_em:.3f} delta={delta:+.3f}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="T1 selective supersede validation")
    parser.add_argument("--n", type=int, default=5, help="Sample size (default: 5)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["etec", "vector_rag", "event_no_etec"],
        help="Methods to benchmark",
    )
    parser.add_argument("--compare-s8", action="store_true", help="Compare with S8 baseline")
    parser.add_argument("--run-id", default=None, help="Run ID prefix")
    args = parser.parse_args()

    run_id = args.run_id or f"t1-validate-{args.n}"

    print(f"Selecting {args.n} stratified samples...")
    samples = select_stratified_samples(
        "data/raw/longmemeval/longmemeval_s_cleaned.json",
        args.n,
        args.seed,
    )

    cat_dist = defaultdict(int)
    for s in samples:
        cat_dist[s.get("question_type", "unknown")] += 1

    print(f"Selected {len(samples)} samples:")
    for cat, count in sorted(cat_dist.items()):
        print(f"  {cat}: {count}")

    config_path = f"configs/longmemeval/{run_id}.toml"
    config_content = create_config(samples, run_id, args.methods)
    Path(config_path).write_text(config_content)
    print(f"\nConfig written to: {config_path}")

    print(f"\nRunning benchmark (n={args.n})...")
    start_time = time.time()
    summary = run_benchmark(config_path, "runs/t1-validation")
    elapsed = time.time() - start_time
    print(f"Benchmark completed in {elapsed:.1f}s")

    baseline = load_s8_baseline() if args.compare_s8 else None
    compare_results(summary, baseline)


if __name__ == "__main__":
    main()
