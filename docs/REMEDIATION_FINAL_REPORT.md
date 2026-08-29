# Remediation Final Report (S5, Branch C — Intermediate Route)

> Stage 5 finalization. Built on S0–S3 (`docs/archive/REMEDIATION_SPEC.md`). This report
> carries **no pre-declared expectation** (negative-result framework,
> `docs/archive/METHODOLOGY_CHANGE.md`) and **does not claim** thesis翻盘 / ETEC有效 /
> QEMR有效. Branch C is the intermediate route: SUPERSEDE is reachable on real
> data but insufficient to lift overall accuracy.
>
> Source v2 run: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`
> (FINALIZED, git `17b1014`). S3 diagnosis: `docs/QEMR_FAILURE_DIAGNOSIS.md`.
> S4a reproducibility: `configs/longmemeval/offline10.toml` + `.env.example`.

## 1. Executive summary

**Thesis (branch C, intermediate route)**: ETEC's evidence-constrained
SUPERSEDE is **reachable on real LongMemEval data** (109 fires across 40/50
samples, first time on real data; reachability test PASS, not XFAIL) but
**insufficient to lift overall `full` EM above `vector_rag`** (0.48 vs 0.56,
Δ −0.08). Two identified contributors (neither is the QEMR weight profile nor
the SUPERSEDE consumption):

1. **Router mis-routing (primary, fixable, future work)** — the deterministic
   router classifies only 4% of the v2 slice's `single-session-user` questions
   as SEMANTIC; 80% fall to HYBRID (weight-neutral) and 16% to TEMPORAL
   (down-weights dense relevance from 1.0 to 0.3). Full-500 accuracy = 38% <
   80% N9 threshold. The router's `_FACT_RE` does not match LongMemEval's
   "what + noun + did + subject + verb" phrasings.
2. **Operating-surface narrowness (structural, not fixable in S5)** — all 50 v2
   questions are `single-session-user` (factual lookups scoped to one session).
   Per M2, 74% of differing-prediction samples are reader-level ties: both
   `full` and `event_no_etec` serve the same current value. There is no
   temporal-salient answer for consolidation to change.

**Honest nuance on v1→v2**: the `full` vs `event_no_etec` gap closed from
−0.08 (v1, ETEC harmful) to 0.00 (v2, ETEC neutral), but the closure was
**driven by `event_no_etec` dropping 0.06 (0.54→0.48), not by `full` rising
(+0.02 only, 0.46→0.48)**. The v3 required-fact-slot prompt made ETEC
*neutral*, not *beneficial*; the absolute flagship EM (0.48) still trails
`vector_rag` (0.56).

**Positive contributions (infrastructure, not accuracy)**: 100% provenance
coverage infrastructure; 33/33 failure attribution; tamper-proof
`FINALIZED.json` runs; mechanism-level root-cause diagnosis (router / weights /
M2 three-layer localization in S3).

## 2. v1 vs v2 EM comparison (same model: mimo-v2.5, same 4096 budget)

Numbers traceable to:
- v1: `runs/publication/m13-longmemeval-test50-mimo/summary.json` (FINALIZED, git `e585d7e`)
- v2: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json` (FINALIZED, git `17b1014`)

| method         | v1 EM | v2 EM | Δ     |
|----------------|-------|-------|------|
| no_memory      | 0.00  | 0.00  | +0.00 |
| full_context   | 0.00  | 0.00  | +0.00 |
| vector_rag     | 0.56  | 0.56  | +0.00 |
| event_no_etec  | 0.54  | 0.48  | -0.06 |
| etec           | 0.52  | 0.46  | -0.06 |
| full (flagship)| 0.46  | 0.48  | +0.02 |

_No pre-declared expectation. Same model (mimo-v2.5) reader+extractor, same
4096-token budget, same embedding (qwen3-embedding-0.6b). v1 vs v2 EM is
directly comparable. Cross-model comparison against the 24-question
deepseek-v4-flash run is forbidden (AGENTS.md N8; the deepseek run is
out-of-service and not reproducible)._

**Read**:
- `full` improved +0.02 EM (0.46 → 0.48) — a slight improvement, not a翻盘.
- `event_no_etec` and `etec` both dropped 0.06.
- The `full` vs `event_no_etec` gap closed from −0.08 (v1, ETEC harmful) to
  0.00 (v2, ETEC neutral). But the closure was driven by `event_no_etec`
  dropping, not by `full` rising.
- Absolute `full` EM (0.48) still trails `vector_rag` (0.56) by 0.08.

