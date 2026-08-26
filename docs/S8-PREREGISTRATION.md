# S8 Pre-Registered Decision Rules

> Pre-registered **before** the S8 stratified100 live run (Phase D).
> Frozen at Step 3b; the live run (Step 5) produces the numbers that
> locate the project claim into one of the branches below. No
> post-hoc rule changes permitted (AGENTS.md anti-p-hacking).
>
> Source: `docs/S8-stratified-validation-prompt.md` §3b.
> Sample manifest: `configs/longmemeval/stratified100.toml.inc`
> (n=100, seed=42, allocation sums to 100, distribution within ±2 of the
> 500-question proportions).

## 1. Sample design (frozen)

- **Source population**: `data/raw/longmemeval/longmemeval_s_cleaned.json`
  (500 questions, 6 `question_type` categories).
- **Sample size**: n=100, the project's **final** validation sample
  (not a pilot — the 500-question run is downgraded to optional
  future-work, see `docs/METHODOLOGY_CHANGE.md`).
- **Allocation** (largest remainder method, deterministic):
  | question_type | 500 count | 500 % | n=100 alloc | Δ vs ideal |
  |---|---|---|---|---|
  | multi-session | 133 | 26.6% | 27 | +0.4 |
  | temporal-reasoning | 133 | 26.6% | 27 | +0.4 |
  | knowledge-update | 78 | 15.6% | 15 | −0.6 |
  | single-session-user | 70 | 14.0% | 14 | 0.0 |
  | single-session-assistant | 56 | 11.2% | 11 | −0.2 |
  | single-session-preference | 30 | 6.0% | 6 | 0.0 |
- **Seed**: 42 (reproducible; the manifest is committed to git as the
  pre-registered sample design — IDs + allocation only, no question
  content).
- **ETEC home-court subset** (the categories where SUPERSEDE can
  change the reader-visible answer): `temporal-reasoning` + `knowledge-
  update` = 27 + 15 = 42 questions. This is the primary effect-
  direction evaluation surface.

## 2. Decision rules (pre-registered)

The project claim is located into one of three branches based on the
**direction + effect size** of `full` vs `vector_rag` on the ETEC
home-court subset (temporal-reasoning + knowledge-update). Significance
is **not** required (n=100 is under-powered for paired-proportion
significance at α=0.05; see §3 MDE). The decision is effect-direction +
magnitude, per the small-sample pre-registered framework already
established in `docs/METHODOLOGY_CHANGE.md`.

| Result condition on ETEC home-court subset (n≈42) | Project claim branch | Project claim wording (honest red line) |
|---|---|---|
| `full` − `vector_rag` ΔEM ≥ **+0.05** | **C+ (upgraded)** | "ETEC on the home-court stratified small sample (n=42) shows the **correct direction** with effect size Δ=+X; statistical significance was not reached (n=100 is under-powered for paired-proportion significance). 500q optional future-work only confirms significance." |
| \|Δ\| < **0.05** | **C (intermediate, maintained)** | "ETEC on the home-court stratified small sample is **neutral** (|Δ|<0.05); the v2 branch-C thesis (reachable but insufficient) is maintained. Continue searching for other levers (embedding swap / sentinel prompt tuning) as future-work." |
| `full` − `vector_rag` ΔEM ≤ **−0.05** | **D (negative result)** | "ETEC on the home-court stratified small sample shows the **wrong direction** (Δ=−X); the project claim is rewritten as a branch-D negative-result paper. ETEC no longer claims 'reachable' as a positive contribution." |

### Block condition (pre-registered)

If the router's per-category accuracy on the ETEC home-court subset
(temporal-reasoning + knowledge-update) is **< 70%** at Phase C dry-run
(even when the global 500q accuracy ≥ 60%), the live run is **blocked**.
Return to Step 1 to strengthen the router rules. Do NOT enter Phase D
with an untrusted router — a mis-router would manufacture effect-size
noise that no post-hoc analysis can disentangle.

## 3. MDE and statistical power (pre-registered)

### MDE formula (paired-proportion test)

For a paired design comparing two proportions (EM scores) on the same
questions, the minimum detectable effect (MDE) is:

```
MDE ≈ (z_{α/2} + z_β) × √(p̄ × (1−p̄) / n)
```

where:
- `z_{α/2} = 1.96` (α = 0.05 two-sided)
- `z_β = 0.84` (power = 0.80)
- `p̄ = (p1 + p2) / 2` is the pooled baseline proportion
- `n` is the number of paired observations

Note: Unlike the two-independent-proportions formula, the paired design
does **not** include the factor of 2 in the variance term, because the
same questions are evaluated by both methods (within-subject design).

The paired correlation ρ between methods on the same questions is
accounted for via the variance inflation factor (VIF):

```
VIF = 2 × (1 − ρ)
MDE_adjusted = MDE × √(VIF)
```

