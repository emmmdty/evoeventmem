# Stage 2 独立审查报告

**Reviewer**: independent subagent (did NOT author S2 code), per `docs/S2-execution-prompt.md` lines 419-436.
**Run under review**: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot` (n=50, mimo-v2.5 reader/extractor, v3 prompt, finalized 2026-08-19).
**Review date**: 2026-08-19.

**Verdict**: **CONDITIONAL PASS** — all hard gates pass; the S2 scope boundary is in fact held; the single automated test `FAILED` line is a false positive caused by a parsing bug in the test itself (not an S2 scope violation). Soft-gate xfail (sentinel rate ≥ 20%) is by design and routes per spec. Proceed to S3 with the unresolved items below documented.

## §1. Acceptance criteria checklist (15 items, spec lines 348-364)

| # | Criterion | Result | Command output |
|---|---|---|---|
| 1 | `finalized/FINALIZED.json` exists | ✅ | `ls runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json` → 834 bytes; `test_s2_run_dir_finalized` PASSED |
| 2 | 50/50 samples (manifest valid) | ✅ | `expected=50 completed=50 missing=[] valid=True`; `test_s2_50_samples_complete` PASSED |
| 3 | `retrieval.jsonl` = 200 lines | ✅ | `lines: 200 (want 200)`; `test_s2_retrieval_jsonl_has_200_lines` PASSED |
| 4 | ETEC actions report generated (with SUPERSEDE count) | ✅ | `{'ADD': 7188, 'MERGE': 1770, 'REJECT': 352, 'SUPERSEDE': 109}`; `SUPERSEDE count: 109`; `test_s2_etec_actions_report_has_supersede_count` PASSED |
| 5 | fact_slot effective rate ≥ 50% (soft gate) | ✅ | `real fact_slot: 6295 (66.8%)`; `test_s2_fact_slot_effective_rate_at_least_50_percent` PASSED |
| 6 | valid_from rate ≥ 50% (soft gate) | ✅ | `valid_from present: 6294 / 9419 = 66.8%`; `test_s2_valid_from_rate_at_least_50_percent` PASSED |
| 7 | sentinel rate < 20% (soft gate) | ⚠️ XFAIL | `sentinel: 3124 / 9419 = 33.2% (limit: 20%)`; `test_s2_sentinel_rate_below_20_percent` XFAIL (expected; spec line 354 routes to S3/S5, does NOT block S2) |
| 8 | Reachability test PASS or XFAIL | ✅ | subprocess `tests/consolidation/test_etec_real_data_reachability.py` → `1 passed in 3.30s` (PASS, not XFAIL); `test_s2_reachability_pass_or_xfail_on_v2_snapshot` PASSED |
| 9 | v1 vs v2 `full` EM comparison written to `docs/EVALUATION.md` | ✅ | `## test50-mimo-v2-factslot (n=50, mimo-v2.5, v3 prompt, 2026-08-19)` header present at line 163; 6-method EM table filled (no TBD); `test_s2_v1_vs_v2_em_comparison_table_can_be_built` PASSED |
| 10 | `pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q` green | ✅ | `222 passed in 0.49s` |
| 11 | `ruff check .` green | ✅ | `All checks passed!` |
| 12 | `mypy src` green | ✅ | `Success: no issues found in 33 source files` |
| 13 | `evoeventmem.cli smoke` outputs "smoke ok" | ✅ | `smoke ok: The project switched the package registry to npmmirror. score=0.400` |
| 14 | `git diff src/evoeventmem/` empty (S2 is measurement stage) | ✅ | `git diff src/evoeventmem/ \| wc -l` → `0`; `test_s2_scope_no_src_changes_beyond_s4b` PASSED |
| 15 | `git diff --stat` only touches allowed S2 files | ⚠️ (false-positive FAIL) | `git diff --name-only` → `docs/EVALUATION.md` only (IN scope). But `test_s2_scope_diff_stat_touches_only_expected_files` FAILED with `offending=['1']` — a parsing bug: the test skips the summary line only on `endswith("changed")` or `"files changed" in line`, so the **singular** `" 1 file changed, 47 insertions(+), 28 deletions(-)"` line is not skipped and its first token `'1'` is flagged as an offending path. The actual scope boundary IS held. |

**Acceptance-test summary**: `1 failed, 12 passed, 1 xfailed in 3.93s`. The 1 xfailed is by design (sentinel). The 1 failed is a false positive (test-side parsing bug). Both are documented as conditions below.

