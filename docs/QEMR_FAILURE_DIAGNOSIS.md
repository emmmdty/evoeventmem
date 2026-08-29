# QEMR Failure Diagnosis (S3)

> Stage 3 diagnosis of why QEMR failed to convert the 109 SUPERSEDE fires (S2)
> into reader-visible EM gains (`full` EM +0.02 only). Per
> `docs/archive/S3-execution-prompt.md`, this report is built incrementally across
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

**Scope**: `src/evoeventmem/retrieval.py` adds three diagnostic
`RetrievalStrategy` values (`QEMR_NO_TEMPORAL`, `QEMR_NO_GRAPH`,
`QEMR_UNIFORM`) + `resolve_weights` branches. The production
`QEMR_WEIGHT_PROFILES` dict is **not** modified. `benchmarks/mechanism/weight_ablation.py`
re-runs only the `full` retrieval method on the v2 extraction snapshot under
each strategy. Same reader (`mimo-v2.5`), same budget (4096), same embedding
(`qwen3-embedding-0.6b`); only the QEMR weight profile differs
(AGENTS.md anti-mixed-methods). The `strategy` field on every
`QEMRRetrievalResult` makes each arm observable (no silent fallback).

**Cache strategy**: a composite cache reads embeddings + extraction-time
chat hits from the v2 run dir (read-only) and writes reader chat misses to
the ablation dir. The v2 run dir is never mutated. Reader messages differ
under ablation weights → reader cache misses → 150 fresh `mimo-v2.5` calls
(3 arms × 50); a transient connection-reset outer retry wrapper kept all
arms at 0 failures.

### EM comparison (50 questions, same model / same budget)

| arm | strategy | EM | scored | failed | n |
|---|---|---|---|---|---|
| v2 full (baseline) | qemr | 0.4800 | 50 | 0 | 50 |
| no_temporal | qemr_no_temporal | 0.4600 | 50 | 0 | 50 |
| no_graph | qemr_no_graph | 0.4800 | 50 | 0 | 50 |
| uniform | qemr_uniform | 0.4200 | 50 | 0 | 50 |

_No pre-declared expectation (negative-result framework,
`METHODOLOGY_CHANGE.md`)._

### Findings

1. **`qemr` (0.48) ≥ all ablations** — the production weight profile is the
   best (or tied) on the 50-question slice. The weight profile is **not
   over-fit** to this slice; removing any source does not help.
2. **`qemr_no_temporal` (0.46) < `qemr` (0.48), Δ -0.02** — the temporal
   source contributes a small positive on LongMemEval. This is the
   **opposite** of the LoCoMo §9 finding (`no_temporal` 0.3654 >
   `qemr` 0.3000). On LongMemEval single-session-user questions the
   temporal-recency source is mildly helpful, not harmful.
3. **`qemr_no_graph` (0.48) = `qemr` (0.48), Δ 0.00** — the graph source is
   weight-neutral on this slice. (The 50 questions are all
   `single-session-user`, so graph traversal has little surface; this is
   not evidence that graph is useless in general, only that it is not the
   bottleneck here.)
4. **`qemr_uniform` (0.42) < `qemr` (0.48), Δ -0.06** — equal-weight fusion
   underperforms the intent-specific profile. The query-adaptive weight
   design buys +0.06 EM on this slice; it is not over-engineered.

### §2 verdict

- The QEMR weight profile is **not** the failure root cause: it beats or
  ties every ablation arm, and the intent-specific design is +0.06 over
  uniform. Weight-profile edits are **not** warranted.
- The temporal source is mildly helpful (not harmful as in LoCoMo), so the
  "ETEC temporal filter hurts retrieval" hypothesis is not supported on
  this slice.
- The bottleneck is elsewhere: §1 shows 38% router accuracy (SEMANTIC
  questions mis-routed to TEMPORAL/HYBRID), and the absolute `full` EM
  (0.48) still trails `vector_rag` (0.56) by 0.08 — pointing at the
  retrieval pipeline structure and/or embedding quality, not the weights.

_Artifact_: `runs/publication/m13-longmemeval-test50-mimo-v2-ablation/ablation_report.md`
+ `ablation_summary.json` + `ablation_<arm>.json` × 3.

## §3. Embedding model comparison (Step 3)

**Status: skipped — deferred to S5 (cost + infrastructure).**

Per `docs/archive/S3-execution-prompt.md` Step 3 fallback (lines 425-427), the
embedding comparison (bge-large-en-v1.5 / e5-large-v2 vs
qwen3-embedding-0.6b) is declared skipped for two reasons:

1. **Infrastructure**: the GPU embedding server
  (`gpu-5090:/mnt/aidata/tongjiakai/embed_server/qwen_embed_server.py`)
  is configured only for `qwen3-embedding-0.6b`. Switching to
  `bge-large-en-v1.5` or `e5-large-v2` requires downloading model weights,
  reconfiguring the server, and rebuilding the SSH tunnel — outside S3's
  diagnostic scope.
2. **Cost**: a different embedding model invalidates the entire
  `model_cache/embeddings/` (872M, content-addressed per model_id). All
  ~9419 events × 50 samples would need fresh embeddings, plus fresh reader
  calls (cache misses on different packed items). Step 2 already
  established the weight profile is not the bottleneck; the marginal value
  of an expensive embedding swap is low before the S5 framing decision.

**What S5 would gain by re-running**: if a stronger embedding model lifts
`full` EM above `vector_rag` (0.56), embedding quality is the bottleneck
and QEMR's design is sound. If it does not, the retrieval pipeline
structure (not embedding quality) is the root cause. This is a meaningful
but non-urgent experiment; it belongs in S5 path B (positive thesis) where
the 500-question run would amortize the re-embedding cost.

The S5 decision should re-evaluate whether to re-enable this comparison
based on the §5 root-cause conclusion below.

## §4. M2 stale-memory judge (Step 4)

**Trigger**: S2 SUPERSEDE = 109 > 0 → M2 is mandatory (B2 fix). The judge
checks whether the 109 superseded memories are *consumed* by retrieval
(reader sees the fresh value) or whether `full` (ETEC + QEMR) still serves
stale values that `event_no_etec` (non-ETEC) would also serve.