## 3. ETEC reachability diagnosis (v2 S2 measurement)

**Source**: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`
(extraction snapshots, consolidation.jsonl, summary.json).

- **SUPERSEDE = 109 across 40/50 samples** — first time SUPERSEDE fires on
  real data (v1 baseline had SUPERSEDE = 0; the v1 v2-prompt extraction did
  not produce `fact_slot` so the point-interval overlap gate never matched).

| action    | v2 count |
|-----------|----------|
| ADD       | 7,188    |
| MERGE     | 1,770    |
| REJECT    | 352      |
| SUPERSEDE | 109      |

- **fact_slot / valid_from / sentinel (v3 prompt, n=50, 9419 events)**:
  - fact_slot effective rate (excl. sentinel) = 66.8% (6295/9419) — ≥ 50% floor ✅
  - valid_from non-empty rate = 66.8% (6294/9419) — ≥ 50% floor ✅
  - sentinel rate ("none") = 33.2% (3124/9419) — ⚠️ ≥ 20% ceiling (known
    weakness, not re-tuned in S2/S3/S5 per AGENTS.md anti-fishing rule; see
    Limitations §6.2)

- **Four-gate reachability test**: PASS (not XFAIL). At least one
  within-sample pair on the v2 snapshot satisfies all four SUPERSEDE gates
  (`not multi_valued` AND `_same_fact_slot` AND `not _same_fact_value` AND
  `_intervals_overlap`). The SUPERSEDE logical path is reachable on real
  data; the v1 unreachability was an extraction-pipeline metadata gap
  (R1+R1b cascade), not a consolidation-logic bug.

- **Replay/online consistency**: the 109 SUPERSEDE actions are byte-identical
  between the deterministic replay and the original online run. 2/50 samples
  show minor ADD↔MERGE reclassification (known limitation, documented in
  `tests/mechanism/test_replay.py`; does not affect SUPERSEDE counts).

## 4. QEMR root-cause diagnosis (S3 §1–§4)

Source: `docs/QEMR_FAILURE_DIAGNOSIS.md` (§1 router, §2 weights, §3 embedding
skipped, §4 M2). All S3 artifacts gitignored under `runs/`; the diagnosis
report is committed.

| Step | Lever tested | Result | Verdict |
|---|---|---|---|
| §1 Router | rule accuracy (N9) | 38% full-500, 4% v2-slice | **below 80% threshold** — router mis-routes |
| §2 Weights | no_temporal / no_graph / uniform | qemr ≥ all arms | **weight profile is sound** — not over-fit |
| §3 Embedding | bge/e5 vs qwen3 | skipped (cost + infra) | deferred to S5 future work |
| §4 M2 judge | full stale vs event_no_etec | 74% tie, 0% full-stale | **retrieval not ignoring SUPERSEDE** |

### §1 Router (primary, fixable, future work)

- 50-question slice (matches v2 run): accuracy 4.0% (2/50). All 50 are
  `single-session-user` (gold = SEMANTIC). Predicted: 40 → HYBRID,
  8 → TEMPORAL, 2 → SEMANTIC.
- Full 500-question supplement (deterministic router only, no LLM/benchmark):
  accuracy 38.0% (190/500) — well below the N9 80% threshold.
- Weight-profile nuance: `QEMR_WEIGHT_PROFILES` assigns identical weights to
  SEMANTIC and HYBRID (`{DENSE: 1.0, GRAPH: 0.3, TEMPORAL: 0.2, EPISODIC: 0.1}`),
  so the dominant SEMANTIC→HYBRID mis-route (40/50) is weight-neutral. The
  weight-altering mis-route is SEMANTIC→TEMPORAL (8/50 = 16%), which applies
  the temporal-heavy profile to factual lookups.
- **Scope boundary (N9)**: `src/evoeventmem/router.py` is untouched in S3/S5;
  rule edits are listed as future work (`_FACT_RE` strengthening + a
  `knowledge-update` regex).
- Artifact: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/router_diagnosis_report.md`

### §2 Weights (sound, not the root cause)

| arm | strategy | EM | scored | failed | n |
|---|---|---|---|---|---|
| v2 full (baseline) | qemr | 0.4800 | 50 | 0 | 50 |
| no_temporal | qemr_no_temporal | 0.4600 | 50 | 0 | 50 |
| no_graph | qemr_no_graph | 0.4800 | 50 | 0 | 50 |
| uniform | qemr_uniform | 0.4200 | 50 | 0 | 50 |

