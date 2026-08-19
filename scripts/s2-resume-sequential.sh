#!/usr/bin/env bash
# S2 v2-factslot resume script: complete remaining sub-batches with limited
# parallelism to avoid HTTP 429 rate-limiting from the mimo-v2.5 endpoint.
#
# The full 10-parallel-batch launcher hit 429 after ~30/50 samples. This
# script resumes each non-finalized sub-batch with N_WORKERS parallelism
# (default 2) to stay under the rate limit.
#
# After all sub-batches finalize, runs the merge pass + finalize on the
# main run dir.
#
# Usage:
#   bash scripts/run50-parallel-v2-factslot.sh --resume  # full launcher resume
#   bash scripts/s2-resume-sequential.sh                  # this script (low parallelism)
#   N_WORKERS=3 bash scripts/s2-resume-sequential.sh      # custom parallelism
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

CONFIG="configs/longmemeval/test50-mimo.toml"
RUN_DIR="runs/publication/m13-longmemeval-test50-mimo-v2-factslot"
SUB_BATCH_PREFIX="runs/publication/m13-sub-v2-batch"
N_BATCHES=10
N_WORKERS=${N_WORKERS:-2}

echo "=== S2 v2-factslot sequential resume (N_WORKERS=$N_WORKERS) ==="
echo "Main run dir: $RUN_DIR"

# Check embedding tunnel
if ! nc -z 127.0.0.1 11436 2>/dev/null; then
    echo "ERROR: embedding tunnel (port 11436) is DOWN"
    exit 1
fi
echo "Embedding tunnel: UP"

# Phase 1: resume non-finalized sub-batches with limited parallelism.
echo ""
echo "=== Phase 1: resuming non-finalized sub-batches (max $N_WORKERS parallel) ==="
pending_batches=()
for i in $(seq 1 $N_BATCHES); do
    SUB_DIR="${SUB_BATCH_PREFIX}-${i}"
    if [[ -f "${SUB_DIR}/finalized/FINALIZED.json" ]]; then
        echo "  Batch $i: SKIP (already finalized)"
        continue
    fi
    pending_batches+=("$i")
done
echo "Pending batches: ${#pending_batches[@]} -> ${pending_batches[*]}"

process_batch() {
    local batch_num=$1
    local sub_dir="${SUB_BATCH_PREFIX}-${batch_num}"
    local batch_ids=$(uv run python -c "
import json
from pathlib import Path
data = json.loads(Path('data/raw/longmemeval/longmemeval_s_cleaned.json').read_bytes())
ids = [r['question_id'] for r in data[:50]]
start=$(((${batch_num} - 1) * 5))
print(' '.join(ids[start:start+5]))
")
    echo "  Batch $batch_num: $batch_ids -> $sub_dir"
    # Remove stale manifest/summary so run.py rebuilds with current git state,
    # but keep samples/ + model_cache/ so completed samples are skipped.
    rm -f "${sub_dir}/manifest.json" "${sub_dir}/summary.json" \
          "${sub_dir}/finalized/FINALIZED.json" 2>/dev/null || true
    rm -rf "${sub_dir}/etec" "${sub_dir}/event_no_etec" "${sub_dir}/full" \
           "${sub_dir}/full_context" "${sub_dir}/no_memory" "${sub_dir}/vector_rag" \
           "${sub_dir}/finalized" 2>/dev/null || true
    uv run python -m benchmarks.longmemeval.run \
        --config "$CONFIG" \
        --run-dir "$sub_dir" \
        --sample-ids $batch_ids > "${sub_dir}.log" 2>&1
}

# Process pending batches with limited parallelism (N_WORKERS at a time).
FAIL=0
while [[ ${#pending_batches[@]} -gt 0 ]]; do
    pids=()
    for ((w=0; w<N_WORKERS && ${#pending_batches[@]}>0; w++)); do
        batch_num=${pending_batches[0]}
        pending_batches=("${pending_batches[@]:1}")
        process_batch "$batch_num" &
        pids+=($!)
    done
    for pid in "${pids[@]}"; do
        if ! wait $pid; then
            echo "  WARNING: process $pid failed"
            FAIL=$((FAIL + 1))
        fi
    done
done
echo "Sub-batches done. Failures: $FAIL"

# Phase 2: copy per-sample files + model_cache from each sub-batch into main.
echo ""
echo "=== Phase 2: merging per-sample files into $RUN_DIR ==="
mkdir -p "$RUN_DIR/samples" "$RUN_DIR/model_cache/chat" "$RUN_DIR/model_cache/embeddings"
copied_samples=0
for i in $(seq 1 $N_BATCHES); do
    SUB_DIR="${SUB_BATCH_PREFIX}-${i}"
    if [[ ! -d "$SUB_DIR/samples" ]]; then
        continue
    fi
    for f in "$SUB_DIR"/samples/*.json; do
        [[ -f "$f" ]] || continue
        fname=$(basename "$f")
        if [[ ! -f "$RUN_DIR/samples/$fname" ]]; then
            cp "$f" "$RUN_DIR/samples/$fname"
            copied_samples=$((copied_samples + 1))
        fi
    done
    cp -n "$SUB_DIR/model_cache/chat/"*.json "$RUN_DIR/model_cache/chat/" 2>/dev/null || true
    cp -n "$SUB_DIR/model_cache/embeddings/"*.json "$RUN_DIR/model_cache/embeddings/" 2>/dev/null || true
    echo "  $SUB_DIR: merged"
done
echo "Copied $copied_samples new sample files into $RUN_DIR/samples/"

# Phase 3: merge pass + finalize on the main run dir.
echo ""
echo "=== Phase 3: merge pass + finalize on $RUN_DIR ==="
rm -f "$RUN_DIR/manifest.json" "$RUN_DIR/summary.json" "$RUN_DIR/retrieval.jsonl" \
      "$RUN_DIR/consolidation.jsonl" "$RUN_DIR/evidence.jsonl" "$RUN_DIR/extraction_snapshot.json"
rm -rf "$RUN_DIR/etec" "$RUN_DIR/event_no_etec" "$RUN_DIR/full" "$RUN_DIR/full_context" \
       "$RUN_DIR/no_memory" "$RUN_DIR/vector_rag" "$RUN_DIR/finalized"

time uv run python -m benchmarks.longmemeval.run \
    --config "$CONFIG" \
    --run-dir "$RUN_DIR" 2>&1 | tail -20

echo ""
echo "=== S2 v2-factslot resume complete ==="
echo "Run dir: $RUN_DIR"
echo ""
echo "Verification:"
echo "  ls $RUN_DIR/finalized/FINALIZED.json 2>/dev/null && echo 'FINALIZED' || echo 'NOT FINALIZED'"
echo "  ls $RUN_DIR/retrieval.jsonl && wc -l $RUN_DIR/retrieval.jsonl"
echo "  ls $RUN_DIR/samples/*.json 2>/dev/null | grep -v extraction_snapshot | wc -l"