## §2. 50-question snapshot authenticity

- 50 per-sample snapshots present: ✅ `samples/` contains 50 `{sample_id}.json` (ingestion) + 50 `{sample_id}.extraction_snapshot.json` (per-sample snapshots) = 100 files; the top-level `extraction_snapshot.json` (20.8 MB) aggregates all 50.
- All events tagged `extractor_prompt_version == "event-extraction.v3"`: ✅ `total events: 9419`, `v3-tagged events: 9419`, `non-v3 events: 0` (100.0% coverage).
- Spot-check of events across 3 samples for `evidence_refs + raw_turn_id + locator` chain integrity: ✅
  - Sentinel events (`fact_slot='none'`) from `001be529`, `0862e8bf`, `118b2229`: each has `evidence_refs[0]` with `locator` (e.g. `chars=37:148`), `metadata.raw_turn_id` (e.g. `3722ea11_2:0`), `metadata.sample_id`, `metadata.session_id`, `metadata.speaker='user'`, `quote` (verbatim source text), `source_id` (e.g. `dataset=longmemeval/sample=001be529/session=3722ea11_2/turn=3722ea11_2%3A0`), `source_type='turn'` — chain fully intact.
  - Real-slot events from `001be529` (e.g. `fact_slot='plan.trip_destination'`, `'preference.travel_dates'`, `'plan.trip_budget'`): same intact chain, plus `valid_from` populated as UTC-aware ISO 8601 (e.g. `2023-05-21T03:29:00+00:00`).

## §3. ETEC actions report authenticity

- Independent re-run of the ETEC `Counter` script (spec lines 370-382): ✅
  ```
  ETEC actions: {'ADD': 7188, 'MERGE': 1770, 'REJECT': 352, 'SUPERSEDE': 109}
  SUPERSEDE count: 109
  Samples with SUPERSEDE > 0: 40 / 50
  ```
- SUPERSEDE count matches `summary.json` aggregation: ✅ The per-sample `ingestion.etec.actions` aggregated across the 50 sample files yields SUPERSEDE = 109 over 40/50 samples (10 samples had 0 SUPERSEDE). v1 baseline SUPERSEDE = 0. The 109 count is the first time SUPERSEDE fires on real data. (Note: `summary.json` does not store a top-level `methods.etec.actions` field — both v1 and v2 return `None` for that path; the actions live in per-sample files and are aggregated by the diagnostic. This is a persistence-contract observation, not a discrepancy.)

## §4. fact_slot / sentinel rate S1c-vs-S2 comparison

- S1c 5-question baseline: effective rate 60.3% (625/1036), sentinel rate 39.7% (411/1036), reachability 107 four-gate pairs.
- S2 50-question v2 measurement: effective rate **66.8% (6295/9419)**, sentinel rate **33.2% (3124/9419)**, valid_from rate **66.8% (6294/9419)**, valid_until rate **0.7% (63/9419)**, reachability test **PASSES** (≥1 within-sample four-gate pair satisfies all SUPERSEDE gates).
- Per-sample distribution: sentinel rate ranges 18.1% (af8d2e46) to 48.2% (60d45044); 4 samples ≤ 20% ceiling (af8d2e46 18.1%, caf9ead2 18.4%, 25e5aa4f 18.4%, b86304ba 22.3% just above); the 50-question mean (33.2%) is more stable than S1c's 5-question 39.7% (-6.5pp) but still well above the 20% ceiling.
- Sentinel rate < 20%? **No** (33.2% ≥ 20%). Per spec line 354 + 413-417: do NOT re-tune the prompt in S2 (AGENTS.md anti-fishing); route to S3/S5 decision. The 33.2% is a documented weakness for the S5 paper limitations section.

## §5. Reachability test sound

- Reachability test calls real consolidation functions (no mocks): ✅ `test_four_gate_supersede_is_reachable_on_real_extraction_output` instantiates the real `ETECConsolidator` and exercises the real four-gate SUPERSEDE predicate against the v2 extraction snapshot's `MemoryRecord`s.
- PASS or XFAIL both count as S2 pass: ✅ The test **PASSED** (not XFAIL) in 3.30s — at least one within-sample event pair on the v2 snapshot satisfies `not multi_valued` AND `_same_fact_slot` AND `not _same_fact_value` AND `_intervals_overlap`. This is the strongest possible reachability outcome.
- Snapshot path parameterization works (`EEM_S1B_SNAPSHOT_PATH` honored): ✅ Running `EEM_S1B_SNAPSHOT_PATH=runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s` → `1 passed in 3.38s`.