Same reader (mimo-v2.5), same 4096 budget, same embedding
(qwen3-embedding-0.6b); only the QEMR weight profile differs
(AGENTS.md anti-mixed-methods). The `strategy` field on every
`QEMRRetrievalResult` makes each arm observable (no silent fallback).

- `qemr` (0.48) ≥ all ablations — the production weight profile is the best
  (or tied) on the 50-question slice. Not over-fit.
- `qemr_no_temporal` (0.46) < `qemr` (0.48), Δ −0.02 — temporal source mildly
  helpful (opposite of the LoCoMo §9 finding `no_temporal` 0.3654 > `qemr`
  0.3000; cross-dataset difference).
- `qemr_no_graph` (0.48) = `qemr` (0.48), Δ 0.00 — graph weight-neutral on
  single-session-user slice (not evidence graph is useless in general).
- `qemr_uniform` (0.42) < `qemr` (0.48), Δ −0.06 — query-adaptive design buys
  +0.06 EM on this slice; not over-engineered.
- Production `QEMR_WEIGHT_PROFILES` is **not** modified in S3/S5.
- Artifact: `runs/publication/m13-longmemeval-test50-mimo-v2-ablation/`
  (`ablation_summary.json` + `ablation_<arm>.json` × 3 + `ablation_report.md`)

### §3 Embedding (skipped, deferred to future work)

Skipped in S3 for two documented reasons: (1) infrastructure — the GPU
embedding server is configured only for `qwen3-embedding-0.6b`, a swap requires
downloading weights, reconfiguring, rebuilding the SSH tunnel; (2) cost — a
different embedding model invalidates the entire content-addressed
`model_cache/embeddings/` and would require fresh embeddings + fresh reader
calls. Step 2 already established the weight profile is not the bottleneck, so
the marginal value of an expensive embedding swap is low before the S5 framing
decision. S5 routes the embedding swap to future work (below).

### §4 M2 stale-memory judge (retrieval not ignoring SUPERSEDE)

