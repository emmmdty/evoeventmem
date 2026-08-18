# REMEDIATION_SPEC.md v1.1 — Delta Review

> Reviewer: same independent technical reviewer who authored `REMEDIATION_SPEC_REVIEW.md` (v1.0).
> Review date: 2026-08-18
> Subject: `docs/REMEDIATION_SPEC.md` v1.1
> Method: verify B1–B4 resolution against spec text + source code; spot-check N1/N3/N7/N8/N9; scan for new inconsistencies. Delta review only.

---

## Verdict: **PASS** (post NP1 fix)

All four blocking issues (B1–B4) are substantively resolved. NP1 (leftover v1.0 acceptance block at lines 221–252 with contradictory `fact_slot ≥80%` and `3–5d` estimate) was resolved by the author on 2026-08-18 — the duplicate block has been deleted; S1b now has a single, consistent acceptance block. Spec is internally consistent and ready to execute.

## Per-blocking-issue

- **B1 — RESOLVED.** S1a headline downgraded to "让…第一道闸门 `_same_fact_slot`…可满足" (line 114). R3 explicitly scoped out in three places: top "不做什么" (line 7), S1a scope边界 (line 116), and bottom "不做什么" (line 596). xfail fallback added at S1b step 2 (lines 184–185): "测试改成 **xfail 标记**…**不通过硬调阈值让测试 pass**". S1 split into S1a (line 112) + S1b (line 173).
- **B2 — RESOLVED.** S3 step 4 (lines 340–343) adds M2 as conditional sub-stage: "仅当 S2 测出 SUPERSEDE > 0 时执行。若 SUPERSEDE=0，跳过本步". Judge model ≠ reader: "judge 用 `minimax-m3` 或其他不同族模型" (line 343). S5 marks M2 not-needed under A (line 482), mandatory under B (line 489) and C (line 496).
- **B3 — RESOLVED.** S4 split into S4a (config/docs, no code, line 381) and S4b (latency code fix, line 426). Dependency graph retitled "B3 修正：S4b 必须先于 S2" (line 548) with explicit edge "S4b … ──必须先于 S2───┐" (line 555). S2 prerequisites reflect: "S4b…必须先于 S2…否则…对比无效" (line 265).
- **B4 — RESOLVED.** Replay/online divergence re-check added to S2 step 5 (line 276, "不静默修…auditability 角度的真实证据"). 6m NA note added to S0 step 5 (line 74) and redundantly in S4a step 4 (line 402). Judge same-source bias covered via B2's judge-≠-reader rule (line 343). "0/32" replaced with "0/8 测量 + 0/24 外推" in evidence table (line 18).

## Non-blocking spot-check

- **N1 — RESOLVED.** Evidence table (line 19) lists all four gates including `not _same_fact_value`; S1b test assertion (line 184) names all four explicitly.
- **N3 — RESOLVED.** Citations 1/2/5 marked ✓ with webfetch verification notes (lines 609–614); §5.3/§5.4 percentages flagged as body-not-abstract (line 168, 609); unverified ones marked "未独立验证，ID plausible".
- **N7 — RESOLVED.** Branch D added (lines 498–503) with infra-failure trigger + v1-only fallback; reflected in dependency graph (line 570).
- **N8 — RESOLVED.** v2-vs-deepseek cross-model comparison forbidden in S2 step 6 (line 277), S4a step 3 (line 400), and "不做什么" (line 602).
- **N9 — RESOLVED.** S3 step 1 (lines 326–328) scoped to confusion matrix + recommendation only; rule changes explicitly deferred to independent follow-up task; "不做什么" reinforces (line 597).

## New problems

- **NP1 — RESOLVED.** The leftover v1.0 acceptance block (former lines 221–252) with contradictory `fact_slot ≥80%` and `3–5d` estimate has been deleted by the author on 2026-08-18. S1b now contains a single acceptance block with `≥50%` threshold (consistent with N4) and `1–2 days` estimate.
- No new vendor coupling (judge model configurable, not hard-coded in core).
- No new broad refactors (S1a preserves `_score_pair`; S3.1 defers router changes).
- New acceptance criteria are verifiable or explicitly marked human-judgment (S3 root-cause per N10, line 354).
- Dependency graph consistent with each stage's prerequisites section (verified S2, S4b).

## Recommendation

Spec is **PASS** and ready to execute. Start with S0. Do not start S1a–S5 until S0 is committed.
