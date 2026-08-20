# QEMR Failure Diagnosis (S3)

> Stage 3 diagnosis of why QEMR failed to convert the 109 SUPERSEDE fires (S2)
> into reader-visible EM gains (`full` EM +0.02 only). Per
> `docs/S3-execution-prompt.md`, this report is built incrementally across
> Steps 1-5 and carries **no pre-declared expectation** (negative-result
> framework, `METHODOLOGY_CHANGE.md`).
>
> S3 only **diagnoses**; it does **not** modify `router.py` rules, the
> production `QEMR_WEIGHT_PROFILES`, R3, or the v3 prompt. Fixes route to
> post-S3 tasks needing independent-review approval.

- **Source v2 run**: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`
- **Reader model**: `mimo-v2.5` (4096-token budget)
- **S2 SUPERSEDE count**: 109 across 40/50 samples
- **S2 `full` EM**: 0.48 (Δ +0.02 vs v1); `vector_rag` EM = 0.56

## §1. Router accuracy diagnosis (Step 1, N9 scope)

**Scope**: read-only. `benchmarks/mechanism/router_diagnosis.py` runs the
deterministic `QueryRouter` (policy `query-router.rules.v1`) on LongMemEval
questions and emits a gold × predicted confusion matrix + rule-edit
suggestions. `src/evoeventmem/router.py` is **not** modified (N9).

**Gold-label mapping** (LongMemEval `question_type` → `QueryIntent`):
- `single-session-user` / `-assistant` / `-preference` → `SEMANTIC`
  (factual attribute lookup scoped to one session)
- `multi-session` → `HYBRID` (cross-session aggregation/comparison)
- `knowledge-update` → `TEMPORAL` (value changed across sessions)
- `temporal-reasoning` → `TEMPORAL` (first/last/most-recent/ordering)

### 50-question slice (matches the v2 benchmark run)

- **N = 50, accuracy = 4.0% (2/50)**
- All 50 v2 questions are `single-session-user` (gold = SEMANTIC).
- Predicted: 40 → HYBRID, 8 → TEMPORAL, 2 → SEMANTIC.

| gold \ pred | hybrid | semantic | temporal |
|---|---|---|---|
| semantic | 40 | 2 | 8 |

| intent | support | precision | recall | f1 |
|---|---|---|---|---|
| semantic | 50 | 100.0% | 4.0% | 7.7% |

**Why so low**: LongMemEval factual questions are phrased as
"What degree did I graduate with?" / "Where did I redeem a $5 coupon?".
The router's `_FACT_RE` keys on `what (is|are|was)` / `where (is|does|do)` /
`favorite` / `lives in`, so these "what + noun + past-tense verb" and
"where + did + subject + verb" phrasings do not match; the queries fall
through to HYBRID (low-confidence fallback).

### Full 500-question supplement (router-only, no LLM/benchmark)

> Pure deterministic router classification. This is **outside** the
> "不跑 500 题" scope guard, which restricts the full benchmark pipeline
> (retrieval + reader LLM), not the deterministic router function.

- **N = 500, accuracy = 38.0% (190/500)** — well below the N9 80% threshold.
- LongMemEval distribution: `single-session-user`=70, `single-session-assistant`=56,
  `single-session-preference`=30, `multi-session`=133, `knowledge-update`=78,
  `temporal-reasoning`=133.

| gold \ pred | episodic | graph | hybrid | procedural | semantic | temporal |
|---|---|---|---|---|---|---|
| hybrid | 3 | 6 | 80 | 0 | 9 | 35 |
| semantic | 19 | 3 | 96 | 0 | 15 | 23 |
| temporal | 1 | 14 | 83 | 1 | 17 | 95 |

| intent | support | precision | recall | f1 |
|---|---|---|---|---|
| hybrid | 133 | 30.9% | 60.2% | 40.8% |
| semantic | 156 | 36.6% | 9.6% | 15.2% |
| temporal | 211 | 62.1% | 45.0% | 52.2% |

### Mitigating nuance: weight-profile impact

`QEMR_WEIGHT_PROFILES` (`src/evoeventmem/retrieval.py:72`) assigns
**identical** weights to `SEMANTIC` and `HYBRID`:
`{DENSE: 1.0, GRAPH: 0.3, TEMPORAL: 0.2, EPISODIC: 0.1}`.

So the dominant mis-route (SEMANTIC→HYBRID, 40/50 on the v2 slice and
96/156 on the full set) does **not** change the QEMR weights applied.
The weight-altering mis-route is **SEMANTIC→TEMPORAL** (8/50 = 16% on the
v2 slice; 23/156 on the full set), which applies the temporal-heavy profile
`{TEMPORAL: 1.0, EPISODIC: 0.4, DENSE: 0.3, GRAPH: 0.2}` to factual lookups.

### Rule-modification suggestions (N9: not applied in S3)

1. Strengthen `_FACT_RE` for LongMemEval phrasings ("what + noun + did +
   subject + verb", "where did subject verb", "how many/much did").
2. Add a `knowledge-update` regex (e.g. "used to", "now", "has changed",
   "previously", "currently") so `knowledge-update` questions don't fall to
   HYBRID/TEMPORAL indiscriminately.
3. Review `_TEMPORAL_STRONG_RE` false-positives ("last month" in
   "What certification did I complete last month?" triggers TEMPORAL when
   the intent is a factual lookup).

These are **diagnoses**, not edits; `git diff src/evoeventmem/router.py`
remains empty (Step 7 scan).

### §1 verdict

- Full-500 router accuracy = **38.0%** < 80% N9 threshold → router
  mis-routing is a contributing factor to QEMR failure.
- But the dominant mis-route (SEMANTIC→HYBRID) is weight-neutral; the
  weight-altering mis-route (SEMANTIC→TEMPORAL) hits 16% of the v2 slice.
- Router rule edits route to a post-S3 task (N9 scope boundary).

_Artifact_: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/router_diagnosis_report.md`

## §2. Weight profile ablation (Step 2)

_Pending — see Step 2 below._

## §3. Embedding model comparison (Step 3)

_Pending — see Step 3 below._

## §4. M2 stale-memory judge (Step 4)

_Pending — see Step 4 below._

## §5. Root-cause conclusion + S5 routing (Step 5)

_Pending — written in Step 5._
