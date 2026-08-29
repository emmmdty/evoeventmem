# Stage 1b 独立审查报告

**结论**: CONDITIONAL PASS — S1b 的可达性测试在真实 LLM 输出上 PASS（四重 gate 在 22 对 event 上同时命中，SUPERSEDE 经验上可达），全栈回归/lint/mypy/smoke 全绿，scope/R3/reader/overclaim 全部守住；唯一未决项是 fact_slot 非空率 48.2% 略低于 spec 的 50% 门槛（spec fallback 明确这是"回 S1a 修 prompt"的触发条件，不是 S1b 失败）。S2 可在 S1a prompt 加固后进入。

## Implementation summary

The implementer added an `--extraction-only` short-circuit flag to `benchmarks/longmemeval/run.py` (threads `extraction_only: bool = False` through `run_experiment` / `_process_sample`; after writing the per-sample extraction snapshot, returns a minimal `SampleResult` with `methods={}` and skips `materialize_event_store` / `materialize_raw_turn_store` / methods loop / reader; `run_experiment` skips `finalize_run` when `extraction_only=True`). They added a 5-question mimo provider config (`configs/longmemeval/smoke5-mimo.toml`), a pure-function stats script (`benchmarks/mechanism/extraction_smoke.py` with `load_snapshot` / `compute_stats` / `format_stats` + CLI), 4 unit tests for the stats script, and a real-data reachability test (`tests/consolidation/test_etec_real_data_reachability.py`) that parses `MemoryRecord`s from the combined snapshot, enumerates all within-sample (source, target) pairs via `itertools.combinations`, computes the four SUPERSEDE gates by calling the real private consolidation functions (`_is_multi_valued` / `_same_fact_slot` / `_same_fact_value` / `_interval` / `_intervals_overlap`), and asserts ≥1 pair satisfies all four (or `pytest.xfail` with R3-blocking stats, or `pytest.skip` if the snapshot is missing). A real 5-question mimo extraction smoke was run under `runs/s1b/smoke5/` (gitignored): 666 events total, all carrying `metadata.extractor_prompt_version == "event-extraction.v2"`. The reachability test PASSED (22 pairs satisfy all four gates; 0 blocked by `multi_valued=True` since S1a did not emit `multi_valued`). No `src/evoeventmem/{extraction,consolidation,retrieval,router}.py` was modified; R3 is untouched; no reader/finalize artifacts at the run root; no new overclaim introduced in the new source content.

## §1-14 acceptance checklist

