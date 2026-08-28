#!/usr/bin/env python3
"""Fast T1 validation using S8 extraction snapshots.

Skips extraction (uses cached S8 snapshots), only runs:
- materialization (store building)
- retrieval + reader (per method)

This is ~10x faster than full benchmark.

Usage:
    uv run python scripts/t1_fast_validate.py --n 5
    uv run python scripts/t1_fast_validate.py --n 10
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

from benchmarks.common.artifacts import ExtractionSnapshot
from benchmarks.common.memory_inputs import (
    materialize_event_store,
)
from benchmarks.common.normalization import iter_longmemeval_records
from benchmarks.common.providers import build_model_bundle, cache_for_run
from benchmarks.longmemeval.run import Method, _run_memory_method, load_config


def load_s8_samples(n: int) -> list[dict]:
    """Load samples from S8 run."""
    s8_dir = Path("runs/publication/s8-stratified100")
    samples = []
    for f in (s8_dir / "samples").glob("*.json"):
        if "extraction_snapshot" not in f.name:
            samples.append(json.loads(f.read_text()))
    return samples[:n]


def run_single_sample(
    sample: dict,
    config: Any,
    bundle: Any,
    records: dict[str, Any],
    s8_dir: Path,
) -> dict[str, Any]:
    """Run retrieval+reader for a single sample using S8 snapshot."""
    sid = sample["sample_id"]
    category = sample["category"]

    # Load extraction snapshot from S8
    snapshot_path = s8_dir / "samples" / f"{sid}.extraction_snapshot.json"
    if not snapshot_path.exists():
        return {"error": "no_snapshot"}

    snapshot_data = json.loads(snapshot_path.read_text())
    snapshot = ExtractionSnapshot.model_validate(snapshot_data)

    # Materialize stores
    etec_store, _ = materialize_event_store(
        snapshot, apply_etec=True, embedding_model=bundle.embedding, user_id=sid
    )

    # Get record and question
    record = records.get(sid)
    if not record:
        return {"error": "no_record"}

    question = record.questions[0]

    # Run ETEC method
    result = _run_memory_method(
        Method.ETEC,
        question,
        etec_store,
        config,
        bundle,
        user_id=sid,
        input_kind="event",
        snapshot_id=snapshot.snapshot_id,
        write_latency_ms=0,
    )

    return {
        "sample_id": sid,
        "category": category,
        "answer": question.answer,
        "etec_prediction": result.prediction,
        "etec_em": result.exact_match,
        "etec_token_f1": result.token_f1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fast T1 validation")
    parser.add_argument("--n", type=int, default=5, help="Sample size")
    parser.add_argument("--compare-s8", action="store_true", help="Compare with S8 baseline")
    args = parser.parse_args()

    print(f"Loading {args.n} S8 samples...")
    samples = load_s8_samples(args.n)
    print(f"Loaded {len(samples)} samples")

    # Load config
    config = load_config(Path("configs/longmemeval/smoke5-mimo.toml"))
    config.dataset_path = "data/raw/longmemeval/longmemeval_s_cleaned.json"

    # Build model bundle
    output_dir = Path("runs/t1-fast-validation")
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = build_model_bundle(config.providers, cache_for_run(output_dir))

    # Load records
    records = {r.sample_id: r for r in iter_longmemeval_records(Path(config.dataset_path))}

    # Run samples
    results = []
    start_time = time.time()

    for i, sample in enumerate(samples):
        sample_start = time.time()
        sid = sample["sample_id"]
        cat = sample["category"]
        print(f"[{i+1}/{len(samples)}] {sid} ({cat})...", end=" ", flush=True)

        s8_dir = Path("runs/publication/s8-stratified100")
        result = run_single_sample(sample, config, bundle, records, s8_dir)
        results.append(result)

        elapsed = time.time() - sample_start
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            print(f"EM={result['etec_em']:.0f} ({elapsed:.1f}s)")

    total_time = time.time() - start_time
    print(f"\nTotal time: {total_time:.1f}s ({total_time/len(results):.1f}s/sample)")

    # Analyze results
    print("\n" + "=" * 70)
    print("Results")
    print("=" * 70)

    by_category = defaultdict(list)
    for r in results:
        if "error" not in r:
            by_category[r["category"]].append(r["etec_em"])

    print("\nPer-Category EM:")
    for cat, ems in sorted(by_category.items()):
        print(f"  {cat:<30} EM={sum(ems)/len(ems):.3f}  (n={len(ems)})")

    overall = [r["etec_em"] for r in results if "error" not in r]
    if overall:
        print(f"\nOverall EM: {sum(overall)/len(overall):.3f}  (n={len(overall)})")

    if args.compare_s8:
        print("\n" + "=" * 70)
        print("Comparison with S8 Baseline")
        print("=" * 70)

        s8_summary = json.loads(Path("runs/publication/s8-stratified100/summary.json").read_text())
        s8_etec = s8_summary["methods"]["etec"]["exact_match"]
        s8_vecrag = s8_summary["methods"]["vector_rag"]["exact_match"]

        curr_overall = sum(overall) / len(overall) if overall else 0
        print(f"S8 ETEC:    {s8_etec:.3f}")
        print(f"S8 VecRAG:  {s8_vecrag:.3f}")
        print(f"Current:    {curr_overall:.3f}")
        print(f"Delta:      {curr_overall - s8_etec:+.3f}")

    # Save results
    output_file = output_dir / "results.json"
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    main()
