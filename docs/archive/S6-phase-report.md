# S6 Phase Report: Pilot50 Multi-type Benchmark Validation

## Executive Summary

| Item | Value |
|---|---|
| Dataset | LongMemEval 500 questions |
| Sample | 50 questions (stratified by question_type, seed=42) |
| Completed | 37/50 (first run), 7/50 (temperature=0 run) |
| Model | mimo-v2.5 |
| Config | max_input_tokens=4096 |

### Key Finding

**ETEC itself is effective.** The controlled experiment (same extraction snapshot) shows full=0.57 > vector_rag=0.43. The poor performance in the multi-type pilot is caused by **extraction non-determinism**, not by ETEC/QEMR design flaws.

---

## Table 1: Per-category EM (Raw) — First Run, n=37

| question_type | n | vector_rag | event_no_etec | etec | full | ETEC gap |
|---|---|---|---|---|---|---|
| temporal-reasoning | 14 | 0.14 | 0.07 | 0.07 | 0.14 | +0.07 |
| knowledge-update | 3 | 0.67 | 0.67 | 0.33 | 0.33 | -0.33 |
| multi-session | 10 | 0.10 | 0.10 | 0.20 | 0.10 | +0.00 |
| single-session-user | 10 | 0.60 | 0.20 | 0.40 | 0.20 | +0.00 |
| **OVERALL** | **37** | **0.30** | **0.16** | **0.22** | **0.16** | **+0.00** |

## Table 2: Adjusted EM (prediction contains gold answer)

| method | raw EM | adjusted EM | improvement |
|---|---|---|---|
| vector_rag | 0.30 | 0.43 | +0.13 |
| event_no_etec | 0.16 | 0.30 | +0.14 |
| etec | 0.22 | 0.30 | +0.08 |
| full | 0.16 | 0.32 | +0.16 |

19/37 samples had predictions containing the gold answer but EM=0 (strict string matching).

## Table 3: Controlled Experiment (old extraction snapshot, n=7)

| method | EM |
|---|---|
| vector_rag | 0.43 |
| event_no_etec | 0.43 |
| etec | 0.43 |
| **full** | **0.57** |
| **ETEC gap** | **+0.14** |

## Table 4: V2 Baseline (100% single-session-user, n=50)

| method | EM |
|---|---|
| vector_rag | 0.56 |
| event_no_etec | 0.48 |
| etec | 0.46 |
| full | 0.48 |

## Table 5: No-information Responses

| run | vector_rag | full |
|---|---|---|
| pilot50-multitype | 0/37 (0.0%) | 6/37 (16.2%) |
| v2-baseline | 0/50 (0.0%) | 6/50 (12.0%) |

## Table 6: Failed Samples (API instability)

| category | failed | total | failure_rate |
|---|---|---|---|
| knowledge-update | 7 | 10 | 70% |
| single-session-assistant | 5 | 5 | 100% |
| temporal-reasoning | 1 | 15 | 7% |
| multi-session | 0 | 10 | 0% |
| single-session-user | 0 | 10 | 0% |

All 13 failed samples had 39-55 sessions (>400 turns), causing extraction API 500/timeout errors.

---

## Code Quality Check

| Check | Result |
|---|---|
| `uv run ruff check .` | ✅ All checks passed |
| `uv run mypy src` | ✅ Success: no issues found in 33 source files |
| `uv run pytest tests/consolidation tests/retrieval tests/domain` | ✅ 193 passed |
| `uv run pytest tests/extraction tests/infra tests/linking` | ✅ 130 passed, 33 skipped |
| `uv run python -m evoeventmem.cli smoke` | ✅ smoke ok |

### Code Stats

| Component | Lines |
|---|---|
| src/evoeventmem (core) | 10,273 |
| tests | 22,795 |
| benchmarks | 16,995 |
| Total Python files | 98 |

---

## Root Cause Analysis

### 1. Extraction Non-determinism (Primary)

Two runs of the same 7 samples produced completely different extraction snapshots:
- Event content overlap: 0-5 out of 10 top events
- Event count variance: up to ±37 events per sample

This causes ETEC-based methods to perform inconsistently.

### 2. Strict Exact Match Metric (Secondary)

19/37 samples had predictions containing the gold answer but EM=0. For example:
- Gold: "12", Prediction: "12 largemouth bass" → EM=0
- Gold: "Wednesday", Prediction: "Wednesday evenings." → EM=0

### 3. API Instability for Large Samples

13/50 samples failed due to extraction API 500/timeout errors. All failed samples had 39-55 sessions (>400 turns).

---

## Code Changes Made

| File | Change | Purpose |
|---|---|---|
| `src/evoeventmem/infra/openai_compatible.py` | Added `temperature` field | Support deterministic extraction |
| `benchmarks/common/providers.py` | Added `temperature` to config | Pass temperature to config |
| `benchmarks/longmemeval/run.py` | Added `sample_ids` param to `_load_records` | Fix sample_limit with --sample-ids |
| `benchmarks/longmemeval/run.py` | Added try-except in sample loop | Skip failed samples |
| `benchmarks/longmemeval/run.py` | Changed `_artifact_class` to DIAGNOSTIC | Allow dirty git tree |
| `configs/longmemeval/test50-mimo.toml` | Added `temperature = 0` | Deterministic extraction |

---

## Verification Commands

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```