| # | Check | Result | Command output |
|---|---|---|---|
| 1 | `configs/longmemeval/smoke5-mimo.toml` 存在（5 题 mimo 配置） | ✅ | File exists; content verified — `sample_limit = 5`, extractor `mimo-v2.5` @ `https://opencode.ai/zen/go/v1`, methods list retained (irrelevant under `--extraction-only`) |
| 2 | `benchmarks/longmemeval/run.py` 加了 `--extraction-only` flag，不破坏现有 suite | ✅ | `git diff benchmarks/longmemeval/run.py` = +82/-11; flag added at argparse + threaded through `run_experiment` / `_process_sample`; full regression `217 passed` |
| 3 | `runs/s1b/smoke5/extraction_snapshot.json` 存在并含 5 题 snapshot | ✅ | `total snapshots: 5`; per-sample events: 118b2229=118, 1e043500=117, 51a45a95=127, 58bf7951=146, e47becba=158 (total 666) |
| 4 | `benchmarks/mechanism/extraction_smoke.py` 存在并跑通，打印 `fact_slot 非空率 ≥ 50%` | ⚠️ | Script exists and runs; **prints `fact_slot non-empty: 321 / 666 = 48.2%`** — below the 50% threshold. Per spec fallback (lines 320-324) this triggers "回 S1a 修 prompt", NOT an S1b FAIL |
| 5 | `tests/consolidation/test_etec_real_data_reachability.py` 存在，PASS 或 XFAIL | ✅ | `tests/consolidation/test_etec_real_data_reachability.py::test_four_gate_supersede_is_reachable_on_real_extraction_output PASSED [100%]` — `1 passed in 0.17s` |
| 6 | `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py -q` 全绿 | ✅ | `217 passed in 0.52s` |
| 7 | `uv run ruff check .` 全绿 | ✅ | `All checks passed!` |
| 8 | `uv run mypy src` 全绿 | ✅ | `Success: no issues found in 33 source files` |
| 9 | `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok" | ✅ | `smoke ok: The project switched the package registry to npmmirror. score=0.400` |
| 10 | `git diff src/evoeventmem/consolidation.py` / `retrieval.py` / `router.py` 为空 | ✅ | All three diffs empty (`exit=0`, no output) |
| 11 | `git diff src/evoeventmem/extraction.py` 为空 | ✅ | Empty diff (`exit=0`) — S1b did not touch S1a schema/prompt/wiring |
| 12 | `git status --short runs/` 无 commit（runs/ 是 gitignored） | ✅ | No output; `git check-ignore -v runs/s1b/smoke5/extraction_snapshot.json` → `.gitignore:20:runs/` confirms gitignored |
| 13 | `LLMEventExtractor.PROMPT_VERSION` 仍 `event-extraction.v2` | ✅ | `706:    PROMPT_VERSION = "event-extraction.v2"` (line 601 `rule.v1` belongs to the separate `RuleExtractor` class) |
| 14 | 独立审查 PASS（`docs/STAGE1b_REVIEW.md`） | ⚠️ | This document. Verdict = **CONDITIONAL PASS** (see dedicated fact_slot section) |

**Score: 13 ✅ / 1 ⚠️ / 0 ❌.** The single ⚠️ (criterion #4) is a real measurement result, not a spec violation — the spec's fallback section explicitly routes this to S1a prompt strengthening, not S1b failure.

## §1-10 review protocol

### §1. 验收标准逐条勾选

See the 14-row table above. Every criterion was re-run independently; outputs are quoted verbatim from the actual command output, not the implementer's report. The single non-green item (#4) is the fact_slot < 50% finding, addressed in the dedicated section below.

### §2. 5 题 snapshot 真实性

**Finding**: 5 per-sample snapshots present; every event across all 5 samples carries `metadata.extractor_prompt_version == "event-extraction.v2"`.

**Evidence** (independent re-run, not the implementer's report):
```
total snapshots: 5
118b2229: events=118 v2=118
1e043500: events=117 v2=117
51a45a95: events=127 v2=127
58bf7951: events=146 v2=146
e47becba: events=158 v2=158
```
Spot-check of one event in `e47becba` (the sample with the most events):
- `metadata.fact_slot = "possession.fitness_tracker"`
- `metadata.fact_value = "Fitbit Inspire HR"`
- `metadata.valid_from = "2023-02-15T00:00:00+00:00"`
- `metadata.valid_until = None`
- `metadata.extractor_prompt_version = "event-extraction.v2"` ✅
- top-level `valid_from = "2023-02-15T00:00:00Z"` (UTC-aware, Z suffix) ✅
- `evidence_refs` chain intact: `locator="chars=154:247"`, `raw_turn_id="85a1be56_1:0"`, `source_id="dataset=longmemeval/sample=e47becba/session=85a1be56_1/turn=85a1be56_1%3A0"`, `quote="I've been tracking my progress with my new Fitbit Inspire HR..."` ✅ provenance preserved.

This confirms S1a's v2 prompt actually entered the LLM call on real data — the spec's primary scientific question for §2.

### §3. fact_slot 非空率落地

**Finding**: 48.2% (321/666) — **below the spec's 50% threshold**. See the dedicated section below for the spec-fallback analysis.

**Evidence** (`uv run python -m benchmarks.mechanism.extraction_smoke runs/s1b/smoke5`):
```
=== S1b extraction smoke statistics ===
samples: 5  total events: 666
fact_slot non-empty:           321 / 666 =  48.2%
fact_value non-empty:          321 / 666 =  48.2%
metadata.valid_from:           321 / 666 =  48.2%
top-level valid_from:          321 / 666 =  48.2%
metadata.valid_until:            2 / 666 =   0.3%
top-level valid_to:              2 / 666 =   0.3%
metadata.multi_valued=True:     0 / 666 =   0.0%
distinct fact_value pairs (pre-consolidation): 22
--- per sample ---
  118b2229: events= 118 fact_slot= 51.7% valid_from= 51.7% valid_until=  0.0%
  1e043500: events= 117 fact_slot= 33.3% valid_from= 33.3% valid_until=  0.0%
  51a45a95: events= 127 fact_slot= 52.8% valid_from= 52.8% valid_until=  0.0%
  58bf7951: events= 146 fact_slot= 42.5% valid_from= 42.5% valid_until=  1.4%
  e47becba: events= 158 fact_slot= 58.2% valid_from= 58.2% valid_until=  0.0%
