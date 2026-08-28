#!/usr/bin/env python3
"""Stratified small-sample benchmark for quick iteration.

Design principles (from literature):
1. Stratified random sampling by category
2. Minimum 2 samples per category
3. Proportional allocation with minimum protection
4. Focus on direction validation, not CI estimation

Usage:
    uv run python scripts/stratified_quick_benchmark.py --n 20
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def load_dataset(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def stratified_sample(
    dataset: list[dict],
    n: int,
    seed: int = 42,
    min_per_category: int = 2,
) -> list[dict]:
    """Stratified random sampling with minimum per-category protection."""
    rng = random.Random(seed)

    # Group by category
    by_category: dict[str, list[dict]] = defaultdict(list)
    for sample in dataset:
        cat = sample.get("question_type", "unknown")
        by_category[cat].append(sample)

    total = len(dataset)
    selected: list[dict] = []

    # Phase 1: minimum per category
    for _cat, samples in sorted(by_category.items()):
        n_min = min(min_per_category, len(samples))
        selected.extend(rng.sample(samples, n_min))

    # Phase 2: proportional allocation for remaining budget
    remaining = n - len(selected)
    if remaining > 0:
        selected_ids = {s["question_id"] for s in selected}
        pool = [s for s in dataset if s["question_id"] not in selected_ids]

        # Weight by category size
        cat_weights = {}
        for cat, samples in by_category.items():
            n_selected = sum(1 for s in selected if s.get("question_type") == cat)
            n_target = max(1, round(len(samples) / total * n))
            cat_weights[cat] = max(0, n_target - n_selected)

        # Sample remaining proportionally
        weighted_pool = []
        for s in pool:
            cat = s.get("question_type", "unknown")
            w = cat_weights.get(cat, 0)
            weighted_pool.extend([s] * w)

        if weighted_pool and remaining > 0:
            extra = rng.sample(weighted_pool, min(remaining, len(weighted_pool)))
            selected.extend(extra)

    # Deduplicate
    seen = set()
    unique = []
    for s in selected:
        if s["question_id"] not in seen:
            seen.add(s["question_id"])
            unique.append(s)

    return unique[:n]


def create_config_toml(
    samples: list[dict],
    run_id: str,
    methods: list[str],
) -> str:
    """Create benchmark config in TOML format for selected samples."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Stratified small-sample benchmark")
    parser.add_argument("--n", type=int, default=20, help="Total samples (default: 20)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["etec", "vector_rag", "event_no_etec"],
        help="Methods to benchmark",
    )
    parser.add_argument("--run-id", default="stratified-quick", help="Run ID prefix")
    args = parser.parse_args()

    dataset = load_dataset(Path("data/raw/longmemeval/longmemeval_s_cleaned.json"))
    samples = stratified_sample(dataset, args.n, args.seed)

    print(f"Selected {len(samples)} samples:")
    cat_dist = defaultdict(int)
    for s in samples:
        cat = s.get("question_type", "unknown")
        cat_dist[cat] += 1
    for cat, count in sorted(cat_dist.items()):
        print(f"  {cat}: {count}")

    config_toml = create_config_toml(samples, args.run_id, args.methods)
    config_path = Path(f"configs/longmemeval/{args.run_id}.toml")
    config_path.write_text(config_toml, encoding="utf-8")
    print(f"\nConfig written to: {config_path}")
    print("\nRun with:")
    print(f"  uv run python -m benchmarks.longmemeval.run --config {config_path}")


if __name__ == "__main__":
    main()
