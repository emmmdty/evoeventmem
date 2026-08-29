# Stage 3 Independent Review

> Reviewer: independent subagent (post-implementation review only — no code
> changes made). Scope: verify S3 (`docs/S3-execution-prompt.md`) acceptance
> criteria and scope boundaries against the actual committed artifacts.
>
> Source commits reviewed: `a428e8d` (s3.1 router diagnosis) →
> `c215ff8` (s3.2 weight ablation) → `87009a7` (s3.4 M2 judge + docs §3-§5)
> on top of S2 base `17b1014`.

## 1. Review summary

**CONDITIONAL PASS.** All 15 acceptance criteria from `docs/S3-execution-prompt.md`
(lines 372-389) are met: router diagnosis re-runs deterministically and matches
the report; three weight ablations (not just the required two) ran under the
same reader / budget / embedding; M2 judge is `minimax-m3` ≠ reader
`mimo-v2.5` with 31 cached judge calls; `router.py` is untouched and
`QEMR_WEIGHT_PROFILES` is not mutated; pytest/ruff/mypy/smoke all green; no
new overclaim. The verdict is **conditional** rather than full PASS because of
two documented, non-blocking deviations and three unresolved risks (M2
judge's correctness/staleness conflation on non-temporal-salient questions;
the degenerate single-class 50-question router slice; the IPv4 monkeypatch
diagnostic shim) that should be revisited before S5 finalizes its framing.

## 2. Acceptance criteria checklist

Verification commands were run with `workdir=/home/tjk/myProjects/internship-projects/evoeventmem-starter`
on 2026-08-20. Each criterion below cites the actual command output.

| # | Criterion (S3 prompt lines 372-389) | Status | Evidence |
|---|---|---|---|
| 1 | `benchmarks/mechanism/router_diagnosis.py` exists + runs + produces confusion matrix | ✅ | `uv run python -m benchmarks.mechanism.router_diagnosis --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot --output /tmp/opencode/s3-review-router-recheck.md` → `Router diagnosis report written to /tmp/opencode/s3-review-router-recheck.md`. Diff against committed artifact: `router report stable`. |
| 2 | Router confusion matrix + suggestions written to `docs/QEMR_FAILURE_DIAGNOSIS.md` §1 | ✅ | Report §1 (lines 18-110) contains the 50-question and full-500 confusion matrices, per-class P/R/F1, misclassified samples, and 3 rule-edit suggestions explicitly marked "not applied in S3" (N9). |
| 3 | At least 2 weight ablations ran (`no_temporal`, `uniform`) | ✅ | **Three** ablations ran (exceeds minimum). `ablation_summary.json`: `no_temporal` EM=0.46, `no_graph` EM=0.48, `uniform` EM=0.42 — all with `scored=50, failed=0`. |
| 4 | Embedding comparison done OR declared skipped | ✅ | §3 of report (lines 178-207) explicitly declares "Status: skipped — deferred to S5 (cost + infrastructure)." Reasons documented per prompt fallback lines 425-427. |
| 5 | M2 done with judge≠reader OR declared not-run + auditability weakness | ✅ | M2 ran. `m2_judge_report.json`: `judge_model=minimax-m3`, `reader_model=mimo-v2.5`, `judge_is_reader=false`. 31 cached judge calls under `m2_judge_cache/`. |
| 6 | Diagnosis report has root-cause conclusion (N10, human-judgment, not a verification command) | ✅ | §5 (lines 279-358) synthesizes §1-§4 into two identified contributors (router mis-routing primary; operating-surface narrowness structural) and three ruled-out non-causes, with explicit S5 branch-C routing recommendation. |
| 7 | Full pytest suite green | ✅ | `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q` → `305 passed, 1 skipped, 1 xfailed in 19.91s`. Skip = known replay-cache miss; xfail = S2 sentinel rate 33.2% (pre-existing). |
| 8 | `uv run ruff check .` green | ✅ | `All checks passed!` |
| 9 | `uv run mypy src` green | ✅ | `Success: no issues found in 33 source files` |
| 10 | `uv run python -m evoeventmem.cli smoke` outputs "smoke ok" | ✅ | `smoke ok: The project switched the package registry to npmmirror. score=0.400` |
| 11 | `git diff src/evoeventmem/router.py` empty | ✅ | `git diff 17b1014..HEAD -- src/evoeventmem/router.py` → (no output). N9 scope preserved. |
| 12 | `git diff src/evoeventmem/retrieval.py` only adds ablation entry, `QEMR_WEIGHT_PROFILES` untouched | ✅ | Diff shows only: 3 new `RetrievalStrategy` enum values + 3 `resolve_weights` branches that copy `dict(QEMR_WEIGHT_PROFILES[intent])` then zero one source on the copy. Production dict literal is never rewritten. New `test_s3_ablation_strategies_zero_named_source_without_mutating_production` test verifies non-mutation. |
| 13 | M2 judge ≠ reader (`minimax-m3` ≠ `mimo-v2.5`) | ✅ | See row 5. |
| 14 | Ablation same model same budget (AGENTS.md anti-mixed-methods) | ✅ | `reader_model`: v2=`mimo-v2.5`, ablation=`mimo-v2.5` → same. `max_input_tokens`: v2=4096, ablation=4096 → same. `embedding_model`: v2=`qwen3-embedding-0.6b`, ablation=`qwen3-embedding-0.6b` → same. |
| 15 | No new overclaim ("显著提升" / "thesis 翻盘" / "ETEC 有效" / "QEMR 有效") | ✅ | `rg -n '显著提升\|significant improvement\|outperform\|thesis 翻盘\|ETEC 有效\|QEMR 有效\|thesis翻盘\|ETEC有效\|QEMR有效' docs/QEMR_FAILURE_DIAGNOSIS.md src/` → exactly one match at line 284, which is the report's own disclaimer that it does **not** claim those things ("it does not claim thesis翻盘 / ETEC有效 / QEMR有效"). Meta-statement, not overclaim. |