```

Observations:
- `fact_slot` / `fact_value` / `valid_from` rates are identical (321/666) — S1a's `_build_memory` correctly mirrors all three together (the contract: fact_slot present ⟺ fact_value present ⟺ valid_from present on the same event).
- `valid_until` is near-zero (0.3%) — end-events are rare on this 5-question slice; most extracted facts are start-only (single-event form, `valid_until=None`). Not a bug — the schema accepts both forms; S2 will measure the empirical state-change split rate.
- `multi_valued=True` is 0% — confirms S1a did NOT emit `multi_valued` (R3 untouched). This is why R3 did not block any pair in the reachability test (see §4).

### §4. 可达性测试 sound

**Finding**: The reachability test is sound and PASSes on real data. It parses real `MemoryRecord`s (no mocks), enumerates ALL within-sample (source, target) pairs, calls the real private consolidation gate functions, and PASS is the actual outcome.

**Evidence** — read `tests/consolidation/test_etec_real_data_reachability.py`:
- **(a) parses real MemoryRecords (no mocks)**: line 78 `memories.append(MemoryRecord.model_validate(raw))`. No `unittest.mock`, no `MagicMock`, no `StaticJSONChatModel`. The events come from the real mimo-v2.5 LLM output in `runs/s1b/smoke5/extraction_snapshot.json`.
- **(b) enumerates ALL within-sample pairs**: line 135 `for source, target in combinations(memories, 2)`. Pairs are grouped per-sample (line 130 `for sample_id, memories in per_sample`) — cross-sample pairs are correctly NOT enumerated (different conversations cannot SUPERSEDE each other). The skip guard at line 53-59 `pytest.skip`s cleanly if the snapshot file is missing.
- **(c) calls the real private consolidation gate functions**: lines 34-40 import `_interval`, `_intervals_overlap`, `_is_multi_valued`, `_same_fact_slot`, `_same_fact_value` from `evoeventmem.consolidation`. `_gate_breakdown` (lines 88-110) calls each one directly. No stubs, no reimplementations.
- **(d) PASS is the actual outcome (not xfail)**: `1 passed in 0.17s`. The test returns early at line 200-201 when `all_four_pairs > 0`. The xfail fallback (lines 207-213) prints R3-blocking stats and was NOT triggered.
- **xfail fallback is non-silent**: if the four-gate had not hit, `pytest.xfail` (line 207) carries a message with `total_pairs`, `first_three_pairs`, and `gate_blocked_after_first_three['multi_valued_false']` — not a silent skip. The skip path (line 54-59) is only for the missing-snapshot case.

**Independent re-enumeration** (re-derived, not the implementer's numbers):
```
total_pairs=44678 all_four=22 first_three=22 blocked_by_mv=0
gate_pass mv_false/slot/distinct/overlap = 44678/23/44632/24077
  118b2229: events=118 pairs=6903  first3=2  all4=2  blocked_mv=0
  1e043500: events=117 pairs=6786  first3=0  all4=0  blocked_mv=0
  51a45a95: events=127 pairs=8001  first3=0  all4=0  blocked_mv=0
  58bf7951: events=146 pairs=10585 first3=6  all4=6  blocked_mv=0
  e47becba: events=158 pairs=12403 first3=14 all4=14 blocked_mv=0
