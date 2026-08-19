#!/usr/bin/env bash
# S2 / S4b v2-factslot 50-run parallel launcher (per-batch sub-dir pattern).
#
# Splits the 50 LongMemEval samples into 10 batches of 5. Each batch runs
# in its own sub-directory (``runs/publication/m13-sub-v2-batch-N/``) with
# ``--run-dir`` so the per-batch manifest only lists that batch's 5 sample
# IDs — this avoids the manifest-drift refusal that fires when batches 2-10
# try to ``--resume-dir`` against a manifest with only batch 1's 5 IDs.
#
# After all 10 batches complete, per-sample files are copied into the main
# run directory, then a merge pass loads all 50 samples from disk, writes
# the main manifest + summary, and finalizes.
#
# This matches the v1 baseline pattern (``runs/publication/m13-sub-batch-N``
# sub-dirs + ``m13-longmemeval-test50-mimo/`` main dir).
#
# Usage:
#   bash scripts/run50-parallel-v2-factslot.sh
#   bash scripts/run50-parallel-v2-factslot.sh --resume  # skip already-done batches
#
# S4b prerequisites (must already be in the working tree):
#   - CachedEmbeddingModel batching (src/evoeventmem/models/cache.py)
#   - OpenAICompatibleEmbeddingClient progressive-shrink (src/evoeventmem/infra/openai_compatible.py)
#   - Pre-warm at write time (benchmarks/longmemeval/run.py)
#
# Prereqs: .env configured (mimo-v2.5), embedding tunnel up (port 11436).
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

CONFIG="configs/longmemeval/test50-mimo.toml"
RUN_DIR="runs/publication/m13-longmemeval-test50-mimo-v2-factslot"
SUB_BATCH_PREFIX="runs/publication/m13-sub-v2-batch"
N_BATCHES=10
BATCH_SIZE=5
RESUME_MODE=0
if [[ "${1:-}" == "--resume" ]]; then
    RESUME_MODE=1
fi

echo "=== S2 v2-factslot 50-run parallel launcher ==="
echo "Config: $CONFIG"
echo "Main run dir: $RUN_DIR"
echo "Sub-batch prefix: $SUB_BATCH_PREFIX"
echo "Batches: $N_BATCHES x $BATCH_SIZE samples"
echo "Resume mode: $RESUME_MODE (1=skip already-done sub-batches, 0=fresh run)"

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

# Phase 1: launch all 10 per-batch sub-runs in parallel (or skip if --resume
# and the sub-batch is already done).
echo ""
echo "=== Phase 1: launching $N_BATCHES per-batch sub-runs in parallel ==="
PIDS=()
for i in $(seq 0 $((N_BATCHES - 1))); do
    BATCH="${BATCHES[$i]}"
    BATCH_NUM=$((i + 1))
    SUB_DIR="${SUB_BATCH_PREFIX}-${BATCH_NUM}"
    if [[ "$RESUME_MODE" == "1" ]] && [[ -f "${SUB_DIR}/finalized/FINALIZED.json" ]]; then
        echo "  Batch $BATCH_NUM: SKIP (already finalized at $SUB_DIR)"
        continue
    fi
    echo "  Batch $BATCH_NUM: $BATCH -> $SUB_DIR"
    rm -rf "$SUB_DIR"
    uv run python -m benchmarks.longmemeval.run \
        --config "$CONFIG" \
        --run-dir "$SUB_DIR" \
        --sample-ids $BATCH > "${SUB_DIR}.log" 2>&1 &
    PIDS+=($!)
done

if [[ ${#PIDS[@]} -gt 0 ]]; then
    echo "Waiting for ${#PIDS[@]} parallel batches..."
    FAIL=0
    for pid in "${PIDS[@]}"; do
        if ! wait $pid; then
            echo "  WARNING: process $pid failed"
            FAIL=$((FAIL + 1))
        fi
    done
    echo "Parallel batches done. Failures: $FAIL/${#PIDS[@]}"
fi

# Phase 2: copy per-sample files from each sub-batch into the main run dir.
echo ""
echo "=== Phase 2: merging per-sample files into $RUN_DIR ==="
mkdir -p "$RUN_DIR/samples" "$RUN_DIR/model_cache"
copied_samples=0
for i in $(seq 0 $((N_BATCHES - 1))); do
    BATCH_NUM=$((i + 1))
    SUB_DIR="${SUB_BATCH_PREFIX}-${BATCH_NUM}"
    if [[ ! -d "$SUB_DIR/samples" ]]; then
        echo "  $SUB_DIR: no samples dir, skipping"
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
    # Merge model_cache (chat + embeddings) into the main run dir so the
    # merge pass can reuse cached LLM calls if it needs to re-process any
    # sample.
    if [[ -d "$SUB_DIR/model_cache/chat" ]]; then
        mkdir -p "$RUN_DIR/model_cache/chat"
        cp -n "$SUB_DIR/model_cache/chat/"*.json "$RUN_DIR/model_cache/chat/" 2>/dev/null || true
    fi
    if [[ -d "$SUB_DIR/model_cache/embeddings" ]]; then
        mkdir -p "$RUN_DIR/model_cache/embeddings"
        cp -n "$SUB_DIR/model_cache/embeddings/"*.json "$RUN_DIR/model_cache/embeddings/" 2>/dev/null || true
    fi
    echo "  $SUB_DIR: merged"
done
echo "Copied $copied_samples new sample files into $RUN_DIR/samples/"

# Phase 3: merge pass on the main run dir. This builds the main manifest
# (all 50 expected IDs), loads each sample from disk, writes the run-root
# artifacts (extraction_snapshot.json, retrieval.jsonl, summary.json,
# per-method predictions), and finalizes.
echo ""
echo "=== Phase 3: merge pass + finalize on $RUN_DIR ==="
# Remove any stale manifest from a previous failed merge; the merge pass
# will rebuild it from the current (clean) git state.
rm -f "$RUN_DIR/manifest.json" "$RUN_DIR/summary.json" "$RUN_DIR/retrieval.jsonl" \
      "$RUN_DIR/consolidation.jsonl" "$RUN_DIR/evidence.jsonl" "$RUN_DIR/extraction_snapshot.json"
rm -rf "$RUN_DIR/etec" "$RUN_DIR/event_no_etec" "$RUN_DIR/full" "$RUN_DIR/full_context" \
       "$RUN_DIR/no_memory" "$RUN_DIR/vector_rag" "$RUN_DIR/finalized"

time uv run python -m benchmarks.longmemeval.run \
    --config "$CONFIG" \
    --run-dir "$RUN_DIR" 2>&1 | tail -20

echo ""
echo "=== S2 v2-factslot 50-run complete ==="
echo "Run dir: $RUN_DIR"
echo ""
echo "Verification:"
echo "  ls $RUN_DIR/finalized/FINALIZED.json 2>/dev/null && echo 'FINALIZED' || echo 'NOT FINALIZED'"
echo "  ls $RUN_DIR/retrieval.jsonl && wc -l $RUN_DIR/retrieval.jsonl"
echo "  ls $RUN_DIR/samples/*.json 2>/dev/null | grep -v extraction_snapshot | wc -l"
echo ""
echo "S2 acceptance tests (run after this script finishes):"
echo "  EEM_S2_RUN_DIR=$RUN_DIR uv run pytest tests/benchmarks/test_s2_acceptance.py -v -s"
echo "  uv run python -m benchmarks.mechanism.s2_diagnostics"
