# Stage 2 独立审查报告

**Status**: TEMPLATE — to be filled by an independent reviewer subagent after the v2-factslot run finalizes.

**Reviewer instructions** (per `docs/S2-execution-prompt.md` lines 419-436):
1. Run `EEM_S2_RUN_DIR=runs/publication/m13-longmemeval-test50-mimo-v2-factslot uv run pytest tests/benchmarks/test_s2_acceptance.py -v -s` and confirm every hard gate passes.
2. Run `uv run python -m benchmarks.mechanism.s2_diagnostics` and read the consolidated report.
3. Run `EEM_S1B_SNAPSHOT_PATH=runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s` (PASS or XFAIL both count as S2 pass).
4. Run `uv run python -m benchmarks.mechanism.replay --run-dir runs/publication/m13-longmemeval-test50-mimo-v2-factslot` (record divergence as known limitation, do not silently fix).
5. Spot-check 3-5 events across 3 samples for evidence_refs + raw_turn_id + locator chain integrity.
6. Confirm scope boundary held: `git diff src/evoeventmem/` is empty **after** S4b is committed (S4b is a separate stage that DID modify src/, but S2 does not).

**Verdict**: TBD (PASS / CONDITIONAL PASS / FAIL).

## §1. Acceptance criteria checklist (15 items, spec lines 348-364)

| # | Criterion | Result | Command output |
|---|---|---|---|
| 1 | `finalized/FINALIZED.json` exists | TBD | `ls runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json` |
| 2 | 50/50 samples (manifest valid) | TBD | `test_s2_acceptance.test_s2_50_samples_complete` |
| 3 | `retrieval.jsonl` = 200 lines | TBD | `test_s2_acceptance.test_s2_retrieval_jsonl_has_200_lines` |
| 4 | ETEC actions report generated | TBD | `test_s2_acceptance.test_s2_etec_actions_report_has_supersede_count` |
| 5 | fact_slot effective rate ≥ 50% (soft gate) | TBD | `test_s2_acceptance.test_s2_fact_slot_effective_rate_at_least_50_percent` |
| 6 | valid_from rate ≥ 50% (soft gate) | TBD | `test_s2_acceptance.test_s2_valid_from_rate_at_least_50_percent` |
| 7 | sentinel rate < 20% (soft gate) | TBD | `test_s2_acceptance.test_s2_sentinel_rate_below_20_percent` |
| 8 | Reachability test PASS or XFAIL | TBD | `test_s2_acceptance.test_s2_reachability_pass_or_xfail_on_v2_snapshot` |
| 9 | v1 vs v2 `full` EM comparison written to `docs/EVALUATION.md` | TBD | grep for `## test50-mimo-v2-factslot` in docs/EVALUATION.md |
| 10 | `pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q` green | TBD | (paste output) |
| 11 | `ruff check .` green | TBD | (paste output) |
| 12 | `mypy src` green | TBD | (paste output) |
| 13 | `evoeventmem.cli smoke` outputs "smoke ok" | TBD | (paste output) |
| 14 | `git diff src/evoeventmem/` empty (S2 is measurement stage) | TBD | `git diff src/evoeventmem/ \| wc -l` |
| 15 | `git diff --stat` only touches docs/EVALUATION.md + scripts/run50-parallel-v2-factslot.sh + docs/STAGE2_REVIEW.md + S2 test/diagnostic files | TBD | `git diff --stat` |

## §2. 50-question snapshot authenticity

- 50 per-sample snapshots present: TBD
- All events tagged `extractor_prompt_version == "event-extraction.v3"`: TBD
- Spot-check of 3-5 events across 3 samples for `evidence_refs + raw_turn_id + locator` chain integrity: TBD

## §3. ETEC actions report authenticity

- Independent re-run of the ETEC Counter script: TBD
- SUPERSEDE count matches `summary.json.methods.etec.actions.SUPERSEDE` (or samples dir aggregation): TBD

## §4. fact_slot / sentinel rate S1c-vs-S2 comparison

- S1c 5-question baseline: effective rate 60.3%, sentinel rate 39.7%, reachability 107 four-gate pairs.
- S2 50-question v2 measurement: effective rate TBD%, sentinel rate TBD%, reachability TBD four-gate pairs.
- Per-sample distribution comparison: TBD
- Sentinel rate < 20%? (routes S2 → S3 if yes, → S5 path A limitations if no)

## §5. Reachability test sound

- Reachability test calls real consolidation functions (no mocks): TBD
- PASS or XFAIL both count as S2 pass: TBD
- Snapshot path parameterization works (`EEM_S1B_SNAPSHOT_PATH` honored): TBD

## §6. R3 untouched

- `git diff src/evoeventmem/consolidation.py` empty: TBD
- `_EventDraft` still has no `multi_valued` field: TBD
- `consolidation.py:876` `multi_valued` / `supersede_contradiction_min=0.7` / `_same_fact_slot` references unchanged: TBD

## §7. Scope boundary held

- `git diff --stat` touches only the allowed S2 + S4b files: TBD
- No `src/evoeventmem/*` changes after S4b commit: TBD

## §8. No new overclaim

- `rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效' docs/EVALUATION.md src/`: TBD
- S2 section only states measurements ("SUPERSEDE = N", "v2 EM = X vs v1 = 0.46"), no thesis-翻盘 / ETEC-有效 / significant-improvement claims.

## §9. No cross-model comparison

- v1 vs v2 both use mimo-v2.5: TBD
- v2 vs 24-question deepseek run NOT mentioned in the S2 section: TBD

## §10. Replay/online consistency

- v2 run replay vs online `ingestion.etec.actions` divergence: TBD
- If divergent, recorded as known limitation (not silently fixed): TBD

## §11. Git state

- Working tree clean or changes explainable: TBD
- HEAD not advanced by S2 implementer (no auto-commit): TBD

## §12. AGENTS.md boundary

- Core memory logic not vendor-specific: TBD
- Evidence provenance unbroken: TBD
- UTC-aware datetimes: TBD
- No datasets / secrets / model weights / benchmark caches committed: TBD

## Risk register

1. S4b vector_rag latency fix moved embedding cost from search to write time — v1 vs v2 search latency NOT directly comparable; EM still comparable.
2. Sentinel rate S1c measured 39.7% on 5 questions; if S2 50-question measurement also ≥ 20%, route to S3/S5 (do NOT re-tune prompt in S2 per AGENTS.md anti-fishing).
3. SUPERSEDE = 0 in v1; if v2 also 0, pivot to S5 path A (negative-result paper).
4. Embedding server (qwen3-embedding-0.6b) had OOM issues during S4b verification; restarted with BATCH_SIZE=8 + max_length=2048. v2 run may hit transient 5xx → progressive shrink handles it, but slow.
5. Run is in background (PID 674612); if SSH session dies or machine reboots, run aborts. Resume with `bash scripts/run50-parallel-v2-factslot.sh --resume`.

## Sign-off

- Verdict: TBD
- Conditions (if CONDITIONAL PASS): TBD
- Next-stage routing: TBD
EOF
