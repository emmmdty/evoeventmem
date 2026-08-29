# REMEDIATION_SPEC.md — Independent Review

> Reviewer: independent technical reviewer (senior engineer / interviewer perspective)
> Review date: 2026-08-18
> Subject: `docs/REMEDIATION_SPEC.md` v1.0 (2026-08-18)
> Method: read spec fully, then grounded claims against source code, finalized artifacts, audit, AGENTS.md, and arXiv abstracts. No code changes made.
> Convention: file paths are repo-relative; line numbers are 1-indexed post-Read-prefix.

---

## Verdict: **CONDITIONAL PASS**

The spec is fundamentally sound, well-grounded in real evidence, and its S5 branching logic is honestly balanced (no bias toward positive outcomes). The numbers cited match `summary.json` and `report.md` exactly. The structural-unreachability claim is technically correct. The literature citations are real (3 of 8 spot-checked via arXiv; all abstracts confirm the spec's attributed claims).

However, the spec repeats a milder form of the very overclaim pattern it is meant to remediate, and it forgets four audit-flagged issues (M2, replay/online divergence, 6m NA ETEC actions, judge same-source bias). These are fixable in a v1.1 revision without restructuring the spec. **No stage should execute until the Blocking issues below are resolved.**

---

## Per-checklist-item findings (with evidence)

### 1. Problem framing accuracy — **MOSTLY ACCURATE, one overclaim**

**1a. R1/R1b/R3 structural unreachability — ACCURATE (with one imprecision).**

Verified by reading the code:
- `src/evoeventmem/consolidation.py:876`: `_contradiction_score` returns 0.0 unless ALL of `not multi_valued`, `_same_fact_slot == True`, and `not _same_fact_value` hold (plus interval-overlap check at lines 880–884). The spec's evidence table line 17 and S1 line 113 describe the gate as "multi_valued=False + same_fact_slot + interval overlap" — this **omits the `_same_fact_value=False` (different values) condition**, which is semantically obvious (you can't contradict yourself) but is a real third gate the spec glosses. Minor imprecision, not a misframing.
- `src/evoeventmem/consolidation.py:398–401`: SUPERSEDE branch requires `contradiction_score >= supersede_contradiction_min` (0.7) AND `not multi_valued`. Then `consolidation.py:402–410` calls `_fact_effective_time` (defined at `consolidation.py:770–771` as `memory.valid_from or memory.event_time`); if either side is None → REJECT (`missing_fact_effective_time`); if equal → REJECT (`equal_fact_effective_time`); else SUPERSEDE.
- `src/evoeventmem/extraction.py` — grep for `fact_slot|valid_from|valid_until|multi_valued` returns **0 matches** in this file. Confirms R1 (no `fact_slot` produced) and R1b (no `valid_from` produced). The LLM is free to emit arbitrary metadata, but extraction has no schema enforcing these fields.
- Conclusion: SUPERSEDE is structurally unreachable when (a) extraction omits `fact_slot` (R1 → `_same_fact_slot` always False → contradiction=0), and (b) extraction omits `valid_from` (R1b → `_fact_effective_time` falls back to coarse `event_time` → `equal_fact_effective_time` REJECT).

**1b. "test50-mimo omitted from all docs" — ACCURATE.**

`grep -rl "test50\|m13-longmemeval-test50" docs/ README.md` returns exactly **2 files**: `docs/REMEDIATION_SPEC.md` (the spec itself) and `docs/next-window-50run-prompt.md` (a working prompt). No narrative or results doc (`README.md`, `EVALUATION.md`, `STRONG_RESULTS_SMALL_SAMPLE.md`, `INTERVIEW_KIT.md`, `RESUME_NARRATIVE.md`, `9of10_ACCEPTANCE.md`, `9of10_AUDIT.md`) mentions it. The spec's S0 acceptance criterion (>=5 files after fix) is achievable by adding sections to the four docs listed in S0 step 1. ✓

