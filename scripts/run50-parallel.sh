#!/usr/bin/env bash
# Parallel 50-run launcher: splits 50 samples into 10 batches of 5,
# runs batch 1 to create the run directory, then batches 2-10 in parallel,
# then a merge pass to collect all per-sample results into shared artifacts.
#
# Usage: bash scripts/run50-parallel.sh
# Prereqs: .env configured (mimo-v2.5), embedding tunnel up (port 11436)
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

CONFIG="configs/longmemeval/test50-mimo.toml"
RUN_DIR="runs/publication/m13-longmemeval-test50-mimo"
N_BATCHES=10
BATCH_SIZE=5

echo "=== 50-run parallel launcher ==="
echo "Config: $CONFIG"
echo "Run dir: $RUN_DIR"
echo "Batches: $N_BATCHES x $BATCH_SIZE samples"

# Check embedding tunnel
if ! nc -z 127.0.0.1 11436 2>/dev/null; then
    echo "ERROR: embedding tunnel (port 11436) is DOWN"
    echo "Fix: ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090"
    exit 1
fi
echo "Embedding tunnel: UP"

# Quick model availability check
echo "Checking model availability..."
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
        --sample-ids $BATCH > "runs/publication/batch$((i+1)).log" 2>&1 &
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

echo ""
echo "=== 50-run complete ==="
echo "Run dir: $RUN_DIR"
echo ""
echo "Verification:"
echo "  ls $RUN_DIR/finalized/FINALIZED.json 2>/dev/null && echo 'FINALIZED' || echo 'NOT FINALIZED'"
echo "  ls $RUN_DIR/retrieval.jsonl && wc -l $RUN_DIR/retrieval.jsonl"
echo "  ls $RUN_DIR/samples/*.json | wc -l"