## §6. R3 untouched

- `git diff src/evoeventmem/consolidation.py` empty: ✅ `git diff src/evoeventmem/consolidation.py | wc -l` → `0`.
- `_EventDraft` still has no `multi_valued` field added by S2: ✅ `multi_valued: bool = False` at `consolidation.py:41` is pre-existing (S1a-era); S2 introduced no new `multi_valued` population. The only `multi_valued`-adjacent text in `extraction.py` is at lines 1438-1439 — a comment stating `multi_valued` is intentionally NOT populated (`# R3 (multi_valued over-flagging) is out of scope for S1a.`).
- `consolidation.py` `multi_valued` / `0.7` / `supersede_contradiction_min` / `_same_fact_slot` references unchanged: ✅ All references (lines 41, 400, 429, 433, 484, 490, 499, 511, 874, 876) are pre-existing; S2 diff on `src/evoeventmem/` is empty (0 lines). R3 was not modified.

## §7. Scope boundary held

- `git diff --stat` touches only the allowed S2 files: ✅ (actual) `git diff --name-only` → `docs/EVALUATION.md` only; `git diff --numstat` → `47  28  docs/EVALUATION.md`. This is within the allowed S2 set (`docs/EVALUATION.md`, `docs/STAGE2_REVIEW.md`, `docs/S2-execution-prompt.md`, `scripts/run50-parallel-v2-factslot.sh`, `tests/benchmarks/test_s2_acceptance.py`, `tests/benchmarks/test_s4b_vector_rag_latency.py`, `benchmarks/mechanism/s2_diagnostics.py`).
  - ⚠️ The automated test `test_s2_scope_diff_stat_touches_only_expected_files` reports FAILED, but this is a **false positive** from a parsing bug in the test: it skips the trailing summary line only when `line.endswith("changed")` or `"files changed" in line`, so the singular `" 1 file changed, 47 insertions(+), 28 deletions(-)"` line is parsed as a path and its first token `'1'` is flagged. The test should be fixed to skip the summary line via a regex like `^\s*\d+ files? changed` (see Risk register item 6 + Condition 1).
- No `src/evoeventmem/*` changes after S4b commit: ✅ `git diff src/evoeventmem/ | wc -l` → `0`. S4b commit `46b7b38` (the only recent src/ change) touched exactly: `benchmarks/longmemeval/run.py`, `src/evoeventmem/infra/openai_compatible.py`, `src/evoeventmem/models/cache.py`, `tests/benchmarks/test_s4b_vector_rag_latency.py`, `tests/models/test_model_cache.py`, `tests/models/test_openai_compatible.py` — matches the expected S4b file set.

## §8. No new overclaim

- `rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效' docs/EVALUATION.md src/`: ✅ no matches in either `docs/EVALUATION.md` (exit 1) or `src/` (no matches).
- S2 section language is appropriately measured: the doc explicitly states "**a slight improvement but not a翻盘**", "**ETEC went from harmful to neutral**", "**still not the best method**" (`full` 0.48 < `vector_rag` 0.56), and "**necessary condition for the positive thesis, NOT sufficient**". The automated `test_s2_no_overclaim_in_evaluation_md` PASSED. S2 only claims measurements ("SUPERSEDE = 109 across 40/50 samples", "v2 `full` EM = 0.48 vs v1 = 0.46").

## §9. No cross-model comparison

- v1 vs v2 both use mimo-v2.5: ✅ `v1 reader: mimo-v2.5`, `v2 reader: mimo-v2.5`, `same_model=True`.
- v2 vs 24-question deepseek run NOT mentioned as a comparison in the S2 section: ✅ The S2 section header is `## test50-mimo-v2-factslot (n=50, mimo-v2.5, v3 prompt, 2026-08-19)` and explicitly states `**Same-model comparison**: v1 and v2 both use mimo-v2.5 ... Cross-model comparison against the 24-question deepseek-v4-flash run is forbidden (N8).` The deepseek run is referenced only to forbid comparison, not to draw one.

## §10. Replay/online consistency

