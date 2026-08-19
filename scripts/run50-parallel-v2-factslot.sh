#!/usr/bin/env bash
# S2 / S4b v2-factslot 50-run parallel launcher.
#
# Splits the 50 LongMemEval samples into 10 batches of 5, runs batch 1
# to create the run directory, then batches 2-10 in parallel, then a
# merge pass to collect all per-sample results into shared artifacts.
#
# This launcher is the v2 counterpart of scripts/run50-parallel.sh. The
# only intentional differences from v1 are:
#   - RUN_DIR points at the v2-factslot directory so v1 baseline
#     artifacts are not overwritten (spec line 236).
#   - Finalize is run at the end so the S2 acceptance tests can pick up
#     ``finalized/FINALIZED.json`` immediately (spec line 348).
#
# S4b prerequisites (must already be in the working tree):
#   - CachedEmbeddingModel.batch_uncached_texts_into_single_call (the
#     batching fix in src/evoeventmem/models/cache.py).
#   - OpenAICompatibleEmbeddingClient progressive-shrink (handles
#     intermittent server-side 5xx on large batches in
#     src/evoeventmem/infra/openai_compatible.py).
#   - Pre-warm at write time in benchmarks/longmemeval/run.py (shifts
#     embedding cost from search_latency_ms to vector_index_ms).
#
# Usage:
#   bash scripts/run50-parallel-v2-factslot.sh
#   bash scripts/run50-parallel-v2-factslot.sh --resume  # resume after a crash
#
# Prereqs: .env configured (mimo-v2.5), embedding tunnel up (port 11436).
# Embedding tunnel rebuild:
#   cpolar-ssh-update  # refresh the cpolar port in ~/.ssh/config
#   ssh -o StrictHostKeyChecking=accept-new -f -N -L 11436:127.0.0.1:11436 gpu-5090
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

CONFIG="configs/longmemeval/test50-mimo.toml"
RUN_DIR="runs/publication/m13-longmemeval-test50-mimo-v2-factslot"
N_BATCHES=10
BATCH_SIZE=5
RESUME_MODE=0
if [[ "${1:-}" == "--resume" ]]; then
    RESUME_MODE=1
fi

echo "=== S2 v2-factslot 50-run parallel launcher ==="
echo "Config: $CONFIG"
echo "Run dir: $RUN_DIR"
echo "Batches: $N_BATCHES x $BATCH_SIZE samples"
echo "Resume mode: $RESUME_MODE (1=resume existing run dir, 0=fresh run)"

# Check embedding tunnel
if ! nc -z 127.0.0.1 11436 2>/dev/null; then
    echo "ERROR: embedding tunnel (port 11436) is DOWN"
    echo "Fix: cpolar-ssh-update && ssh -o StrictHostKeyChecking=accept-new -f -N -L 11436:127.0.0.1:11436 gpu-5090"
    exit 1
fi
echo "Embedding tunnel: UP"

# Quick model availability check
echo "Checking mimo-v2.5 availability..."
curl -s -m 10 https://opencode.ai/zen/go/v1/chat/completions \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "Content-Type: application/json" \
    -H "User-Agent: opencode/1.0" \
    -d '{"model":"mimo-v2.5","messages":[{"role":"user","content":"OK"}],"max_tokens":50}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('Model:', 'OK' if 'choices' in d else d.get('error',{}).get('message','UNKNOWN'))"