**Result: 15/15 ✅.**

## 3. Findings

### 3.1 Router diagnosis (Step 1)

Re-running `router_diagnosis.py` produced a byte-identical report
(`diff … && echo "router report stable"`). Numbers re-derived from
`router_diagnosis_report.md`:

- **50-question slice** (matches v2 run): accuracy 4.0% (2/50); all 50 are
  `single-session-user` (gold = SEMANTIC); predicted = 40 HYBRID + 8
  TEMPORAL + 2 SEMANTIC. Confusion matrix and per-class F1 (semantic
  precision 100%, recall 4.0%, F1 7.7%) match `QEMR_FAILURE_DIAGNOSIS.md`
  §1.
- **Full 500-question supplement** (router-only, no LLM — explicitly noted as
  outside the "不跑 500 题" benchmark-run scope guard): accuracy 38.0%
  (190/500), well below the N9 80% threshold. Distribution and per-class
  P/R/F1 match §1.
- The report correctly identifies that the dominant SEMANTIC→HYBRID
  mis-route is weight-neutral (SEMANTIC and HYBRID share the same
  `QEMR_WEIGHT_PROFILES` entry) and that the weight-altering mis-route
  (SEMANTIC→TEMPORAL, 8/50 = 16%) is the actionable signal.

### 3.2 Weight ablation (Step 2)

Re-derived from `runs/publication/m13-longmemeval-test50-mimo-v2-ablation/ablation_summary.json`:

| arm | strategy | EM | scored | failed |
|---|---|---|---|---|
| v2 full (baseline) | qemr | 0.48 | 50 | 0 |
| no_temporal | qemr_no_temporal | 0.46 | 50 | 0 |
| no_graph | qemr_no_graph | 0.48 | 50 | 0 |
| uniform | qemr_uniform | 0.42 | 50 | 0 |

All four numbers in `QEMR_FAILURE_DIAGNOSIS.md` §2 (lines 133-138) match the
artifact byte-for-byte. Same reader (`mimo-v2.5`), same budget (4096), same
embedding (`qwen3-embedding-0.6b`) — verified from `ablation_summary.json`
fields. `qemr` ≥ all ablations; `qemr` beats `uniform` by +0.06; temporal
source contributes +0.02 (mildly helpful, opposite of LoCoMo §9). The §2
verdict ("weight profile is not the failure root cause") is supported.

Note: the v2_full_em=0.48 baseline is taken from the v2 run's
`summary.json`, not re-derived by re-running qemr through the ablation
script. The script's `ARM_STRATEGIES` dict includes an optional `"qemr"`
sanity arm, but only 3 ablation arms were actually run and reported. This is
acceptable because the v2 baseline is already finalized and cached.

### 3.3 Embedding comparison (Step 3)

Skipped per §3 of the diagnosis report. Reasons (GPU embedding server
configured only for `qwen3-embedding-0.6b`; embedding cache invalidation
cost ~9419 events × 50 samples) are documented and routed to S5 path B.
This matches the prompt's Step 3 fallback (lines 425-427).

### 3.4 M2 stale-memory judge (Step 4)

Re-derived from `m2_judge_report.json`:

