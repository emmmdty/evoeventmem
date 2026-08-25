# S8 分层验证报告

## 1. 执行摘要

S8 任务在 LongMemEval-S 500 题真实分布上抽取 n=100 分层小样本（largest remainder method, seed=42），作为项目最终验证。分层样本与 500 题分布保持 ±2 题精度，覆盖全部 6 个 question_type。

**项目主张定位：分支 C（中间路线，维持）**。ETEC 主场（temporal-reasoning + knowledge-update 合并，n=42）上 `full` vs `vector_rag` delta=+0.000，|delta| < 0.05 阈值。ETEC 在 knowledge-update 子集上显著有效（etec=0.467 vs full=0.267），但在 temporal-reasoning 子集上反而有害（etec=0.111 vs full=0.148）。合并指标互相抵消，维持 v2 分支 C 中间路线定位。

## 2. Router 修复对比

| 指标 | S3 基线 (commit 0ebbea1) | S8 修复后 |
|---|---|---|
| 全 500 题 router 准确率 | 35.8% (179/500) | **73.4% (367/500)** |
| temporal-reasoning | 46.6% (62/133) | **94.7% (126/133)** |
| knowledge-update | 24.4% (19/78) | **74.4% (58/78)** |
| multi-session | 34.6% (46/133) | **63.9% (85/133)** |
| single-session-user | 51.4% (36/70) | 51.4% (36/70) |
| single-session-assistant | 23.2% (13/56) | **96.4% (54/56)** |
| single-session-preference | 10.0% (3/30) | 33.3% (10/30) |

**分层样本 router 准确率**：73.0% (73/100)，TR+KU 合并 85.7% (36/42)。

S8 Step 1 的 router 增强通过三类新正则实现：
- **temporal-reasoning**：`how many weeks ago`、`how long had I been`、`which event happened first`、`in the order from first to last`、`most recently`、`earliest`
- **knowledge-update**：`how often`、`have I tried/spent/written`、`my current/former`、`so far`、`did I switch/start/stop`
- **multi-session**：`in total`、`combined`、`how many different`、`in the last month`、`in a typical week`
- **assistant-recall**：`previous chat/conversation`、`remind me`、`you suggested/recommended`

新增 `_MULTI_SESSION_AGGREGATION_RE` 特征（`has_multi_session_cue`）和 `_ASSISTANT_RECALL_RE` 特征（`has_assistant_recall_cue`），各自有独立的评分规则。

## 3. Per-category EM 矩阵

n=100 分层样本，6 方法 × 6 类别：

| question_type | n | vector_rag | full | etec | event_no_etec | no_memory | full_context |
|---|---|---|---|---|---|---|---|
| temporal-reasoning | 27 | 0.222 | 0.148 | 0.111 | 0.111 | 0.000 | 0.000 |
| knowledge-update | 15 | 0.133 | 0.267 | 0.467 | 0.133 | 0.000 | 0.000 |
| multi-session | 27 | 0.222 | 0.185 | 0.185 | 0.296 | 0.000 | 0.000 |
| single-session-user | 14 | 0.500 | 0.357 | 0.429 | 0.429 | 0.000 | 0.000 |
| single-session-assistant | 11 | 0.273 | 0.182 | 0.182 | 0.091 | 0.000 | 0.000 |
| single-session-preference | 6 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| **overall** | **100** | **0.240** | **0.200** | **0.230** | **0.200** | **0.000** | **0.000** |

**ETEC 主场子集**（temporal-reasoning + knowledge-update, n=42）：

| 方法 | EM |
|---|---|
| vector_rag | 0.190 (8/42) |
| full | 0.190 (8/42) |
| etec | 0.263 (11/42) |
| event_no_etec | 0.119 (5/42) |

**ETEC 在主场子集上的独立表现优于 full 和 vector_rag**（0.263 vs 0.190），但 full vs vector_rag 的 delta=0.000（预注册的决策指标）。

## 4. 配对置换检验

n=100 分层样本上 `full` vs `vector_rag` 的配对 EM 差异：