```

Discrepancies from the implementer's report (none affect the verdict):
- Implementer claimed "~27,307 within-sample pairs" — **actual is 44,678** (understated by ~40%; the implementer's pair-count arithmetic was off, but the test still enumerates the full set).
- Implementer claimed "16+ pairs satisfy ALL FOUR gates" — **actual is 22** ("16+" is technically true since 22 ≥ 16, but imprecise).
- Implementer claimed "0 blocked by multi_valued=True" — **verified correct** (`blocked_by_mv=0`; consistent with `multi_valued=0%` in the stats). The spec's expected R3-block scenario did not materialize because S1a did not emit `multi_valued` on any event.

**Scientific read**: the four-gate SUPERSEDE conditions DO co-occur on real mimo-v2.5 LLM output — 22 within-sample pairs satisfy `not multi_valued` AND `_same_fact_slot` AND `not _same_fact_value` AND `_intervals_overlap`. This is a positive reachability result. It does NOT claim empirical SUPERSEDE > 0 (5 questions is too small for a trigger-rate claim — the test docstring line 21 explicitly disclaims this).

**Minor finding (low severity)**: line 79 catches `except Exception` broadly and silently skips events that fail `MemoryRecord.model_validate`. The comment (lines 80-82) defends this ("reachability is about whether any pair hits the gates, not full coverage"). In practice zero events were skipped on this snapshot (666 raw → 666 parsed), so the broad catch had no effect here. Future runs on noisier LLM output should log skipped events to keep the audit trail observable — but this does not affect S1b's verdict.

### §5. R3 未被碰

**Finding**: R3 (`multi_valued` over-flagging) is untouched.

**Evidence**:
- `git diff src/evoeventmem/consolidation.py` = **EMPTY** (`exit=0`, no output).
- `git diff src/evoeventmem/extraction.py` = **EMPTY** (`exit=0`) — S1b did not touch S1a's schema/prompt/wiring.
- `grep -n "multi_valued" src/evoeventmem/extraction.py` → only 2 matches, both in a code COMMENT (lines 1214-1215) noting R3 is out of scope for S1a. NO `_EventDraft.multi_valued` field.
- `grep -nE "multi_valued|0\.7|supersede_contradiction_min" src/evoeventmem/consolidation.py` → only pre-existing matches:
  - `41: multi_valued: bool = False` (the `ConsolidationFeatures` schema field)
  - `48: supersede_contradiction_min: float = Field(default=0.7, ...)` (the threshold)
  - `399-400, 429, 433, 484, 490, 499, 511` (the existing `_score_pair` / `_is_multi_valued` call sites)
  - All pre-existing; no S1b additions.

### §6. scope 边界守住

**Finding**: Scope boundary held. Only `benchmarks/longmemeval/run.py` modified (the `--extraction-only` flag) + 4 new files. NO changes to `src/evoeventmem/*`, `benchmarks/experiments/`, or `tests/mechanism/`.

**Evidence**:
```
$ git diff --stat
 benchmarks/longmemeval/run.py | 93 ++++++++++++++++++++++++++++++++++++++-----
 1 file changed, 82 insertions(+), 11 deletions(-)
```
Untracked new files:
```
?? benchmarks/mechanism/extraction_smoke.py
?? configs/longmemeval/smoke5-mimo.toml
?? tests/benchmarks/test_extraction_smoke.py
?? tests/consolidation/test_etec_real_data_reachability.py
```
- `git diff src/evoeventmem/extraction.py` = EMPTY ✅
- `git diff src/evoeventmem/consolidation.py` = EMPTY ✅
- `git diff src/evoeventmem/retrieval.py` = EMPTY ✅
- `git diff src/evoeventmem/router.py` = EMPTY ✅
- No changes under `benchmarks/experiments/` or `tests/mechanism/` ✅

The run.py diff is the minimal `--extraction-only` short-circuit: adds the argparse flag (lines 305-313), threads `extraction_only: bool = False` through `run_experiment` (line 369) and `_process_sample` (line 439), writes the per-sample snapshot then returns a minimal `SampleResult` with `methods={}` (lines 465-507), and skips `finalize_run` (line 427). No retrieval/reader/metrics mainline code was modified.

### §7. 未跑 reader

**Finding**: Reader did not run. No reader/finalize artifacts at the run root.

**Evidence**:
```
$ ls runs/s1b/smoke5/answers.json runs/s1b/smoke5/predictions.json runs/s1b/smoke5/metrics.json runs/s1b/smoke5/FINALIZED.json 2>&1
ls: 无法访问 'runs/s1b/smoke5/answers.json': 没有那个文件或目录
ls: 无法访问 'runs/s1b/smoke5/predictions.json': 没有那个文件或目录
ls: 无法访问 'runs/s1b/smoke5/metrics.json': 没有那个文件或目录
ls: 无法访问 'runs/s1b/smoke5/FINALIZED.json': 没有那个文件或目录
```
`run.log` first line contains "extraction-only":
```
extraction-only: per-sample snapshots and extraction_snapshot.json written; retrieval/reader/finalize skipped.
```

**Minor observation (not a violation)**: the run root contains empty method-subdir placeholders (`etec/`, `full/`, `no_memory/`, etc. each with 0-byte `predictions.jsonl` / `retrieval.jsonl` / `samples.jsonl`). These are NOT the root-level `predictions.json` / `metrics.json` the spec checks for — they are 0-byte JSONL stubs created by the run-experiment initialization path, consistent with the reader not having produced any output. All 18 such files are 0 bytes (`wc -c` confirms). The spec's reader-ran check (root-level `answers.json` / `predictions.json` / `metrics.json`) passes cleanly.

### §8. 未引入新 overclaim

**Finding**: No new overclaim introduced by S1b's NEW source content. The two matches in the reachability test docstring are the spec's OWN contract language with an immediate disclaimer.

**Evidence**:
```
$ grep -nE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" \
    benchmarks/mechanism/extraction_smoke.py \
    tests/consolidation/test_etec_real_data_reachability.py \
    tests/benchmarks/test_extraction_smoke.py \
    configs/longmemeval/smoke5-mimo.toml
tests/consolidation/test_etec_real_data_reachability.py:13:real LLM output, and S2 has reason to expect SUPERSEDE > 0.
tests/consolidation/test_etec_real_data_reachability.py:21:This test does NOT claim SUPERSEDE > 0 empirically — 5 questions are too
```
- Line 13 is in the PASS-branch docstring: *"If the four gates co-occur on at least one pair within the 5-question extraction snapshot, the test PASSES — SUPERSEDE is empirically reachable on real LLM output, and S2 has reason to expect SUPERSEDE > 0."* This is a **forward-looking statement about S2's expectations**, not a claim that SUPERSEDE > 0 has been measured. It faithfully mirrors the spec's own contract language (spec line 11: "若四重 gate 命中 → S2 重跑有理由相信会测到 SUPERSEDE > 0").
- Line 21 immediately disclaims: *"This test does NOT claim SUPERSEDE > 0 empirically — 5 questions are too small for a statistically meaningful trigger-rate claim."*
- `git diff benchmarks/longmemeval/run.py | grep -nE "<overclaim phrases>"` → no matches (`exit=1`). The run.py diff contains no overclaim.
- `benchmarks/mechanism/extraction_smoke.py`, `tests/benchmarks/test_extraction_smoke.py`, `configs/longmemeval/smoke5-mimo.toml` → no matches at all.

The one pre-existing-doc match outside the exclusion list (`docs/S0-execution-prompt.md:3`) is the S0 execution prompt's own anti-overclaim rule text ("任何新增的强 claim 必须有 p-value + CI 支撑"), not a new S1b claim — it predates S1b.

**Read**: the implementer's "reachability test PASSED" claim is a **"four-gate co-occurs on ≥1 pair"** claim (verified: 22 pairs), NOT a "SUPERSEDE > 0" claim. This respects spec line 332 ("不声称 SUPERSEDE > 0 — 5 题样本太小").

### §9. git 状态

**Finding**: Working tree shows exactly the 5 intended files (1 modified + 4 new). HEAD is `8663fb8` (NOT advanced — implementer did not commit). `runs/` is gitignored.

**Evidence**:
```
$ git log --oneline -3
8663fb8 docs(s1b): execution prompt for real-data reachability smoke + fact_slot stats
162183c feat(s1a): ETEC R1/R1b schema — fact_slot/valid_from/valid_until/fact_value in extraction + prompt v2
8545635 docs(s1a): execution prompt for ETEC R1/R1b schema + prompt v2
```
```
$ git status --short
 M benchmarks/longmemeval/run.py
?? benchmarks/mechanism/extraction_smoke.py
?? configs/longmemeval/smoke5-mimo.toml
?? tests/benchmarks/test_extraction_smoke.py
?? tests/consolidation/test_etec_real_data_reachability.py
```
- HEAD = `8663fb8b4c81cbd47a71f6fdf4d5396305159c83` (the docs(s1b) execution-prompt commit). The implementer did NOT commit code — consistent with spec line 30 ("不擅自 commit").
- `git status --short runs/` → no output (runs/ is gitignored; `.gitignore:20:runs/`).
- The run.log records `"git_commit": "8663fb8b..."` and `"git_dirty": true` — the smoke was executed at HEAD 8663fb8 with the working-tree S1b code changes applied, exactly as expected.

### §10. AGENTS.md 边界

**Finding**: AGENTS.md boundaries respected.

- **No vendor-specific model client**: `benchmarks/mechanism/extraction_smoke.py` imports only stdlib (`argparse`, `json`, `sys`, `pathlib`, `typing`, `collections.abc`) — no OpenAI/Anthropic/Google client. `tests/consolidation/test_etec_real_data_reachability.py` imports `pytest` + `evoeventmem.consolidation` + `evoeventmem.domain.models` (internal ports/models, no vendor client). The run.py diff does not add any new vendor import. The vendor-specific provider configuration (mimo-v2.5 @ opencode.ai/zen/go) lives in `configs/longmemeval/smoke5-mimo.toml`, exactly where the spec put it.
- **Evidence provenance unbroken**: the reachability test and stats script are read-only on the snapshot JSON; they do not touch `evidence_refs` / `raw_turn_id` / `locator`. The spot-check in §2 confirmed `evidence_refs` intact in the real snapshot (with `locator`, `raw_turn_id`, `quote`, `source_id`, `source_type` all populated). The run.py `--extraction-only` short-circuit returns before `materialize_event_store` / `materialize_raw_turn_store`, so the materialization path (which the spec notes preserves provenance) is simply skipped, not modified.
- **UTC-aware datetimes**: the snapshot's `valid_from` values use ISO-8601 with explicit offset (`"2023-02-15T00:00:00Z"` / `"2023-02-15T00:00:00+00:00"`). The run.log `created_at` uses `"2026-08-19T03:23:40.077038Z"`. All UTC-aware.
- **Small pure functions + ports**: `load_snapshot`, `compute_stats`, `format_stats`, `_event_metadata`, `_gate_breakdown`, `_load_real_events` are all small pure (or read-only) functions. The reachability test depends on the `consolidation` module's public-ish gate functions, not a concrete vendor client.
- **No datasets / secrets / model weights / benchmark caches committed**: `runs/s1b/smoke5/` is gitignored; `configs/longmemeval/smoke5-mimo.toml` references `api_key_env = "OPENAI_API_KEY"` (the env-var name, not the key itself). No secrets in the diff.

## Dedicated section: fact_slot < 50% finding

**Measurement**: fact_slot non-empty rate = **48.2%** (321/666) on the 5-question mimo-v2.5 smoke. Per-sample spread: 33.3% / 42.5% / 51.7% / 52.8% / 58.2% — high variance on a 5-question slice; `1e043500` (33.3%) drags the mean below the threshold.

**Spec fallback (lines 320-324)** says, verbatim:
> 如果 fact_slot 非空率 < 50% → S1a prompt 在真实数据上未生效：
> 1. 不在 S1b 调 prompt——回 S1a 修 prompt（S1a 才管 prompt）。
> 2. 把 5 题 snapshot 的低 fact_slot 非空率证据写进 docs/STAGE1b_REVIEW.md，建议回到 S1a 加 prompt 强约束（如 required field + retry on missing fact_slot）。
> 3. 可达性测试 xfail（四重 gate 命中数为 0 是 fact_slot 缺失的下游结果）。

**What the implementer did correctly**:
- Did NOT adjust the prompt in S1b (correct — S1a owns the prompt; spec line 372 "不动 src/evoeventmem/extraction.py 的 schema/prompt/wiring").
- Did NOT tune thresholds or fake the extractor (spec line 376 "不用 fake extractor 凑数").
- `git diff src/evoeventmem/extraction.py` is EMPTY — the S1a prompt/schema/wiring is untouched.

**Where the spec's fallback prediction did NOT materialize**:
- Spec fallback point 3 predicts "可达性测试 xfail (四重 gate 命中数为 0 是 fact_slot 缺失的下游结果)". But the reachability test PASSED: 22 pairs satisfy all four gates (see §4). Even at 48.2% fact_slot rate, the four-gate is reachable on real LLM output. This is a positive reachability result — the spec's pessimistic branch (fact_slot missing → four-gate never hits) did not occur. 321 fact_slot-bearing events × 5 samples were enough to produce 22 SUPERSEDE-reachable pairs.

**This is NOT an overclaim of "SUPERSEDE > 0"**:
- The reachability test docstring (line 21) explicitly disclaims: "This test does NOT claim SUPERSEDE > 0 empirically — 5 questions are too small for a statistically meaningful trigger-rate claim."
- The "PASSED" verdict means "≥1 pair satisfies all four gates" — a reachability statement, not a trigger-rate statement. Per spec line 332, 5 questions is too small for a trigger-rate claim; S2 (50 questions) is the statistically meaningful measurement.

**Recommendation for S2 gating**:
- **S2 should NOT proceed at 48.2% fact_slot rate.** Before S2, return to S1a and strengthen the prompt so fact_slot rate is comfortably above 50% on a larger sample. Candidate fixes (per spec fallback point 2): make `fact_slot` a required field in the JSON schema with a `retry on missing fact_slot` loop in `_extract_single` (extraction.py:669-690 already has a 3-retry scaffold).
- **Alternative**: re-evaluate the 50% threshold itself on 50 questions. 5 questions is noisy — 4 of 5 samples are ≥ 42.5%, 3 of 5 are ≥ 51.7%, and `1e043500` (33.3%) is the single drag. On 50 questions the mean may stabilize above 50% without any prompt change. The spec set 50% "容许非状态类事实不产" (spec line 242); this 5-question slice is consistent with that allowance but too small to confirm.
- Either path is defensible; the decision belongs to S2's planning, not S1b. S1b's job was to surface the measurement and route it correctly — which it did.

## Risk register

1. **R1 (medium, blocks S2)**: fact_slot non-empty rate is 48.2% < 50% on the 5-question slice. Per spec fallback this routes to S1a prompt strengthening (required field + retry on missing fact_slot). S2 should be gated on either (a) S1a prompt strengthening pushing fact_slot comfortably above 50% on a 50-question sample, or (b) a documented re-evaluation of the 50% threshold on 50 questions. This is the sole unresolved item.
2. **R2 (low, expected)**: `valid_until` non-empty rate is 0.3% (2/666). End-events are rare on this slice — most facts are start-only (single-event form, `valid_until=None`). Not a bug (the schema accepts both forms); S2 will measure the empirical state-change split rate. The 22 SUPERSEDE-reachable pairs used open-ended intervals (`valid_until=None` → interval extends to +∞), so overlap is satisfied via the start-boundary logic in `_intervals_overlap`.
3. **R3 (low, positive)**: The spec expected R3 (`multi_valued=True` over-flagging) might block the four-gate. It did NOT — `multi_valued=0%` (S1a did not emit `multi_valued`), so `blocked_by_mv=0` across all 44,678 enumerated pairs. This means the four-gate is genuinely reachable on real LLM output, not reachable-via-R3-bypass. R3 remains unfixed and may still bite at S2 scale (50 questions), but on this 5-question slice it did not block.
4. **R4 (low)**: The reachability test catches `except Exception` broadly at line 79 when parsing events, silently skipping events that fail `MemoryRecord.model_validate`. The comment defends this ("reachability is about whether any pair hits the gates, not full coverage"). On this snapshot zero events were skipped (666 raw → 666 parsed), so the broad catch had no effect. Future runs on noisier LLM output should log skipped events to keep the audit trail observable.
5. **R5 (low, cosmetic)**: The run root contains empty method-subdir placeholders (`etec/predictions.jsonl` etc., all 0 bytes) created by the run-experiment initialization path even though the reader was skipped. These are NOT the root-level `predictions.json` / `metrics.json` the spec checks for; the reader-ran check passes. A future cleanup could suppress method-subdir creation under `--extraction-only`, but this is cosmetic and out of S1b scope.
6. **R6 (low, pre-existing)**: The smoke command output includes `score=0.400` and "The project switched the package registry to npmmirror" — pre-existing smoke behavior, unrelated to S1b. The spec only requires "smoke ok" to print, which it does.

## Implementer-report accuracy audit

The implementer's report was largely accurate; two numeric claims were imprecise (neither affects the verdict):
- **Claim "~27,307 within-sample pairs enumerated"** — actual is **44,678** (understated by ~40%; the test enumerates the full set regardless).
- **Claim "16+ pairs satisfy ALL FOUR gates"** — actual is **22** ("16+" is technically true since 22 ≥ 16, but imprecise).
- **Claim "0 blocked by multi_valued=True"** — verified correct.
- **Claim "fact_slot non-empty rate = 48.2% (321/666)"** — verified correct.
- **Claim "All 5 sample snapshots have every event's `metadata.extractor_prompt_version == event-extraction.v2`"** — verified correct (666/666).
- **Claim full regression / ruff / mypy / smoke green** — verified correct.
- **Claim `git diff` of `extraction.py` / `consolidation.py` / `retrieval.py` / `router.py` empty** — verified correct.
- **Claim `_EventDraft` has no `multi_valued` field + `PROMPT_VERSION` still `event-extraction.v2`** — verified correct.

## Sign-off

- The implementer did NOT commit code. `git status` shows 1 modified working-tree file (`benchmarks/longmemeval/run.py`) + 4 new untracked files, all unstaged. HEAD is `8663fb8` (`docs(s1b): execution prompt...`), the only S1b doc commit, allowed per spec.
- 13 of 14 acceptance criteria pass green; the 14th (fact_slot ≥ 50%) is at 48.2% and routes per the spec's explicit fallback to S1a prompt strengthening, NOT to S1b failure.
- The reachability test PASSes on real mimo-v2.5 LLM output (22 four-gate pairs), answering S1b's primary scientific question: SUPERSEDE is empirically reachable on real LongMemEval extraction output.
- R3, scope, reader-ran, overclaim, provenance, and AGENTS.md boundaries are all clean.
- Verdict: **CONDITIONAL PASS** — S1b is complete and the project may proceed to S2 **only after** S1a prompt strengthening pushes fact_slot rate comfortably above 50% on a 50-question sample (or the 50% threshold is re-evaluated on 50 questions with documented evidence). The reachability PASS and clean scope/R3/overclaim/provenance mean S1b itself introduced no defects that warrant returning to S1b for fixes.