| verdict | count | % |
|---|---|---|
| tie | 23 | 74.2% |
| event_no_etec (B) "less stale" | 6 | 19.4% |
| full (A) "less stale" | 1 | 3.2% |
| parse error | 1 | 3.2% |

Counts (a=1, b=6, tie=23, parse_error=1) sum to 31 = `n_differing`, matching
the §4 table (lines 230-235). 50 v2 samples − 19 identical predictions = 31
differing-prediction samples, as reported. 31 judge cache files exist under
`m2_judge_cache/` (content-addressed `sha256-*.json`), satisfying AGENTS.md's
"LLM judges require cached inputs/outputs" rule.

The §4 verdict correctly notes that the 6 "B less stale" samples reflect the
judge conflating correctness with staleness (judge picked `event_no_etec`
because its answer matched the gold better, not because it was fresher). The
report explicitly flags this as a known judge-design limitation and proposes
an S5 follow-up M2 on `temporal-reasoning` / `knowledge-update` subsets where
staleness is salient.

### 3.5 Sanity checks

- `uv run pytest tests/mechanism -q` → `68 passed, 1 skipped` (skip = known
  replay cache miss, pre-existing).
- `uv run ruff check .` → `All checks passed!`
- `uv run mypy src` → `Success: no issues found in 33 source files`
- `uv run python -m evoeventmem.cli smoke` → `smoke ok`

### 3.6 Scope (git diff)

`git diff --stat 17b1014..HEAD` shows only the expected files:

```
benchmarks/mechanism/m2_stale_judge.py   | 486 +++
benchmarks/mechanism/router_diagnosis.py | 502 +++
benchmarks/mechanism/weight_ablation.py | 496 +++
docs/QEMR_FAILURE_DIAGNOSIS.md           | 358 +++
docs/S3-execution-prompt.md              | 538 +++
src/evoeventmem/retrieval.py             |  28 ++
tests/mechanism/test_m2_stale_judge.py   |  55 ++
tests/mechanism/test_router_diagnosis.py|  83 ++
tests/retrieval/test_qemr.py             |  29 ++
```

No `router.py`, no `consolidation.py`, no `prompts/`, no `configs/longmemeval/`
changes. Working tree clean (`git status --short` empty).

## 4. Scope-boundary check

| Boundary (S3 prompt) | Status | Evidence |
|---|---|---|
| `src/evoeventmem/router.py` untouched (N9) | ✅ | `git diff 17b1014..HEAD -- src/evoeventmem/router.py` → empty |
| Production `QEMR_WEIGHT_PROFILES` dict literal untouched | ✅ | `retrieval.py` diff has no `-[...]` line under the `QEMR_WEIGHT_PROFILES =` literal. Ablation code does `dict(QEMR_WEIGHT_PROFILES[intent])` (copy) then mutates the copy. New test `test_s3_ablation_strategies_zero_named_source_without_mutating_production` verifies production dict intact post-call. |
| R3 (`multi_valued` short-circuit) untouched | ✅ | `consolidation.py` not in `git diff --stat` |
| v3 prompt untouched | ✅ | No `prompts/` or `*.md` prompt file in `git diff --stat` |
| No new overclaim in `docs/QEMR_FAILURE_DIAGNOSIS.md` or `src/` | ✅ | See criterion 15 above |
| No silent fallback in ablation (AGENTS.md) | ✅ | Each `QEMRRetrievalResult` carries a `strategy` field; `ablation_summary.json` records `strategy` per arm (`qemr_no_temporal` / `qemr_no_graph` / `qemr_uniform`) |
| No production path broken | ✅ | Full regression `305 passed, 1 skipped, 1 xfailed` (the xfail is the pre-existing S2 sentinel-rate expectation) |
| No mixed-model / mixed-budget EM comparison (AGENTS.md) | ✅ | Ablation uses same reader + same budget + same embedding as v2 (criterion 14) |
| No datasets / secrets / model weights / benchmark caches committed | ✅ | `runs/` is gitignored; only source + tests + docs in `git diff --stat` |
| Evidence provenance preserved | ✅ | All run artifacts retain `source_run`, `reader_model`, `judge_model`, `embedding_model`, `max_input_tokens` fields |

## 5. Deviations from the prompt