- v2 run replay vs online `ingestion.etec.actions`: ⚠️ Minor divergence on 2/50 samples; aggregate totals consistent.
  - Full 50-sample offline replay (212.3s) vs per-sample online `ingestion.etec.actions`:
    - **48/50 samples: consistent** (replay actions dict == online dict).
    - **2/50 samples: ADD↔MERGE reclassification divergence** (4 actions total, no SUPERSEDE divergence):
      - `577d4d32`: replay `{ADD:127, MERGE:43, REJECT:5, SUPERSEDE:1}` vs online `{ADD:126, MERGE:44, REJECT:5, SUPERSEDE:1}` (Δ: one MERGE→ADD).
      - `a06e4cfe`: replay `{ADD:169, REJECT:4, MERGE:24}` vs online `{ADD:166, MERGE:27, REJECT:4}` (Δ: three MERGE→ADD).
    - Aggregate replay: `{ADD:7192, MERGE:1766, SUPERSEDE:109, REJECT:352}` vs online `{ADD:7188, MERGE:1770, SUPERSEDE:109, REJECT:352}` — Δ ADD +4 / MERGE -4, SUPERSEDE and REJECT exact matches.
  - **SUPERSEDE count is perfectly consistent** (109 = 109), which is the key thesis signal.
- Per spec line 432: recorded as a **known limitation**, NOT silently fixed. Likely root cause: the replay sorts candidates by `(event_time, content)` while online ingestion may have used a different tie-break among same-`event_time` events, causing a few borderline ADD/MERGE classifications to swap. This does not affect SUPERSEDE because the SUPERSEDE gate is not order-sensitive at the swap boundary. S3 may investigate the ordering determinism if ADD/MERGE bucketing matters; for S2 the thesis-relevant SUPERSEDE count is reproducible.

## §11. Git state

- Working tree changes explainable: ✅ `git status --short` → ` M docs/EVALUATION.md` only (the S2 EM-comparison write-up). All other S2 scaffolding (`scripts/run50-parallel-v2-factslot.sh`, `tests/benchmarks/test_s2_acceptance.py`, `benchmarks/mechanism/s2_diagnostics.py`, `docs/S2-execution-prompt.md`, `docs/STAGE2_REVIEW.md`) is already committed in the five `fix(s2-infra)` / `feat(s2-infra)` commits `b609c6b`→`d079c55`.
- HEAD not advanced by S2 implementer during this review: ✅ HEAD = `d079c55 fix(s2-infra): add sequential resume script for rate-limited runs`. No auto-commit was performed. Per spec line 461, the implementer must ask the user before committing; the remaining `docs/EVALUATION.md` change is uncommitted as expected.
- `git status --short runs/` empty (gitignored): ✅ no tracked `runs/` changes.

## §12. AGENTS.md boundary

- Core memory logic not vendor-specific: ✅ `src/evoeventmem/` unchanged by S2; the only vendor-coupled code (`infra/openai_compatible.py`) was touched by S4b, not S2, and the domain/service layers do not import it.
- Evidence provenance unbroken: ✅ Spot-checked events carry full `evidence_refs` chains (`locator` + `raw_turn_id` + `source_id` + `quote` + `speaker` + `source_type`); every event points back to source evidence (spec line: "Reject memory records that cannot point back to source evidence" — satisfied).
- UTC-aware datetimes: ✅ Spot-checked `valid_from` values are ISO 8601 with explicit `+00:00` offset (e.g. `2023-05-21T03:29:00+00:00`).
- No datasets / secrets / model weights / benchmark caches committed: ✅ `runs/` is gitignored (verified empty `git status --short runs/`); `model_cache/` lives under `runs/.../model_cache/` and is therefore also gitignored. No secrets or datasets in the diff.

## Risk register