# Get all 50 sample IDs from the dataset (first 50)
IDS=$(uv run python -c "
import json
from pathlib import Path
data = json.loads(Path('data/raw/longmemeval/longmemeval_s_cleaned.json').read_bytes())
ids = [r['question_id'] for r in data[:50]]
print(' '.join(ids))
")
read -ra SAMPLE_IDS <<< "$IDS"
echo "Sample IDs: ${#SAMPLE_IDS[@]} total"

# Split into batches
declare -a BATCHES
for i in $(seq 0 $((N_BATCHES - 1))); do
    start=$((i * BATCH_SIZE))
    BATCHES[$i]="${SAMPLE_IDS[@]:$start:$BATCH_SIZE}"
done

mkdir -p "$RUN_DIR"

if [[ "$RESUME_MODE" == "1" ]]; then
    # Resume: batch 1 already completed (5 samples + manifest exist). Re-run
    # batches 2-10 in parallel with --resume-dir, then merge + finalize.
    echo ""
    echo "=== Resume mode: launching batches 2-10 in parallel ==="
    PIDS=()
    for i in $(seq 1 $((N_BATCHES - 1))); do
        BATCH="${BATCHES[$i]}"
        echo "  Batch $((i+1)): $BATCH"
        uv run python -m benchmarks.longmemeval.run \
            --config "$CONFIG" \
            --resume-dir "$RUN_DIR" \
            --sample-ids $BATCH > "runs/publication/v2-batch$((i+1)).log" 2>&1 &
        PIDS+=($!)
    done

    echo "Waiting for $((N_BATCHES - 1)) parallel batches..."
    FAIL=0
    for pid in "${PIDS[@]}"; do
        if ! wait $pid; then
            echo "  WARNING: process $pid failed"
            FAIL=$((FAIL + 1))
        fi
    done
    echo "Parallel batches done. Failures: $FAIL/$((N_BATCHES - 1))"

    # Merge pass: read all per-sample files and rewrite shared artifacts
    echo ""
    echo "=== Merge pass: collecting all 50 samples into shared artifacts ==="
    time uv run python -m benchmarks.longmemeval.run \
        --config "$CONFIG" \
        --resume-dir "$RUN_DIR" 2>&1 | tail -10
else
    # Batch 1: create the run directory + run first batch
    echo ""
    echo "=== Batch 1/10: creating run directory + processing first $BATCH_SIZE samples ==="
    BATCH1="${BATCHES[0]}"
    time uv run python -m benchmarks.longmemeval.run \
        --config "$CONFIG" \
        --run-dir "$RUN_DIR" \
        --sample-ids $BATCH1 2>&1 | tail -5

    echo "Batch 1 complete. Run dir: $RUN_DIR"

    # Batches 2-10: run in parallel with --resume-dir
    echo ""
    echo "=== Batches 2-10: launching $((N_BATCHES - 1)) parallel processes ==="
    PIDS=()
    for i in $(seq 1 $((N_BATCHES - 1))); do
        BATCH="${BATCHES[$i]}"
        echo "  Batch $((i+1)): $BATCH"
        uv run python -m benchmarks.longmemeval.run \
            --config "$CONFIG" \
            --resume-dir "$RUN_DIR" \
            --sample-ids $BATCH > "runs/publication/v2-batch$((i+1)).log" 2>&1 &
        PIDS+=($!)
    done

    echo "Waiting for $((N_BATCHES - 1)) parallel batches..."
    FAIL=0
    for pid in "${PIDS[@]}"; do
        if ! wait $pid; then
            echo "  WARNING: process $pid failed"
            FAIL=$((FAIL + 1))
        fi
    done
    echo "Parallel batches done. Failures: $FAIL/$((N_BATCHES - 1))"

    # Merge pass: read all per-sample files and rewrite shared artifacts
    echo ""
    echo "=== Merge pass: collecting all 50 samples into shared artifacts ==="
    time uv run python -m benchmarks.longmemeval.run \
        --config "$CONFIG" \
        --resume-dir "$RUN_DIR" 2>&1 | tail -10
fi

# Finalize
echo ""
echo "=== Finalize ==="
time uv run python -m benchmarks.longmemeval.run \
    --config "$CONFIG" \
    --resume-dir "$RUN_DIR" \
    --finalize-only 2>&1 | tail -10

echo ""
echo "=== S2 v2-factslot 50-run complete ==="
echo "Run dir: $RUN_DIR"
echo ""
echo "Verification:"
echo "  ls $RUN_DIR/finalized/FINALIZED.json 2>/dev/null && echo 'FINALIZED' || echo 'NOT FINALIZED'"
echo "  ls $RUN_DIR/retrieval.jsonl && wc -l $RUN_DIR/retrieval.jsonl"
echo "  ls $RUN_DIR/samples/*.json 2>/dev/null | wc -l"
echo ""
echo "S2 acceptance tests (run after this script finishes):"
echo "  EEM_S2_RUN_DIR=$RUN_DIR uv run pytest tests/benchmarks/test_s2_acceptance.py -v -s"
echo "  uv run python -m benchmarks.mechanism.s2_diagnostics"