- **Judge model**: `minimax-m3` via the Ark API (`ARK_*` env), **explicitly ≠**
  the reader/extractor `mimo-v2.5` (AGENTS.md "LLM judges require cached
  inputs/outputs and a documented judge model"; spec N8/B4). 31 cached judge
  calls under `<source-run>/m2_judge_cache/` (content-addressed).
- **Population**: 31 samples where `full` prediction ≠ `event_no_etec`
  prediction (of 50; 19 produced identical predictions and carry no
  stale/fresh signal).
- **Stale/fresh verdict**:

| verdict | count | % |
|---|---|---|
| tie (same value) | 23 | 74.2% |
| event_no_etec (B) less stale | 6 | 19.4% |
| full (A) less stale | 1 | 3.2% |
| parse error | 1 | 3.2% |

- **74% tie — SUPERSEDE is a reader-level no-op for most samples.** In 23/31
  differing-prediction samples, both answers reflect the same current value
  (differences are formatting — "Luna" vs "Luna.", "Two weeks" vs "Two
  weeks."). The 109 SUPERSEDE fires at the consolidation layer do not change
  what the reader sees for these single-session-user factual lookups.
- **19% event_no_etec "less stale" — a correctness signal, not staleness.**
  Inspecting the 6 "B less stale" samples: the judge picked
  `event_no_etec` because its answer matched the gold better. The judge
  conflated "matches gold" with "less stale" when the temporal dimension was
  not salient. Known judge-design limitation (see Limitations §6.5).
- **No sample showed `full` serving a clearly stale value that
  `event_no_etec` corrected.** The "SUPERSEDE fired but retrieval served the
  stale old value" hypothesis (the original M2 worry) is **not supported** on
  these 50 questions. Retrieval is not ignoring SUPERSEDE's fresh values.
- Artifact: `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/m2_judge_report.md`
  + `.json` + `m2_judge_cache/*.json` (31 cached judge calls)

### §5 Root-cause synthesis

The QEMR failure (S2 `full` EM 0.48 < `vector_rag` 0.56, Δ −0.08; ETEC Δ +0.02
not翻盘) has **two identified contributors**, neither of which is the weight
profile or the SUPERSEDE consumption:

1. **Router mis-routing (§1, primary)** — strongest single contributor,
   fixable (post-S3 router-rule task, N9 scope; future work).
2. **Operating-surface narrowness (§4, structural)** — all 50 v2 questions are
   `single-session-user`; SUPERSEDE fires but in 74% of differing-prediction
   cases both `full` and `event_no_etec` serve the same value. Not fixable by
   S5-scope changes; requires either the 500-question run (path B) or the
   intermediate-route framing (path C, this report).

**Non-causes ruled out**: the weight profile is sound (§2: qemr beats all
ablations); retrieval is not ignoring SUPERSEDE (§4: no full-stale cases);
embedding quality is untested but Step 2 showed weights are not the
bottleneck, so an embedding swap is lower-priority than the router.

## 5. Final thesis positioning (branch C, intermediate route)

**Thesis**: "ETEC's evidence-constrained SUPERSEDE is **reachable on real
LongMemEval data** (109 fires across 40/50 samples, first time on real data;
four-gate reachability PASS) but **insufficient to lift overall `full` EM
above `vector_rag`** on the single-session-user slice (0.48 vs 0.56), because
(a) the router mis-routes 84% of factual lookups away from the SEMANTIC
weight profile (full-500 accuracy 38%, fixable, future work), and (b) the
single-session-user operating surface offers no temporal-salient answer for
consolidation to change (74% of differing-prediction samples are
reader-level ties)."

**This is the most honest result.** It is not branch A (negative-result:
SUPERSEDE structurally unreachable) because SUPERSEDE > 0 and reachable. It
is not branch B (positive thesis: `full`翻盘) because `full` EM did not lift
above `vector_rag`. The pivot is to **auditability**: use SUPERSEDE > 0 to
prove the logical path is reachable, use the accuracy null to prove the
real-data operating surface is too narrow.

**Positive contributions (infrastructure, not accuracy claims)**:

1. **100% provenance coverage infrastructure** — every packed evidence item
   carries `raw_turn_id` with deterministic char-span locators; tamper-proof
   `FINALIZED.json` locks all hashes. (LoCoMo legacy provenance = 0; the new
   pipeline fixed this, not always-on.)
2. **33/33 failure attribution** — manual review of all 33 v1 failures
   localized the dominant cause as `answer_present_reader_wrong` (26/33,
   reader verbose phrasing), not retrieval/extraction/budget failure (7/33).
3. **Tamper-proof FINALIZED.json** — every finalized run carries git commit,
   config hash, dataset hash, model identifier, timestamps; any mutation
   invalidates the marker.
4. **Mechanism-level root-cause diagnosis** — S3's three-layer localization
   (router 38% / weights sound / M2 74% tie) isolates the QEMR failure to
   router rules + operating surface, ruling out weights and SUPERSEDE
   consumption. This is the kind of negative-result diagnosis that
   `docs/archive/NEGATIVE_RESULT_DISCLOSURE.md` and `docs/archive/METHODOLOGY_CHANGE.md`
   pre-registered.

**Framing alignment with literature**:
- **LongMemEval** (arXiv:2410.10813, ICLR 2025): §4 defines
  `single-session-user`; §5.4 reports +6.8%~11.3% from time-aware query
  expansion on *temporal* questions. S3 §1 confirmed the router mis-routes
  the temporal subset, so QEMR's temporal weight is mis-applied on this slice.
- **MemTrace** (arXiv:2606.17328): "evidence 10x retrievable than missing" →
  the project's 100% provenance is a real infrastructure contribution; the
  accuracy null is an honest finding, not a research failure.
- **Filesystem-Based Memory** (arXiv:2607.26637): "no agent converts
  organization into better answers" → cautionary for QEMR's query-adaptive
  design on LongMemEval. S3 §2 confirmed `uniform` 0.42 < `qemr` 0.48
  (organization buys +0.06), but the gain is insufficient to翻盘.
- **Mem0** (arXiv:2504.19413): graph memory +2% → structured gains are
  bounded; consistent with S2 +0.02.
- **LoCoMo §9**: `no_temporal` (0.3654) > `qemr` (0.3000) — S3 §2 on
  LongMemEval is the **opposite** (`no_temporal` 0.46 < `qemr` 0.48);
  cross-dataset difference, documented honestly.

## 6. Limitations

1. **50 questions all `single-session-user`** — the operating surface is
   narrow; results do not extrapolate to `temporal-reasoning` or
   `knowledge-update` subsets where consolidation would have a temporal-salient
   answer to change. The 500-question run (future work) would amortize this.
2. **Sentinel rate 33.2% (≥ 20% ceiling)** — the v3 prompt's known weakness;
   not re-tuned in S2/S3/S5 per AGENTS.md anti-fishing rule. Documented as a
   limitation, not a fix target.
3. **Embedding not compared** — S3 §3 skipped bge-large-en-v1.5 / e5-large-v2
   vs qwen3-embedding-0.6b (cost + infrastructure). Embedding quality is
   therefore untested; future work would re-enable the comparison if the
   router fix alone does not close the `full` vs `vector_rag` gap.
4. **Router accuracy 38% (full-500) / 4% (v2-slice)** — not fixed in S3/S5
   (N9 scope boundary; rule edits require independent-review approval).
   Listed as the highest-leverage future-work lever.
5. **M2 judge conflates correctness with staleness** on non-temporal-salient
   questions — minimax-m3 picked `event_no_etec` as "less stale" in 6/31
   differing-prediction cases because its answer matched the gold better, not
   because it was fresher. Known judge-design limitation; a follow-up M2 on
   the `temporal-reasoning` / `knowledge-update` subsets (where staleness is
   salient) is the S5 follow-up.
6. **50-question single-class router slice is degenerate** — all 50 v2
   questions are `single-session-user` (gold = SEMANTIC), so the router's
   behavior on this slice is one row of the confusion matrix. The full-500
   supplement (3.2% interval filter trigger rate, 38% overall accuracy)
   broadens the picture but is deterministic-only (no benchmark run).
7. **v1→v2 ETEC-neutral closure driven by `event_no_etec` dropping, not
   `full` rising** — the v3 required-fact-slot prompt made ETEC neutral
   (gap −0.08 → 0.00) by lowering the non-ETEC baseline, not by lifting the
   ETEC flagship. This is documented honestly in §2; it is not an ETEC win.
8. **Replay/online minor reclassification** — 2/50 samples show ADD↔MERGE
   reclassification between deterministic replay and online; does not affect
   SUPERSEDE counts but is a known pipeline nondeterminism.

## 7. Future work (post-S5 levers, each needs independent-review approval)

1. **Router rule edits (`_FACT_RE` + `knowledge-update` regex)** — highest
   leverage, lowest cost. Strengthen `_FACT_RE` for LongMemEval phrasings
   ("what + noun + did + subject + verb", "where did subject verb", "how
   many/much did"); add a `knowledge-update` regex ("used to", "now", "has
   changed", "previously", "currently"); review `_TEMPORAL_STRONG_RE`
   false-positives ("last month" in factual lookups). N9 scope.
2. **Embedding model swap (bge-large-en-v1.5 / e5-large-v2)** — deferred from
   S3 §3. Only worth the cost (re-embed ~9419 events × 50 samples + fresh
   reader calls) if the router fix alone does not close the `full` vs
   `vector_rag` gap. If a stronger embedding lifts `full` EM above
   `vector_rag` (0.56), embedding quality is the bottleneck and QEMR's design
   is sound; if not, the retrieval pipeline structure (not embedding quality)
   is the root cause.
3. **M2 on `temporal-reasoning` / `knowledge-update` subsets** — re-run the
   stale-memory judge where staleness is salient; the judge would not
   conflate correctness with freshness on those subsets.
4. **500-question consistency verification** — `configs/longmemeval/main500.toml`
   is checked in; the gateway 429/403 quota block stopped the run. Per
   `docs/archive/METHODOLOGY_CHANGE.md`, the 500-question run is a *stability check*,
   not a significance test (n=500 minimum detectable effect ±0.018–0.039 >
   observed 0.005–0.014; no significance is the expected result). Branch C does
   not require the 500-question run; branch B (positive thesis) would.
5. **Sentinel-rate prompt optimization** — independent task; the v3 prompt's
   33.2% sentinel rate is above the 20% ceiling. Routed to a separate
   prompt-engineering task, not mixed into the QEMR/ETEC line.

## 8. Stage closure

This report closes S5 (branch C). The remediation spec
(`docs/archive/REMEDIATION_SPEC.md`) S0→S5 chain is complete:

- S0 (诚信止血) ✅ commit `b60b38d`
- S1a (schema + prompt v2) ✅ commit `162183c`
- S1b (5q smoke + reachability) ✅ commit `00b3dc6`
- S1c (required fact_slot + v3 prompt) ✅ commit `ab5ba1a` (CONDITIONAL PASS — sentinel 39.7%)
- S4b (vector_rag latency fix) ✅ commit `46b7b38`
- S2 (50q v2 run + diagnosis) ✅ commit `17b1014` (CONDITIONAL PASS — SUPERSEDE=109, full EM +0.02)
- S3 (QEMR diagnosis + M2) ✅ commits `a428e8d`→`8b28a5e` (CONDITIONAL PASS — router 38%, weights sound, M2 74% tie → branch C)
- **S4a (reproducibility config + docs) + S5 (branch C finalization) ← this stage**

Independent review: `docs/archive/STAGE4a5_REVIEW.md`.