**1c. test50-mimo numbers match `summary.json` — EXACT MATCH.**

Cross-checked every number in the spec's S0 step 1 table against `runs/publication/m13-longmemeval-test50-mimo/summary.json`:

| method         | spec EM | summary.json EM (line) | spec tok/q | summary.json tok/q (line) | spec write ms | summary.json p50_write_ms (line) |
|----------------|---------|------------------------|------------|---------------------------|---------------|----------------------------------|
| no_memory      | 0.00    | 0.0 (L179)             | 10.6       | 10.56 (L190)              | -             | null (L187)                      |
| full_context   | 0.00    | 0.0 (L153)             | 4094.9     | 4094.86 (L164)            | -             | null (L161)                     |
| vector_rag     | 0.56    | 0.56 (L205)            | 4072.5     | 4072.5 (L216)             | 45            | 45.09 (L213)                    |
| event_no_etec  | 0.54    | 0.54 (L101)            | 4082.7     | 4082.66 (L112)            | 36            | 36.21 (L109)                    |
| etec           | 0.52    | 0.52 (L75)             | 4083.0     | 4083.0 (L86)              | 130,185       | 130185.24 (L83)                 |
| full           | 0.46    | 0.46 (L127)            | 4080.9     | 4080.92 (L138)            | 130,185       | 130185.24 (L135)                |