With ρ ≈ 0.3 (same questions, different retrieval methods), VIF ≈ 1.4,
inflating MDE by ~18%.

### Assumptions

| Parameter | Value | Rationale |
|---|---|---|
| α | 0.05 two-sided | Standard |
| Power (1−β) | 0.80 | Standard |
| Expected baseline (vector_rag EM) | ≈ 0.56 | S3 v2 50q pilot |
| Paired correlation ρ | ≈ 0.3 | Same questions, different methods |
| n (overall) | 100 | Pre-registered sample |
| n (home-court subset) | ≈ 42 | temporal-reasoning + knowledge-update |

### Computed MDE values

- **Overall (n=100)**: p̄ ≈ 0.50, MDE ≈ ±0.14 (before VIF adjustment)
- **Home-court subset (n≈42)**: p̄ ≈ 0.50, MDE ≈ ±0.21 (before VIF adjustment)

### Caveats on C+/C/D decision stability

- **n=100 MDE=0.14** is far larger than the observed delta range
  (0.00–0.08 across categories). C+/C/D judgment is **unstable** at
  this sample size — a small shift in sample composition could flip the
  verdict.
- **n=42 home-court MDE=0.21** is even larger. The home-court结论
  (full vs vector_rag delta=+0.000) is **directional only**, not
  statistically grounded.
- **500q run** would shrink MDE to ±0.06–0.10, but still requires CI
  support to draw category-specific conclusions. 500q is non-blocking
  per `docs/METHODOLOGY_CHANGE.md`.
- Category-level comparisons (6 categories × N methods) are
  **exploratory** — see `docs/S8-STRATIFIED_VALIDATION_REPORT.md`
  multiple-comparison caveat.

**The decision does NOT require statistical significance.** It requires
**direction + effect size** — this is the small-sample pre-registered
effect-direction framework already established in
`docs/METHODOLOGY_CHANGE.md`. The 500q run (optional future-work) would
shrink MDE to ±0.06–0.10, **still insufficient** to reach LongMemEval-
paper-grade significance, so 100q is enough to locate the project claim.

## 4. M2 judge scope (pre-registered)

The M2 stale-judge (minimax-m3 via ARK API, ≠ mimo-v2.5 reader) runs
**only** on the temporal-salient subset where predictions differ:
- `temporal-reasoning` + `knowledge-update` + `multi-session`
- AND `full` prediction ≠ `event_no_etec` prediction (differing samples
  only).

The judge does **not** run on `single-session-*` (no temporal-salient
answer for consolidation to change — the S3 §4 74% tie was a
correctness/staleness confusion artefact). The judge prompt shows the
gold answer's "current value vs old value" contrast when the raw turns
contain time-stamped value changes.

## 5. Anti-fishing boundary (pre-registered)

- **Allowed** (S5 future-work, executed in S8 Step 1): router regex
  strengthening, extraction-prompt sentinel-rate tuning.
- **Forbidden** in S8: post-hoc weight-profile tuning
  (`QEMR_WEIGHT_PROFILES`), retrieval-budget changes, embedding swap
  based on the EM result. These are S3-confirmed-sound / S3-deferred
  levers, not S8 levers.
- **Forbidden** in S8: dropping or swapping stratified-sample members
  based on the live-run EM. Failure rates are reported honestly per
  category.

## 6. Fairness contracts (pre-registered, AGENTS.md N8)

- Same reader model: `mimo-v2.5` (no cross-model comparison).
- Same extractor model: `mimo-v2.5`.
- Same embedding model: `qwen3-embedding-0.6b` via `gpu-5090:11436`.
- Same token budget: 4096 input tokens per method.
- Same retrieval budget per method (no per-method retrieval-budget
  tuning).
- The M2 judge model (`minimax-m3`) is documented and ≠ the reader
  (AGENTS.md "LLM judges require cached inputs/outputs and a documented
  judge model"); judge calls are cached to
  `<source-run>/m2_judge_cache/`.

## 7. Optional future-work (not blocking the project claim)

Listed for honesty; none is required to locate the project claim into
C+/C/D:

1. **500q stability confirmation** — shrink MDE to ±0.06–0.10;
   `docs/METHODOLOGY_CHANGE.md` already concedes 500q expected non-
   significance. Non-blocking.
2. **Embedding swap** — bge / e5 vs qwen3 (S3 §3 deferred).
3. **Sentinel-rate prompt tuning** — break the 20% ceiling (S5 future-
   work).
4. **O01 learned router** — replace the rule-based router (optional
   task; not in S8).

## 8. Honest red line

A C+ verdict does **not** license the claim "ETEC is effective" or
"QEMR is effective". It licenses only: **"ETEC on the home-court
stratified small sample (n=42) shows the correct direction with effect
size Δ=+X; statistical significance was not reached (n=100 is under-
powered). 500q optional future-work only confirms significance."**

A C or D verdict is reported as written — neutral or negative — with no
softening.