| # | Deviation | Acceptable? | Rationale |
|---|---|---|---|
| 1 | **Steps 4 + 5 combined into one commit (`87009a7`)** — prompt line 74 says "每个步骤单独 commit" with step 4 = `feat(s3.4): M2 stale-memory judge` and step 5 = `docs(s3): QEMR_FAILURE_DIAGNOSIS report + final routing` as separate commits. | ✅ Acceptable | The single commit message clearly indicates both (`feat(s3.4): M2 stale-memory judge … ; docs(s3): diagnosis report §3-§5 + root-cause + S5 routing`). Both deliverables are independently verifiable (M2 artifacts in `m2_judge_*.{json,md}` + cache; report text in `QEMR_FAILURE_DIAGNOSIS.md` §3-§5). Does not obscure review. |
| 2 | **Step 3 (embedding comparison) skipped** — prompt allowed this fallback (lines 425-427: "如果 embedding 对照实验成本超预算 → 显式声明"). | ✅ Acceptable | Explicit declaration in §3 with reasons (GPU server config; cache invalidation cost). S5 decision re-evaluation noted. Matches the prompt's documented fallback path. |
| 3 | **Standalone ablation script (`benchmarks/mechanism/weight_ablation.py`) instead of `configs/longmemeval/test50-mimo-v2-ablation.toml` + `benchmarks.longmemeval.run`** — prompt lines 230-238 suggested creating a config file and running through the existing benchmark pipeline. No `configs/longmemeval/*ablation*` file exists (glob confirmed). | ✅ Acceptable | The standalone script reuses the same `ExtractionSnapshot` / `RetrievalHarness` / `FileModelCache` infrastructure (verified via `weight_ablation.py` imports). Same-model / same-budget / same-embedding invariants are enforced by reading `reader_model` / `max_input_tokens` / `embedding_model` from the v2 `summary.json` and stamping them onto `ablation_summary.json`. The composite cache (`_CompositeFileModelCache`) reuses v2's reader cache read-only and writes only to the ablation dir, so the v2 run dir is never mutated. The deviation is mechanical (script vs config+run.py), not semantic. |
| 4 | **IPv4 `getaddrinfo` monkeypatch in `weight_ablation.py:70-84`** — diagnostic-only network shim that filters DNS resolution to AF_INET to work around intermittent IPv6 connection resets on the opencode.ai endpoint. | ✅ Acceptable (with risk flag) | The shim is contained to the diagnostic script (`benchmarks/mechanism/weight_ablation.py`); `src/evoeventmem/` is untouched (confirmed by `git diff --stat` and inline comment at line 76: "production code (src/evoeventmem) is untouched"). The shim does not affect retrieval determinism (retrieval is cache-fed; only the reader LLM call goes to the network). However, the underlying IPv6/connection-reset flakiness is an infrastructure risk (see §6). |
| 5 | **M2 judge conflates correctness with staleness** — on 6/31 samples the judge picked `event_no_etec` as "less stale" because its answer matched the gold better, not because it was temporally fresher. | ✅ Acceptable (with risk flag) | The diagnosis report explicitly acknowledges this in §4 verdict point 2 (lines 244-252) and §5 post-S3 levers #3 (lines 356-358), proposing a follow-up M2 on `temporal-reasoning` / `knowledge-update` subsets where staleness is salient. The honesty of the self-assessment is appropriate; the limitation does not invalidate the negative M2 finding (no full-stale samples) because the 74% tie rate is the dominant signal and is robust to the conflation. |

## 6. Unresolved risks

1. **M2 judge correctness/staleness conflation.** On 6/31 differing-prediction
   samples, `minimax-m3` picked `event_no_etec` as "less stale" because its
   answer matched the gold better — a correctness signal mislabelled as a
   staleness signal. This is structural: the 50-question v2 slice is all
   `single-session-user`, so there is no "old vs new" value to disambiguate.
   The negative M2 finding (no full-stale samples) is robust to this
   conflation, but the *positive* claim that "SUPERSEDE is a reader-level
   no-op for 74% of samples" depends on the tie verdicts being genuine ties
   (which they are, per manual inspection of the cached judge reasons — the
   ties are formatting-level differences like "Luna" vs "Luna."). An S5
   follow-up M2 on `temporal-reasoning` / `knowledge-update` subsets is
   required before claiming the operating-surface-narrowness thesis
   generalizes beyond `single-session-user`.

2. **Degenerate router confusion matrix on the 50-question slice.** All 50 v2
   questions are `single-session-user` (gold = SEMANTIC), so the v2-slice
   confusion matrix has only one populated gold row. The router accuracy
   (4% on the slice) is therefore not directly comparable to the N9 80%
   threshold, which presumes a multi-class gold distribution. The diagnosis
   report correctly mitigates this by supplementing with the full 500-question
   router-only run (38% accuracy, multi-class confusion matrix), but the S5
   framing should cite the 500-question number, not the 4% slice number, when
   arguing router mis-routing as a contributor.