Token F1 and the vector_rag p50_search latency (437556.77 ms = 437.6 s, matching the spec's "437s") also match. No discrepancies.

**1d. LoCoMo numbers — ACCURATE.**

Spec line 14: "`full` EM=0.0634 < vector_rag 0.0861, p=0.000". `runs/main/report/report.md:17` shows `full | 1986 | 0.0634`, `report.md:14` shows `vector_rag | 1986 | 0.0861`, and `report.md:35` shows `C01 | 0.0861 vs 0.0634 (Δ +0.0227, 95% CI [+0.0141, +0.0312], p=0.000 *)`. ✓

Spec line 15: "拆 ETEC（full→event_no_etec）+8 EM；拆 QEMR（full→etec）+6 EM". 0.54 − 0.46 = 0.08, 0.52 − 0.46 = 0.06. ✓ Honest framing — explicitly labels both contributions as "有害" (harmful).

### 2. Stage scoping — **ACCEPTABLE, two stages borderline**

Per AGENTS.md task protocol ("one task file per chat", "keep changes small enough to review in one diff"):
- **S0** (6 doc edits, 0 code): single-window, small diff. ✓
- **S1** (schema + prompt + consolidation confirmation + fixture regression + new unit test + real-data smoke): the spec itself flags "单窗口可能不够" and proposes splitting into S1a (schema + prompt) and S1b (consolidation tests + smoke). Should commit to the split upfront — 5 days of work touching extraction schema AND prompts AND tests is not "one diff".
- **S2** (run + diagnosis): single-window if the run completes; runs are mostly autonomous. ✓
- **S3** (router diagnosis + weight ablation + optional embedding rerun): 3 sub-stages; embedding rerun is skippable per acceptance criterion. ✓ But "weight profile 消融" in `retrieval.py` is a code change to the retrieval pipeline that should be its own diff.
- **S4** (5 sub-steps including `.env.example`, offline config, **vector_rag latency code fix**, model pinning doc): the vector_rag latency fix (step 3) is a non-trivial code change to `infra/async_embedding.py` or `benchmarks/vector_baseline.py`. The spec estimates it as "独立 1 天" — this is effectively a sixth stage hiding inside S4. Should be split out as S4a (config/docs, no code) and S4b (latency code fix).
- **S5** (5 sub-steps + paper draft): single-window. ✓

### 3. Acceptance criteria verifiability — **MIXED, several unmeasurable**

Verifiable (concrete command, deterministic output):
- S0: `grep -rl`, `test -f`, `wc -w`, `grep -c O09 TASKS.md` — all checkable. ✓
- S2: `ls FINALIZED.json`, the `Counter` python snippet — checkable. ✓
- S4: `test -f configs/.../offline10.toml`, `git ls-files .env | wc -l` — checkable. ✓

**Unmeasurable / under-specified** (flag):
- **S1 step 5**: "5 题 smoke 的 extraction 输出里 `fact_slot` 字段非空率 ≥ 80%" — which 5 questions? What counts as "non-empty"? The verification command is a python stub with a comment, not runnable code. Cannot be checked as written.
- **S1 step 4**: "用 ≥1 真实 LongMemEval 样例的 extraction 输出，断言至少一对 event 命中 `_same_fact_slot=True` 且 `_intervals_overlap=True`" — depends on the LLM emitting the right shape after a prompt change; may simply not fire (see Blocking issue #1). No fallback if the test cannot be written because no pair satisfies the condition.
- **S1 step 6**: "provenance coverage 仍 100%" — no concrete command (which artifact? which field?).
- **S2 step 5**: "v1 vs v2 `full` EM 对比表写入 `docs/EVALUATION.md`" — checkable as a doc edit, but the comparison is a one-off number with no CI specified; pre-registration in `METHODOLOGY_CHANGE.md` is referenced but not enforced by a command.
- **S3 step 1**: "router accuracy < 80% → 修 router 规则" — the threshold is a tuning decision, not a measurable acceptance gate; the spec doesn't say what "修 router 规则" means in scope terms.
- **S3 step 4**: "诊断报告含明确根因结论" — subjective ("明确根因结论").
- **S5 step 1–4**: "README / INTERVIEW_KIT / RESUME_NARRATIVE 与最终 thesis 一致" — subjective consistency check.
- **S5 step "独立审查通过"**: the spec's "独立审查协议" (lines 395–406) is a process, not a command; the review checklist includes subjective items ("未引入新的 overclaim" via `grep` for 强 claim keywords — partially checkable, but the "any new strong claim must have p-value + CI" rule is a human judgment).

### 4. Literature grounding — **VERIFIED (3 of 8 spot-checked)**

Spot-checked via `webfetch` on `https://arxiv.org/abs/<id>`:

| Citation | arXiv verified | Spec claim | Abstract support |
|---|---|---|---|
| LongMemEval (2410.10813) | ✓ real, ICLR 2025, Wu et al. | "fact-augmented keys +9.4% recall, +5.4% QA" (§5.3); "time-aware query expansion +6.8–11.3% temporal" (§5.4) | Abstract confirms the optimizations exist ("fact-augmented key expansion for indexing, and time-aware query expansion") and that they "greatly improve both memory recall and downstream question answering". **Specific percentages are in the paper body, not the abstract** — could not verify the exact 9.4/5.4/6.8–11.3 numbers from the abstract alone. Topic and direction are confirmed. |
| MemTrace (2606.17328) | ✓ real, submitted 2026-06-15, Long et al. | "evidence 10x retrievable than missing" — pivot basis | Abstract EXPLICITLY confirms: "The dominant bottleneck is evidence use, not retrieval: when systems fail, the evidence was retrievable 10 times more often than it was missing." **Exact match.** |
| Mem0 (2504.19413) | ✓ real, submitted 2025-04-28, Chhikara et al. | "graph memory 仅 +2% over base" | Abstract confirms: "Mem0 with graph memory achieves around 2% higher overall score than the base configuration." **Exact match.** |

The five unchecked citations (LOCOMO 2402.17753, MemGPT 2310.08560, TMA-NM 2606.24322, Filesystem-Based Memory 2607.26637, CraniMem 2603.15642) follow plausible arXiv ID conventions for 2024–2026 papers but were not verified by this review. The spec should add a one-line note confirming each citation was retrieved and the claim matched the abstract, to forestall "did you actually read these?" challenges.

### 5. Overclaim prevention — **ONE MATERIAL OVERCLAIM (Blocking #1)**

The spec mostly avoids the overclaim pattern: S5 branches A/B/C are balanced, S2 step 5 says "**不要预先声明期望**", and the spec explicitly cites the pre-registration framework (`METHODOLOGY_CHANGE.md`).

**But the S1 headline repeats the overclaim pattern in a milder form:**
- Spec line 110–111: "让 ETEC 的 SUPERSEDE 分支在真实 LongMemEval 数据上**第一次变得可达**" (make SUPERSEDE branch reachable for the first time on real data).
- The S1 plan only fixes R1 (fact_slot) and R1b (valid_from). It does **not** address R3, which the audit (`9of10_AUDIT.md:162`, `9of10_AUDIT.md:340`) identified as the dominant blocker: `multi_valued` overmark accounts for **18/29 (62%)** of SUPERSEDE-blocking candidates.
- `_is_multi_valued` (`consolidation.py:988–989`) is `metadata.get("multi_valued") is True` on either side. The spec's S1 step 1 adds `fact_slot`, `valid_from`, `valid_until`, `fact_value` to the schema — but **does not add `multi_valued` to the schema or change the prompt's multi_valued behavior**.
- The audit (`9of10_AUDIT.md:340`) explicitly classified fixing `multi_valued` as "borderline 调参凑数" (parameter-fishing) and chose NOT to fix it for compliance with AGENTS.md's "不调参凑数" rule. The spec inherits this constraint by not touching multi_valued.

**Consequence**: After S1, SUPERSEDE may STILL be 0 (the LLM continues overmarking `multi_valued=True` on genuinely single-value slots). The spec's S2 risk section (line 227) honestly acknowledges this: "extraction 产了 fact_slot 但 LLM 仍过打 multi_valued（R3 未修）". But the **S1 acceptance criterion** ("至少一对 event 命中 `multi_valued=False` + `_same_fact_slot=True` + `_intervals_overlap=True`") may then be **unsatisfiable**, and the spec provides no fallback for S1 acceptance failure (the S5 branch-A fallback only triggers after S2 measures SUPERSEDE=0, not after S1 can't write its test).

This is the same pattern the spec is meant to remediate: a strong headline ("becomes reachable") with the risk buried in a sub-bullet. **The S1 headline should be downgraded to "make SUPERSEDE's first gate (fact_slot) satisfiable; whether SUPERSEDE becomes reachable additionally depends on the LLM's multi_valued behavior, which S1 does not modify (R3 not fixed, per AGENTS.md anti-fishing rule)."**

Other overclaim-adjacent items (non-blocking):
- The spec's evidence table row 16 says "ETEC SUPERSEDE 真实数据 0/32（0 测量 + 0 外推）" citing `9of10_ACCEPTANCE.md §a.7`. The audit (`9of10_AUDIT.md:89`, Q1) flagged this exact "0/32" framing as a honesty gap (it conflates 0/8 measured + 0/24 extrapolated). The spec's parenthetical "(0 测量 + 0 外推)" acknowledges the audit's correction, but the headline "0/32" is still the dishonest framing the audit rejected. S0 step 1 / S2 should use "0/8 measured + 0/24 extrapolated" everywhere, including in the spec's own evidence table.
- Spec line 19: "LongMemEval §5.3: fact-augmented keys +9.4% recall, +5.4% QA". These specific numbers are in the paper body, not the abstract (which I verified). The spec should add a note "verified from paper body §5.3" or downgrade to "LongMemEval §5.3 reports positive recall and QA gains from fact-augmented keys" if the body was not actually read.

### 6. Honesty about negative outcomes — **STRONG**

S5 branching logic (lines 347–365) is genuinely balanced:
- Branch A (SUPERSEDE=0, pivot to negative-result + auditability) is given equal structural weight to Branch B (SUPERSEDE>0, positive thesis).
- Branch B is held to a HIGHER bar ("必须补跑 500-run 确认效应稳定") than A or C — the right direction (positive claims require more evidence).
- The spec explicitly labels branch C ("SUPERSEDE > 0 但 full 仍输") as "**最诚实的结果**" — the most honest outcome. There is no rhetorical bias toward B.

The only soft spot: there is no branch for "S2 finishes but the run is inconclusive due to quota/network failures" — the spec's S2 risk section mentions 429/403 and `--resume-dir` retries, but if the run cannot complete, S5 has no path. Should add branch D: "S2 inconclusive due to infra failure → S5 falls back to S0's disclosure + auditability framing using v1 data only".

### 7. AGENTS.md compliance — **COMPLIANT**

- **"Do not commit datasets, secrets, model weights"**: S4 step 5 explicitly verifies `git ls-files .env` is empty. S4 step 1 adds `.env.example` (template, no real values). S4 step 2 uses `deterministic_fake` provider for offline repro. ✓
- **"Core memory logic must not depend on specific vendor"**: S1 changes `extraction.py` schema and prompt, `consolidation.py` confirmation — no vendor imports introduced. S3 ablations are in `retrieval.py` and `benchmarks/mechanism/router_diagnosis.py` — no vendor coupling. S4 step 1 mentions `OPENAI_BASE_URL` in `.env.example` but only as a configurable env var, not a hard dependency in core logic. ✓
- **"Reject broad refactors not required by the selected task"**: S1 explicitly scopes to "不重写 ETEC 算法本身（`_score_pair` 决策树保留）". S3 scopes to "先诊断再决定" (no weight-profile rewrite). S4's vector_rag latency fix is borderline (could touch `infra/async_embedding.py`) — should be its own scoped task. ✓ overall.
- **"Never mark benchmark gains without generated result artifacts"**: S5 step 1 requires `v1 vs v2 对比表` derived from `runs/.../summary.json`. S5 verification: `grep -c "test50-mimo-v2" docs/REMEDIATION_FINAL_REPORT.md`. ✓
- **"Reject changes that mix benchmark methods under unequal model/context/retrieval-budget"**: S2 step 2 explicitly invalidates the v1 chat cache (extraction schema changed) and reuses only embedding cache. ✓ But the spec does NOT address the model mismatch (deepseek-v4-flash in 24-question runs vs mimo-v2.5 in 50-question run) for cross-run comparison — S4 step 4 only documents it as a "已知 limitation". The spec's S5 step 1 "v1 vs v2 对比表" compares mimo-v2.5 (v1) vs mimo-v2.5 (v2) — same model, OK. But comparing v2 to the 24-question deepseek runs is invalid; the spec should explicitly forbid this in S5.

### 8. Missing stages / gaps — **FOUR MATERIAL GAPS**

**Gap 1: M2 stale-memory judge never planned.** The audit (`9of10_AUDIT.md:47–54`, `9of10_AUDIT.md:362`) records M2 as "NOT_PRODUCED_QUOTA_BLOCKED" and defends the null with "SUPERSEDE=0 隐含 full vs event_no_etec stale_rate Δ≈0 (结构性 null)". **This defense is invalid the moment S2 SUPERSEDE > 0.** If SUPERSEDE fires, the structural-null defense collapses and M2 becomes a real measurement the spec must run. The spec's S3 (QEMR diagnosis) and S5 (finalization) never mention M2. The spec must either (a) add M2 to S3 (run stale judge on v2 run if SUPERSEDE > 0), or (b) explicitly scope M2 out with a caveat that the auditability thesis is weakened without it.

**Gap 2: Replay vs online inconsistency unaddressed.** The audit (`9of10_AUDIT.md:74–78`, `9of10_ACCEPTANCE.md:245`) documents the replay/online divergence: `4dfccbf8` online ADD 223/MERGE 1 vs replay ADD 210/MERGE 14, caused by `benchmarks/mechanism/replay.py:130-134` `LinkCandidateGenerator` cache-miss. The spec's S2 plans a new v2 run but says nothing about whether replay will be re-run on v2, whether the divergence will be re-checked, or whether the v2 run's `ingestion.etec.actions` will be trusted as the canonical action counts (as the audit did for v1). The spec should add to S2: "replay/online divergence re-checked on v2; if it persists, document it as a known limitation, do not silently fix".

**Gap 3: 6m run's ETEC actions = NA.** `9of10_ACCEPTANCE.md:127`, `9of10_AUDIT.md:286`: the 6m run never persisted `ingestion.etec.actions` (legacy field contract, no samples dir). The spec's S0 (consistency disclosure) and S4 (model pinning) do not address this. The 6m run cannot be re-run (deepseek-v4-flash decommissioned per S4 step 4). The spec should add to S0 step 5: "explicitly note in EVALUATION.md that 6m run's ETEC actions are NA (legacy field contract, run not reproducible due to decommissioned model)".

**Gap 4: Judge same-source bias (M2 not run).** `9of10_ACCEPTANCE.md:248`: "judge 同源偏差风险：M2（未跑）默认 deepseek-v4-flash 与 reader 同模型". If S3/S5 re-run M2 with mimo-v2.5 as both reader and judge, same-source bias recurs. The spec should specify a judge model != reader model for any M2 rerun, per AGENTS.md ("LLM judges require cached inputs/outputs and a documented judge model").

**Vector_rag 437s latency — addressed but dependency unclear**: S4 step 3 fixes it. S2 risk section (line 227) says "vector_rag 上次 p50=437s. 缓解：S4 修了再跑，或这次并行度降到 5". This implies S4 should land before S2 — but the dependency graph (lines 414–415) says S4 is parallel to S2/S3. Inconsistent (see Blocking #3).

### 9. Dependency graph soundness — **ONE INCONSISTENCY (Blocking #3)**

The graph `S0 → S1 → S2 → S3/S4 → S5` is mostly sound:
- S0 before S1 (honesty first): sound.
- S1 before S2 (extraction change must land before rerun): sound. S2 step 2 correctly invalidates v1 chat cache, reuses embedding cache.
- S2 before S3 (weight ablations reuse v2 reader cache): sound for S3 step 2.
- S2/S3 before S5 (branching needs both): sound.

**Inconsistency — S4 "parallel to S2/S3" is false for step 3**:
- S4 step 3 (vector_rag latency fix) is a code change to `infra/async_embedding.py` or `benchmarks/vector_baseline.py`. If S2's v2 run executes concurrently with this code change, S2's vector_rag p50_search latency is measured mid-fix — neither the v1 437s nor the post-fix <30s number, but an arbitrary intermediate. S2's acceptance criteria don't include latency measurement (only SUPERSEDE counts and EM), so this doesn't block S2 acceptance — but it makes S2's latency data useless for S5's v1-vs-v2 comparison.
- The S2 risk section's "S4 修了再跑，或这次并行度降到 5" implicitly admits S4 should land first OR S2 runs with degraded parallelism. The dependency graph should be corrected to: **S4 step 3 must land before S2 starts; S4 steps 1, 2, 4, 5 can be parallel.**

**S3 step 3 (embedding ablation) doesn't depend on S2's cache** (re-embeds all chunks from scratch) — could be parallel to S1. Minor; current graph (S3 after S2) is safe but suboptimal.

**S1 risk of breaking S3**: S1 changes extraction.py schema. S3's weight ablations reuse v2's reader cache (built post-S1). If S1's schema change breaks provenance assertions in `tests/retrieval/test_qemr.py`, S3's ablations could be invalid. S1 risk section (line 167) does mention "每改一处跑 `tests/retrieval/test_qemr.py` 的 provenance 断言" — adequate mitigation. ✓

### 10. Independent reviewability — **THREE STAGES NEED MORE CONTEXT**

- **S0**: clear, any engineer can execute. ✓
- **S1**: missing context — which specific 5 questions for the smoke? What is the exact schema validation rule for `fact_slot` (min_length? regex?)? Where is the few-shot example inserted (`prompts/event_extraction*.md` — which file)? The verification python snippet is a comment, not runnable code. An engineer would need to invent the smoke procedure. Flag.
- **S2**: mostly clear (run command pattern is in `scripts/run50-parallel.sh`), but the merge procedure ("参照本次执行的 sub-run + merge 方案") is not described — it references an external "2026-08-18 执行记录" without a file path. Flag.
- **S3**: clear — `benchmarks/mechanism/router_diagnosis.py` is named, ablations are named (`qemr_no_temporal`, `qemr_no_graph`, `qemr_uniform`). ✓
- **S4**: clear file targets. ✓
- **S5**: branching criteria are clear; branch C's "operating surface 太窄" is qualitative but defensible. ✓

---

## Blocking issues (must fix before any stage executes)

**B1. S1 overclaim + missing fallback (see §5).**
Downgrade the S1 headline from "make SUPERSEDE reachable" to "make SUPERSEDE's first gate (`_same_fact_slot`) satisfiable; whether SUPERSEDE becomes reachable also depends on the LLM's `multi_valued` behavior, which S1 does NOT modify (R3 not fixed, per AGENTS.md anti-fishing rule; audit `9of10_AUDIT.md:340` classed multi_valued fix as borderline 调参凑数)". Add an S1 acceptance fallback: "if no real LongMemEval pair satisfies `multi_valued=False + same_fact_slot=True + intervals_overlap=True` after the schema change, S1 is CONDITIONAL PASS — proceed to S2 to measure empirically whether SUPERSEDE > 0; if SUPERSEDE=0 in S2, the S1 reachability test is retired as unmet, not failed."

**B2. Add M2 to S3 or explicitly scope it out (see §8, Gap 1 & Gap 4).**
Either: "S3 step 4: if S2 SUPERSEDE > 0, run M2 stale judge on v2 run with judge model != reader model (e.g., minimax-m3 if quota allows, else document M2 as not-run with explicit caveat that the auditability thesis is weakened)". Or: add to S5 branch-A/B/C decision a note that M2 remains structurally null under branch A and is mandatory under branch B (since SUPERSEDE > 0 invalidates the structural-null defense).

**B3. Fix the S4||S2 dependency inconsistency (see §9).**
Update the dependency graph (lines 414–415) and S4's "可与 S2/S3 并行" claim (line 296) to: "S4 step 3 (vector_rag latency code fix) must land before S2 starts; S4 steps 1, 2, 4, 5 can be parallel to S2/S3. S2 may run with reduced parallelism if S4 step 3 is not yet landed, but S2's vector_rag latency data will then be non-comparable to v1 and must be flagged in S5."

**B4. Add the four forgotten audit gaps to S0/S2/S3 (see §8).**
- Replay/online divergence: add to S2 step 4 a sub-step "re-run replay on v2; if divergence persists, document as known limitation, do not silently fix" (per audit `9of10_AUDIT.md:74–78`).
- 6m NA ETEC actions: add to S0 step 5 a sub-bullet "note 6m run's ETEC actions are NA (legacy field contract, deepseek-v4-flash decommissioned, run not reproducible)".
- Judge same-source bias: covered by B2.

---

## Non-blocking issues (should fix in v1.1)

- **N1.** S1 evidence-table description of `_contradiction_score` (line 113) omits the `not _same_fact_value` (different-values) gate at `consolidation.py:876`. Add for accuracy.
- **N2.** Spec's own evidence table row 16 ("ETEC SUPERSEDE 真实数据 0/32") repeats the audit-rejected "0/32" framing. Replace with "0/8 measured + 0/24 extrapolated (same pipeline v1, mechanism40 not finalized)" per audit `9of10_AUDIT.md:89,250`.
- **N3.** LongMemEval §5.3/§5.4 specific percentages (9.4/5.4/6.8–11.3) are in the paper body, not the abstract (verified). Add a note "verified from paper body §5.3/§5.4" or downgrade to qualitative if body was not read. Also add a one-line "citation verified" note for the 5 unchecked citations (LOCOMO, MemGPT, TMA-NM, Filesystem-Based Memory, CraniMem).
- **N4.** S1 step 5's smoke (which 5 questions? what's "non-empty"?) and S1 step 6's "provenance coverage 100%" lack runnable verification commands. Replace the python stub with a concrete `uv run python -c "..."` that exits non-zero on failure.
- **N5.** S4 step 3 (vector_rag latency fix) is effectively a sixth stage hiding inside S4. Split S4 into S4a (config/docs, no code) and S4b (latency code fix) for single-diff reviewability per AGENTS.md.
- **N6.** S1 scope ("3–5 days, 单窗口可能不够") should commit upfront to the S1a/S1b split the spec already proposes as a fallback. Per AGENTS.md "keep changes small enough to review in one diff", 5 days of schema+prompt+tests is not one diff.
- **N7.** S5 has no branch for "S2 inconclusive due to infra failure". Add branch D: "S2 incomplete → S5 falls back to S0's disclosure + auditability framing using v1 data only; v2 rerun deferred to next window".
- **N8.** S5 step 1 "v1 vs v2 对比表" should explicitly forbid comparing v2 (mimo-v2.5) to the 24-question deepseek-v4-flash runs (different model — AGENTS.md "Reject changes that mix benchmark methods under unequal model"). Cross-run comparison is only valid v1-mimo vs v2-mimo.
- **N9.** S3 step 1's "router accuracy < 80% → 修 router 规则" is a tuning decision, not a measurable gate. Either specify the rule changes (which `_RELATIVE_RE` patterns?) or scope S3.1 to "produce confusion matrix; rule changes deferred to a follow-up task if accuracy < 80%".
- **N10.** The spec's "独立审查协议" (lines 395–406) includes subjective items ("未引入新的 overclaim" via grep + human judgment of p-value/CI). This is fine as a process but should not be listed under "验证命令" as if it were a deterministic check.

---

## Recommendations for spec v1.1

1. **Apply B1–B4.** These are the minimum changes to make the spec internally consistent and closed under the audit's own findings.
2. **Tighten S1**: commit to the S1a/S1b split upfront; downgrade the reachability headline; add the multi_valued non-fix as an explicit scope boundary, not a buried risk.
3. **Tighten S2**: add replay-consistency check, infra-failure branch, and explicit non-comparability of v2 latency if S4 step 3 hasn't landed.
4. **Tighten S3**: add M2 sub-stage (conditional on S2 SUPERSEDE > 0) with a non-reader judge model; specify router rule scope or defer rule changes.
5. **Tighten S4**: split into S4a (config/docs) and S4b (latency code fix); fix the dependency graph so S4b precedes S2.
6. **Verify all 8 citations**: add a one-line "verified from abstract/body §X" note per citation; downgrade any specific percentages not actually read from the body.
7. **Replace all subjective acceptance criteria** with concrete commands or explicitly mark them as human-judgment process items (not "验证命令").
8. **Add a v1.1 changelog** at the bottom of the spec noting which Blocking/Non-blocking issues were addressed.

---

## Summary

The spec is a serious, well-grounded remediation plan that correctly identifies the project's real problems (structural unreachability, test50-mimo disclosure gap, trivial-baseline headline, null result). Its S5 branching is honest. The numbers match reality. The literature is real.

But it is not yet above reproach: the S1 headline ("SUPERSEDE becomes reachable") is the same mild overclaim pattern the project is being remediated for, because S1 fixes only R1/R1b and leaves R3 (multi_valued, 62% of blockers) untouched per AGENTS.md's anti-fishing rule. The spec buries this in S2 risks instead of stating it in the S1 headline. It also forgets four audit-flagged issues (M2, replay divergence, 6m NA, judge bias) and has a dependency-graph inconsistency (S4 step 3 is not actually parallel-safe with S2).

CONDITIONAL PASS: fix B1–B4, then execute S0. S1–S5 should not start until v1.1 is reviewed again.