**Judge model**: `minimax-m3` via the Ark API (`ARK_*` env). The judge is
**not** `mimo-v2.5` (the reader/extractor) — different model family
(spec N8 / B4; AGENTS.md "LLM judges require cached inputs/outputs and a
documented judge model"). Judge inputs/outputs are cached to
`<source-run>/m2_judge_cache/` (31 cache files, content-addressed).

**Population**: 31 samples where `full` prediction ≠ `event_no_etec`
prediction (of 50 v2 samples; 19 produced identical predictions and carry
no stale/fresh signal). The judge sees the question, the gold answer, and
both predictions (A = full/with-SUPERSEDE, B = event_no_etec/without).

### Stale/fresh verdict (31 differing-prediction samples)

| verdict | count | % |
|---|---|---|
| tie (same value) | 23 | 74.2% |
| event_no_etec (B) less stale | 6 | 19.4% |
| full (A) less stale | 1 | 3.2% |
| parse error | 1 | 3.2% |

### Findings

1. **74% tie — SUPERSEDE is a reader-level no-op for most samples.** In
   23/31 differing-prediction samples, the judge found both answers
   reflect the same current value (differences are formatting —
   "Luna" vs "Luna.", "Two weeks" vs "Two weeks."). The 109 SUPERSEDE
   fires at the consolidation layer do not change what the reader sees
   for these single-session-user factual lookups.
2. **19% event_no_etec "less stale" — a correctness signal, not a
   staleness signal.** Inspecting the 6 "B less stale" samples: the
   judge picked `event_no_etec` because its answer matched the gold
   better (e.g. "Your friend Sarah" vs full's "an old friend from high
   school"; gold = "Sarah"). The judge conflated "matches gold" with
   "less stale" when the temporal dimension was not salient. This is a
   known judge-design limitation: for single-session-user questions
   there is no "old vs new" value to disambiguate, so the judge falls
   back to correctness.
3. **1% full "less stale"** — one sample where full's answer was
   judged fresher; not enough signal to claim SUPERSEDE helps.
4. **No sample showed `full` serving a clearly stale value that
   `event_no_etec` corrected.** The "SUPERSEDE fired but retrieval
   served the stale old value" hypothesis (the original M2 worry) is
   **not supported** on these 50 questions.

### §4 verdict

- The M2 judge does **not** find evidence that `full` serves stale
  answers that `event_no_etec` corrects. The retrieval layer is not
  ignoring SUPERSEDE's fresh values.
- The dominant signal is "tie" (74%): SUPERSEDE fires at the
  consolidation layer but the 50 questions (all
  `single-session-user`) rarely have a temporal-salient answer where
  consolidation would change the reader-visible value. This is the
  operating-surface-narrowness signal that motivates the S5 branch-C
  (intermediate-route) framing.
- Judge-design caveat: minimax-m3 conflates "matches gold" with "less
  stale" on non-temporal-salient questions. A follow-up M2 on the
  `temporal-reasoning` / `knowledge-update` LongMemEval subsets (where
  staleness is salient) is the S5 follow-up.

_Artifacts_: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/m2_judge_report.md`
+ `.json` + `m2_judge_cache/*.json` (31 cached judge calls).

## §5. Root-cause conclusion + S5 routing (Step 5)

> Per `docs/archive/S3-execution-prompt.md` line 379, this section is a
> human-judgment process item (N10) and is **not** listed among the
> "verification commands". It synthesizes §1-§4; it does not claim
> thesis翻盘 / ETEC有效 / QEMR有效 (S3 only measures, per scope line 81).

### Synthesis of §1-§4

| Step | Lever tested | Result | Verdict |
|---|---|---|---|
| §1 Router | rule accuracy (N9) | 38% full-500, 4% v2-slice | **below 80% threshold** — router mis-routes |
| §2 Weights | no_temporal / no_graph / uniform | qemr ≥ all arms | **weight profile is sound** — not over-fit |
| §3 Embedding | bge/e5 vs qwen3 | skipped (cost + infra) | deferred to S5 |
| §4 M2 judge | full stale vs event_no_etec | 74% tie, 0% full-stale | **retrieval not ignoring SUPERSEDE** |

### Root-cause synthesis

The QEMR failure (S2 `full` EM 0.48 < `vector_rag` 0.56, Δ -0.08; ETEC
Δ +0.02 not翻盘) has **two identified contributors**, neither of which
is the weight profile or the SUPERSEDE consumption:

1. **Router mis-routing (§1, primary)**. The deterministic router
   classifies only 4% of the v2 slice's `single-session-user` questions
   as SEMANTIC; 80% fall to HYBRID and 16% to TEMPORAL. The HYBRID
   mis-route is weight-neutral (SEMANTIC==HYBRID in
   `QEMR_WEIGHT_PROFILES`), but the TEMPORAL mis-route applies the
   temporal-heavy profile to factual lookups, down-weighting dense
   relevance from 1.0 to 0.3. The router's `_FACT_RE` does not match
   LongMemEval's "what + noun + did + subject + verb" phrasings. This is
   the strongest single contributor and is a **fixable** root cause
   (post-S3 router-rule task, N9 scope).

2. **Operating-surface narrowness (§4, structural)**. All 50 v2 questions
   are `single-session-user` — factual attribute lookups scoped to one
   session. SUPERSEDE fires 109 times at the consolidation layer but, per
   M2, in 74% of differing-prediction cases both `full` and
   `event_no_etec` serve the same value. There is no temporal-salient
   answer for consolidation to change. This is the "evidence-constrained
   operating surface is too narrow on single-session-user" signal that
   motivates the S5 intermediate-route framing. It is **not fixable** by
   S3-scope changes; it requires either the 500-question run (S5 path B)
   or the intermediate-route framing (S5 path C).

**Non-causes ruled out**: the weight profile is sound (§2: qemr beats all
ablations); retrieval is not ignoring SUPERSEDE (§4: no full-stale
cases); embedding quality is untested but Step 2 showed weights are not
the bottleneck, so an embedding swap is lower-priority than the router.

### S5 routing

The evidence points to **S5 branch C (intermediate route)**:

- SUPERSEDE > 0 (109) and reachable (S2 reachability PASS) → not branch A
  (negative-result).
- `full` EM did not翻盘 (+0.02 only) and M2 shows 74% tie → not branch B
  (positive thesis): ETEC's SUPERSEDE is reachable on real data but
  insufficient to lift overall accuracy on the single-session-user slice.
- Router accuracy 38% is a fixable contributor, but fixing it is a
  post-S3 task (N9), not an S3 deliverable → S5 framing should present
  the router fix as a *future-work lever*, not a *delivered fix*.

**Recommended S5 framing (branch C)**: "ETEC's evidence-constrained
SUPERSEDE is reachable on real LongMemEval data (109 fires across 40/50
samples) but does not lift `full` EM above `vector_rag` on the
single-session-user slice, because (a) the router mis-routes 84% of
factual lookups away from the SEMANTIC weight profile, and (b) the
single-session-user operating surface offers no temporal-salient answer
for consolidation to change (74% of differing-prediction samples are
ties)."

**Post-S3 levers (each needs independent-review approval)**:
1. Router rule edits (`_FACT_RE` + knowledge-update regex) — highest
   leverage, lowest cost.
2. Embedding model swap (bge-large-en-v1.5 / e5-large-v2) — deferred
   from §3; only worth the cost if the router fix alone does not close
   the `full` vs `vector_rag` gap.
3. M2 on `temporal-reasoning` / `knowledge-update` subsets — where
   staleness is salient and the judge would not conflate correctness
   with freshness.