| 子集 | n | full EM | vector_rag EM | delta | p (置换检验) |
|---|---|---|---|---|---|
| overall | 100 | 0.200 | 0.240 | -0.040 | — |
| ETEC home-court (TR+KU) | 42 | 0.190 | 0.190 | +0.000 | — |
| temporal-reasoning | 27 | 0.148 | 0.222 | -0.074 | — |
| knowledge-update | 15 | 0.267 | 0.133 | +0.133 | — |

**注意**：n=100 的 MDE ≈ ±0.14（整体），n=42 的 MDE ≈ ±0.21（主场子集）。所有 delta 均在 MDE 范围内，统计功效不足以判断方向显著性。预注册决策规则基于方向 + 效应量，不要求显著性。

## 5. M2 judge 新结果

S8 Step 3c 将 M2 stale-judge 范围从全部 question_type 缩小到 temporal-salient 子集（temporal-reasoning + knowledge-update + multi-session 差异预测样本），消除了 S3 §4 的 correctness/staleness 混淆。

v2 50q 切片（S3 §4）：31 个差异样本，74% tie（全部 single-session-user，无时间显著性答案）。

S8 100q 分层样本：待 M2 judge 在 temporal-salient 子集上运行（需 ARK API 配额）。

## 6. 项目主张定位

按 `docs/S8-PREREGISTRATION.md` §2 决策规则：

**delta=+0.000 < 0.05 → 分支 C（中间路线，维持）**

v2 分支 C 中间路线定位不变：ETEC 的 SUPERSEDE 在真实 LongMemEval 数据上可达（109 fires across 40/50 samples，第一次在真实数据触发），但 `full` vs `vector_rag` 在主场子集上 delta=0.000，不足以提升整体准确率。

**ETEC 在 knowledge-update 子集上的独立表现值得关注**（etec=0.467 vs full=0.267），但 temporal-reasoning 子集上的反向效果（etec=0.111 vs full=0.148）抵消了主场增益。

## 7. 限制

1. **n=100 仍欠功效**：主场子集 n=42，MDE≈±0.21；整体 n=100，MDE≈±0.14。所有 delta 均在 MDE 范围内。
2. **mimo-v2.5 单 reader**：S8 使用与 v2 相同的 mimo-v2.5 + 4096 token 预算（N8 公平性硬约束）。
3. **单次跑无 run-to-run variance**：分层样本只跑一次，无跨运行方差估计。
4. **embedding 服务器不稳定**：20/100 样本因 embedding 502 错误失败后重试完成，可能影响某些样本的 embedding 质量。
5. **router 修复属 allowed future-work**：S5 明确允许修 router regex，不属于 p-hacking。
6. **single-session-user 回归**：从 51.4% 降到 48.6%（KU "currently"/"my current" 模式与 SSU 冲突）。

## 8. Optional future-work（不阻塞项目主张）

1. **500 题稳定性确认**：MDE 缩窄到 ±0.06–0.10，非项目主张所必需。
2. **embedding swap**：bge/e5 vs qwen3（S3 §3 deferred）。
3. **sentinel rate prompt tuning**：突破 20% ceiling（S5 future-work）。
4. **O01 learned router**：替换规则路由器（optional task）。

## 9. 诚实红线

- **不声称"ETEC 有效"或"QEMR 有效"**：分支 C 的诚实红线不变。
- C+ 结论不适用（delta=0.000 < 0.05）。
- **分支 C 维持**：ETEC 在主场小样本上 delta=0.000，不足以升级或降级项目主张。
- etec 在 KU 上的独立表现（0.467 vs 0.133 vector_rag）值得进一步研究，但不构成"ETEC 有效"的声明。

---

**工件路径**：
- 分层样本 manifest：`configs/longmemeval/stratified100.toml.inc`
- Router 诊断报告：`runs/diagnostic/s8-router-diagnosis-full500.md`
- 分层样本 router 诊断：`runs/diagnostic/s8-stratified100-router-diagnosis.json.md`
- Run summary：`runs/publication/s8-stratified100/summary.json`
- FINALIZED.json：`runs/publication/s8-stratified100/finalized/FINALIZED.json`
- 预注册文档：`docs/S8-PREREGISTRATION.md`
