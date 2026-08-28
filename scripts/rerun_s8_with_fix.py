#!/usr/bin/env python3
"""Re-run S8 benchmark with fixed retrieval code.

Skips extraction (uses cached snapshots), only re-runs:
- materialization (store building)
- retrieval + reader (per method)

This is ~10x faster than full benchmark.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from benchmarks.common.extraction import ExtractionSnapshot

from benchmarks.common.memory_inputs import (
    materialize_event_store,
    materialize_raw_turn_store,
)
from benchmarks.common.normalization import NormalizedRecord, iter_longmemeval_records
from benchmarks.common.providers import ModelBundle, build_model_bundle, cache_for_run
from benchmarks.longmemeval.run import (
    LongMemEvalConfig,
    Method,
    _run_memory_method,
)
from benchmarks.retrieval_smoke import RetrievalHarness


def load_s8_samples(run_dir: Path) -> list[dict]:
    samples = []
    for f in (run_dir / 'samples').glob('*.json'):
        if 'extraction_snapshot' not in f.name:
            samples.append(json.loads(f.read_text()))
    return samples


def load_extraction_snapshot(run_dir: Path, sample_id: str) -> ExtractionSnapshot:
    path = run_dir / 'samples' / f'{sample_id}.extraction_snapshot.json'
    data = json.loads(path.read_text())
    return ExtractionSnapshot.model_validate(data)


def re_run_sample(
    sample: dict,
    config: LongMemEvalConfig,
    bundle: ModelBundle,
    s8_run_dir: Path,
) -> dict:
    sample_id = sample['sample_id']
    question_id = sample['question_id']
    category = sample['category']
    
    # Load extraction snapshot from S8
    snapshot = load_extraction_snapshot(s8_run_dir, sample_id)
    
    # Materialize stores
    user_id = sample_id
    
    raw_store, raw_ingestion = materialize_event_store(
        snapshot, apply_etec=False, user_id=user_id
    )
    
    etec_store, etec_ingestion = materialize_event_store(
        snapshot, apply_etec=True, embedding_model=bundle.embedding, user_id=user_id
    )
    
    # Re-run each method
    results = {}
    
    for method_name in ['etec', 'vector_rag', 'event_no_etec']:
        method = Method(method_name)
        
        if method_name == 'vector_rag':
            # Vector RAG uses raw turns
            from benchmarks.common.memory_inputs import build_raw_turn_corpus
            record = _load_record_for_sample(sample_id, config)
            corpus = build_raw_turn_corpus(record)
            vector_store, vector_ingestion = materialize_raw_turn_store(corpus, user_id=user_id)
            store = vector_store
        elif method_name == 'etec':
            store = etec_store
        else:
            store = raw_store
        
        RetrievalHarness(
            store,
            bundle.embedding,
            max_items_per_source=config.max_items_per_source,
            max_candidates_per_source=config.max_candidates_per_source,
        )
        
        # Get the question
        record = _load_record_for_sample(sample_id, config)
        question = record.questions[0]
        
        # Run method
        method_result = _run_memory_method(
            method,
            question,
            store,
            config,
            bundle,
            user_id=user_id,
            input_kind='raw_turn' if method_name == 'vector_rag' else 'event',
            snapshot_id=None if method_name == 'vector_rag' else snapshot.snapshot_id,
            write_latency_ms=0,
        )
        
        results[method_name] = method_result
    
    return {
        'sample_id': sample_id,
        'question_id': question_id,
        'category': category,
        'methods': results,
    }


def _load_record_for_sample(sample_id: str, config: LongMemEvalConfig) -> NormalizedRecord:
    records = list(iter_longmemeval_records(Path(config.dataset_path)))
    for r in records:
        if r.sample_id == sample_id:
            return r
    raise ValueError(f'Sample {sample_id} not found')


def main():
    s8_run_dir = Path('runs/publication/s8-stratified100')
    output_dir = Path('runs/t1-fix-validation')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load config
    config = LongMemEvalConfig(
        schema_version='longmemeval.config.v1',
        run_id_prefix='t1-fix-validation',
        dataset_path='data/raw/longmemeval/longmemeval_s_cleaned.json',
        methods=[Method.ETEC, Method.VECTOR_RAG, Method.EVENT_NO_ETEC],
        max_input_tokens=4096,
        max_extraction_tokens=262144,
        max_candidates_per_source=128,
        max_items_per_source=8,
        providers={
            "reader": {
                "provider": "openai_compatible",
                "model_id": "mimo-v2.5",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key_env": "OPENAI_API_KEY",
                "timeout_s": 120,
                "thinking": "disabled",
            },
            "extractor": {
                "provider": "openai_compatible",
                "model_id": "mimo-v2.5",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key_env": "OPENAI_API_KEY",
                "timeout_s": 180,
                "thinking": "disabled",
                "max_tokens": 65536,
            },
            "embedding": {
                "provider": "openai_compatible",
                "model_id": "qwen3-embedding-0.6b",
                "base_url": "http://127.0.0.1:11436/v1",
                "api_key_env": "EMBEDDING_API_KEY",
                "timeout_s": 60,
            },
        },
    )
    
    # Build model bundle
    bundle = build_model_bundle(config.providers, cache_for_run(output_dir))
    
    # Load S8 samples
    samples = load_s8_samples(s8_run_dir)
    print(f'Loaded {len(samples)} S8 samples')
    
    # Re-run all samples
    results = []
    start_time = time.time()
    
    for i, sample in enumerate(samples):
        sample_start = time.time()
        sid = sample["sample_id"]
        cat = sample["category"]
        print(f"[{i+1}/{len(samples)}] {sid} ({cat})...", end=" ", flush=True)
        
        try:
            result = re_run_sample(sample, config, bundle, s8_run_dir)
            results.append(result)
            elapsed = time.time() - sample_start
            print(f'OK ({elapsed:.1f}s)')
        except Exception as e:
            print(f'FAILED: {e}')
            import traceback
            traceback.print_exc()
    
    total_time = time.time() - start_time
    print(f'\nTotal time: {total_time:.1f}s ({total_time/len(samples):.1f}s/sample)')
    
    # Analyze results
    print('\n=== Results ===\n')
    
    by_method = defaultdict(list)
    by_category = defaultdict(lambda: defaultdict(list))
    
    for r in results:
        for method, data in r['methods'].items():
            em = data.get('exact_match', 0)
            by_method[method].append(em)
            by_category[r['category']][method].append(em)
    
    print('Per-Method EM:')
    for method, ems in sorted(by_method.items()):
        print(f'  {method:<20} EM={sum(ems)/len(ems):.3f}  (n={len(ems)})')
    
    print('\nPer-Category EM:')
    for cat in sorted(by_category):
        print(f'  {cat}:')
        for method, ems in sorted(by_category[cat].items()):
            print(f'    {method:<20} EM={sum(ems)/len(ems):.3f}  (n={len(ems)})')
    
    print('\nETEC vs VecRAG Delta:')
    for cat in sorted(by_category):
        if 'etec' in by_category[cat] and 'vector_rag' in by_category[cat]:
            etec_em = sum(by_category[cat]['etec']) / len(by_category[cat]['etec'])
            vecrag_em = sum(by_category[cat]['vector_rag']) / len(by_category[cat]['vector_rag'])
            delta = etec_em - vecrag_em
            print(f'  {cat:<30} ETEC={etec_em:.3f} VecRAG={vecrag_em:.3f} delta={delta:+.3f}')
    
    # Save results
    output_file = output_dir / 'results.json'
    output_file.write_text(json.dumps(results, indent=2))
    print(f'\nResults saved to {output_file}')


if __name__ == '__main__':
    main()