3. **IPv6/connection-reset infrastructure flakiness.** The S3 ablation script
   required an IPv4 monkeypatch and an outer 5-retry loop with 8s×2^n backoff
   (`weight_ablation.py:90-112`) to keep all 3 ablation arms at 0 failures.
   This is a transient network condition, not a code defect, but it means S5's
   500-question run (if attempted) will need the same resilience. The
   monkeypatch should not be promoted into `src/evoeventmem/` production code;
   a proper IPv4/IPv6 fallback belongs in the HTTP client layer if the issue
   persists.

4. **Ablation baseline (`v2_full_em=0.48`) is read from the v2 `summary.json`,
   not re-derived by re-running `qemr` through the ablation script.** This is
   acceptable (the v2 run is finalized and cached) but means the ablation EM
   comparison rests on the v2 run's reproducibility, which S2's review
   already audited. If S5 re-runs the v2 baseline, the ablation arms would
   also need re-running for strict comparability.

5. **Step 3 embedding swap deferred, not declined.** The §3 deferral leaves
   "embedding quality is the bottleneck" as an untested hypothesis. The §5
   root-cause synthesis correctly ranks router mis-routing above embedding
   quality (since §2 showed weights are not the bottleneck), but S5 path B
   should re-evaluate this if the router-rule fix alone does not close the
   `full` vs `vector_rag` gap (Δ -0.08).

## 7. Final verdict

**CONDITIONAL PASS.**

All 15 acceptance criteria pass with verifiable evidence. Scope boundaries
(router.py, `QEMR_WEIGHT_PROFILES`, R3, v3 prompt) are intact. No new
overclaim. No mixed-model / mixed-budget comparison. Tests/lint/types/smoke
all green. The diagnosis report is internally consistent — every number in
§1-§4 was re-derived from the corresponding run artifact and matches
byte-for-byte. The §5 root-cause synthesis is appropriately hedged and routes
to S5 branch C (intermediate route) with three well-scoped post-S3 levers
(router-rule edits; embedding swap; M2 on temporal-salient subsets).

The verdict is **conditional** rather than full PASS because of:

- Two mechanical deviations (combined step-4+5 commit; standalone ablation
  script instead of `run.py` config) that are acceptable but worth noting
  for S4a/S5 reproducibility documentation.
- One infrastructure deviation (IPv4 monkeypatch) that is contained to the
  diagnostic script but signals network flakiness S5 will need to handle.
- One judge-design limitation (correctness/staleness conflation on
  non-temporal-salient questions) that the report honestly acknowledges but
  that bounds the M2 finding's generalizability to `single-session-user`.

None of these block S5. S5 should:
1. Cite the 500-question router accuracy (38%) rather than the 4% slice
   number when arguing router mis-routing.
2. Re-run M2 on `temporal-reasoning` / `knowledge-update` subsets before
   claiming the operating-surface-narrowness thesis generalizes.
3. Not promote the IPv4 monkeypatch into production; instead fix the HTTP
   client if the issue persists.
4. Re-evaluate the Step 3 embedding swap if the post-S3 router-rule fix
   alone does not close the `full` vs `vector_rag` gap.

**Changed files (vs S2 base `17b1014`):**
- `benchmarks/mechanism/router_diagnosis.py` (new, 502 lines) — Step 1
  deterministic router confusion-matrix tool.
- `benchmarks/mechanism/weight_ablation.py` (new, 496 lines) — Step 2
  standalone ablation runner (3 arms, composite cache, IPv4 shim).
- `benchmarks/mechanism/m2_stale_judge.py` (new, 486 lines) — Step 4
  minimax-m3 stale/fresh judge with cached inputs/outputs.
- `tests/mechanism/test_router_diagnosis.py` (new, 83 lines) — confusion
  matrix computation tests on fake queries.
- `tests/mechanism/test_m2_stale_judge.py` (new, 55 lines) — judge-output
  parsing tests.
- `tests/retrieval/test_qemr.py` (+29 lines) — two ablation weight tests
  (zero-source + uniform + production-dict non-mutation).
- `src/evoeventmem/retrieval.py` (+28 lines) — three `RetrievalStrategy`
  enum values + `resolve_weights` branches; production `QEMR_WEIGHT_PROFILES`
  untouched.
- `docs/QEMR_FAILURE_DIAGNOSIS.md` (new, 358 lines) — full §1-§5 diagnosis
  report + S5 routing.
- `docs/S3-execution-prompt.md` (new, 538 lines) — execution prompt
  (committed at `1840d77` before S3 work began).
