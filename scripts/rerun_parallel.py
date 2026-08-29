"""Parallel re-evaluation: reuse v2 extraction snapshots, run retrieval+reader in parallel.

Speeds up benchmark by ~10x by:
1. Skipping extraction (reuses v2 snapshots)
2. Running N samples in parallel (ThreadPoolExecutor)

Usage:
    uv run python scripts/rerun_parallel.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from benchmarks.common.memory_inputs import (
    build_raw_turn_corpus,
    materialize_event_store,
    materialize_raw_turn_store,
)
from benchmarks.common.normalization import iter_longmemeval_records
from benchmarks.common.providers import build_model_bundle, cache_for_run
from benchmarks.longmemeval.run import (
    EVENT_INPUT_KIND,
    VECTOR_INPUT_KIND,
    Method,
    _category_for,
    _METHOD_STRATEGY,
    _retrieval_payload,
    _run_context_method,
    _run_memory_method,
    _store_for,
    _write_latency_for,
    load_config,
    _order_record,
)
from benchmarks.common.artifacts import ExtractionSnapshot

V2_RUN = Path("runs/publication/m13-longmemeval-test50-mimo-v2-factslot")
CONFIG_PATH = Path("configs/longmemeval/test50-mimo-v2-routerfix.toml")
DATASET_PATH = Path("data/raw/longmemeval/longmemeval_s_cleaned.json")
MAX_WORKERS = 10
OUTPUT_DIR = Path("runs/rerun-routerfix")


def load_snapshot(sample_id: str) -> EventExtractionSnapshot:
    snap_path = V2_RUN / "samples" / f"{sample_id}.extraction_snapshot.json"
    return ExtractionSnapshot.model_validate(json.loads(snap_path.read_text()))


def process_sample(args):
    record, config, bundle, run_dir, worker_id = args
    sample_id = record.sample_id
    question = record.questions[0]
    t0 = time.time()

    snapshot = load_snapshot(sample_id)
    ordered_record = _order_record(record)
    corpus = build_raw_turn_corpus(ordered_record)

    t1 = time.time()
    raw_store, raw_ingestion = materialize_event_store(
        snapshot, apply_etec=False, user_id=sample_id
    )
    etec_store, etec_ingestion = materialize_event_store(
        snapshot, apply_etec=True, embedding_model=bundle.embedding, user_id=sample_id
    )
    vector_store, vector_ingestion = materialize_raw_turn_store(corpus, user_id=sample_id)

    if Method.VECTOR_RAG in set(config.methods):
        bundle.embedding.embed_texts([chunk.content for chunk in corpus.chunks])

    methods_result = {}
    for method in config.methods:
        if method in ("no_memory", "full_context"):
            rec = _run_context_method(
                method, question, ordered_record.sessions, config, bundle
            )
        else:
            rec = _run_memory_method(
                method,
                question,
                _store_for(method, vector_store, raw_store, etec_store),
                config,
                bundle,
                user_id=sample_id,
                input_kind=VECTOR_INPUT_KIND if method == "vector_rag" else EVENT_INPUT_KIND,
                snapshot_id=None if method == "vector_rag" else snapshot.snapshot_id,
                write_latency_ms=_write_latency_for(method, 0, 0, 0),
            )
        methods_result[method] = {
            "exact_match": rec.exact_match,
            "token_f1": rec.token_f1,
        }

    elapsed = time.time() - t0
    return {
        "sample_id": sample_id,
        "question_id": question.question_id,
        "elapsed_s": round(elapsed, 1),
        "methods": methods_result,
        "worker": worker_id,
    }


def main():
    print("Loading config...", flush=True)
    config = load_config(CONFIG_PATH)

    print("Building model bundle...", flush=True)
    run_dir = OUTPUT_DIR
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "samples").mkdir(exist_ok=True)
    bundle = build_model_bundle(config.providers, cache_for_run(run_dir))

    print("Loading dataset...", flush=True)
    records = list(iter_longmemeval_records(DATASET_PATH))
    records = records[: config.sample_limit or 50]
    print(f"Loaded {len(records)} samples", flush=True)

    completed = set()
    for f in (run_dir / "samples").glob("*.json"):
        if "extraction_snapshot" not in f.name:
            completed.add(f.stem)
    pending = [r for r in records if r.sample_id not in completed]
    print(f"Already done: {len(completed)}, pending: {len(pending)}", flush=True)

    if not pending:
        print("All done!")
        return

    args_list = [
        (record, config, bundle, run_dir, i % MAX_WORKERS)
        for i, record in enumerate(pending)
    ]

    print(f"Running {len(pending)} samples with {MAX_WORKERS} workers...", flush=True)
    t_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_sample, args): args[0].sample_id
            for args in args_list
        }
        for future in as_completed(futures):
            sid = futures[future]
            try:
                result = future.result()
                results.append(result)
                elapsed = time.time() - t_start
                done = len(results)
                avg_time = elapsed / done
                eta = avg_time * (len(pending) - done)
                ems = result["methods"]
                vr = ems.get("vector_rag", {}).get("exact_match", "?")
                fu = ems.get("full", {}).get("exact_match", "?")
                print(
                    f"[{done}/{len(pending)}] {sid} "
                    f"vr={vr} full={fu} "
                    f"({result['elapsed_s']:.0f}s) "
                    f"ETA {eta/60:.0f}min",
                    flush=True,
                )
            except Exception as e:
                print(f"FAILED {sid}: {e}", flush=True)
                import traceback
                traceback.print_exc()

    total_time = time.time() - t_start
    print(f"\nDone in {total_time/60:.1f} min ({total_time/len(pending):.0f}s/sample)", flush=True)

    # Save individual results
    for r in results:
        out = {
            "sample_id": r["sample_id"],
            "question_id": r["question_id"],
            "methods": r["methods"],
        }
        (run_dir / "samples" / f"{r['sample_id']}.json").write_text(
            json.dumps(out, indent=2)
        )

    # Load previously completed too
    all_results = list(results)
    for f in (run_dir / "samples").glob("*.json"):
        if "extraction_snapshot" in f.name:
            continue
        sid = f.stem
        if sid not in [r["sample_id"] for r in all_results]:
            d = json.loads(f.read_text())
            all_results.append({
                "sample_id": sid,
                "methods": {
                    m: {"exact_match": v.get("exact_match"), "token_f1": v.get("token_f1")}
                    for m, v in d.get("methods", {}).items()
                    if isinstance(v, dict)
                },
            })

    print(f"\n=== Final Results ({len(all_results)} samples) ===")
    print(f"{'method':<20} {'EM':>8} {'token_f1':>10}")
    print("-" * 40)
    for m in ["no_memory", "full_context", "vector_rag", "event_no_etec", "etec", "full"]:
        ems = [
            r["methods"][m]["exact_match"]
            for r in all_results
            if m in r["methods"] and r["methods"][m].get("exact_match") is not None
        ]
        f1s = [
            r["methods"][m]["token_f1"]
            for r in all_results
            if m in r["methods"] and r["methods"][m].get("token_f1") is not None
        ]
        avg_em = sum(ems) / len(ems) if ems else 0
        avg_f1 = sum(f1s) / len(f1s) if f1s else 0
        print(f"{m:<20} {avg_em:>8.4f} {avg_f1:>10.4f}")

    summary = {
        "total_samples": len(all_results),
        "elapsed_s": round(total_time, 1),
    }
    (run_dir / "parallel_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved to {run_dir / 'parallel_summary.json'}")


if __name__ == "__main__":
    main()