1. **Sentinel rate 33.2% ≥ 20% ceiling (known weakness).** 3124/9419 events have `fact_slot='none'`. Per spec line 413-417, this is NOT fixed in S2 (anti-fishing); it routes to S3/S5. The S5 paper limitations section must state that ~1/3 of extracted events are sentinels, and S3/S5 must decide whether to redesign the v3 prompt's contrast-pair example or pivot the SUPERSEDE basis away from `fact_slot`. This is the single largest measurement-quality risk.
2. **`full` EM improvement is marginal (+0.02, 0.46→0.48), not a翻盘.** The flagship is still below `vector_rag` (0.56). SUPERSEDE = 109 is a necessary but not sufficient condition for the positive thesis; S3 must verify QEMR actually consumes the 109 superseded memories and the reader benefits. Risk: S3 finds QEMR ignores SUPERSEDE outputs → thesis still unsupported despite SUPERSEDE > 0.
3. **`event_no_etec` and `etec` EM both dropped 0.06 (0.54→0.48, 0.52→0.46).** The v2 run added ETEC structuring overhead that may have slightly hurt the non-flagship event methods. The `full` vs `event_no_etec` gap closed from -0.08 (v1) to 0.00 (v2) — ETEC went harmful→neutral, but not helpful. S3 must isolate the cause.
4. **Replay/online ADD↔MERGE divergence on 2/50 samples (577d4d32, a06e4cfe).** 4 actions reclassified; SUPERSEDE consistent. Likely a candidate-sort tie-break difference. Recorded as known limitation per spec line 432; do NOT silently fix. S3 may harden the sort determinism if ADD/MERGE bucketing matters.
5. **S4b moved embedding cost from search to write time → v1 vs v2 latency NOT directly comparable.** v2 `search_latency_ms` ≈ 2,333 ms p50; v2 `vector_index_ms` ≈ 68,623 ms p50. EM is still comparable (latency does not affect EM). Any S3 latency claim must use v2-only or re-run v1 on the S4b-fixed code.
6. **False-positive test failure in `test_s2_scope_diff_stat_touches_only_expected_files`.** The test's summary-line skip logic only handles plural `"files changed"` / `endswith("changed")`, not singular `"1 file changed, ..."`. Produces `offending=['1']`. The actual scope boundary IS held (only `docs/EVALUATION.md` modified). The test should be fixed (see Condition 1) so the suite is green before S3.
7. **Minor doc inconsistency: `valid_until` rate.** `docs/EVALUATION.md` S2 section states `1.4% (est.)` but the diagnostic measures `0.7% (63/9419)`. The doc value is roughly 2× the measured value. Cosmetic; should be corrected to `0.7% (63/9419)` for accuracy, but does not affect routing (no spec floor on `valid_until`).
8. **`replay` module has no CLI entry point.** `uv run python -m benchmarks.mechanism.replay --run-dir ...` produces no output and exits 0 because the module has no `if __name__ == "__main__"` block. The replay check requires a custom inline script. Not a blocker for S2, but the spec's step-9 command as written does nothing; S3 should either add a CLI wrapper or document the programmatic entry point.
9. **`summary.json` does not persist `methods.etec.actions`.** Both v1 and v2 return `None` for `summary.json`'s `methods.etec.actions` path; actions live only in per-sample `ingestion.etec.actions`. The diagnostic aggregates them correctly, but a reader inspecting only `summary.json` would see no actions. Consider persisting the aggregate in `summary.json` for S3.

## Sign-off

- **Verdict**: **CONDITIONAL PASS**
- **Conditions** (must be resolved, but do not block entering S3 — track to closure in S3):
  1. Fix the false-positive parsing bug in `test_s2_scope_diff_stat_touches_only_expected_files` (handle singular `"1 file changed"` summary line) so the automated suite is fully green. The S2 scope boundary is in fact held; this is a test-quality fix.
  2. Correct the `valid_until` rate in `docs/EVALUATION.md` from `1.4% (est.)` to `0.7% (63/9419)` (cosmetic accuracy).
- **Known limitations routed forward** (do NOT fix in S2 per spec/AGENTS.md):
  - Sentinel rate 33.2% ≥ 20% ceiling — documented weakness; S3/S5 decides prompt redesign vs. SUPERSEDE-basis pivot.
  - Replay/online ADD↔MERGE divergence on 2/50 samples — recorded, not silently fixed; S3 may harden sort determinism.
- **Next-stage routing**: **S3 (QEMR diagnosis + M2 stale-judge)**. Per spec lines 459-460, SUPERSEDE > 0 (109) routes to S3. Because `full` EM did not翻盘 (+0.02 only), S3's job is the middle route: explain why QEMR fails to convert the 109 SUPERSEDE memories into reader-visible gains, and run M2 stale-judge. S5 paper framing stays on the positive path pending S3, with sentinel rate as a documented limitation.
- **Scope reminder for S3**: do NOT re-tune the v3 prompt in S2/S3 to lower the sentinel rate (anti-fishing); do NOT modify R3 (`multi_valued`); do NOT silently fix the replay divergence; do NOT cross-model compare against the 24-question deepseek run.
