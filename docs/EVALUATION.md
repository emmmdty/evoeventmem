# 评测协议

> 方法论（2026-08-13 变更，详见 [`docs/METHODOLOGY_CHANGE.md`](docs/METHODOLOGY_CHANGE.md)）：
> 评测节奏从"先跑 500 样本再分析"改为"**小样本强结果先行，大样本一致性验证**"。
> 24 样本 run 的价值是机制级强结果（修复验证、机制指标、效率、失败归因），不做显著性声明；
> 500 样本只承担一致性验证（功效分析表明其最小可检测效应 > 观测效应，无显著性是预期内结果）。

## 1. 主任务

### LongMemEval

报告总体与分类型：information extraction、multi-session reasoning、knowledge update、temporal reasoning、abstention。

### LoCoMo

报告 QA 总体和类别结果；利用 evidence dialog IDs 计算 Evidence Precision/Recall/F1；利用 event summary 评估事件抽取/摘要的结构质量。

## 2. 必须基线

1. No Memory；
2. Full Context；
3. Session Summary；
4. Vector RAG；
5. Event memory without ETEC；
6. Event memory + ETEC；
7. Full EvoEventMem：ETEC + QEMR。

Mem0/Graphiti 可作为外部基线，但不应阻塞主线；版本、模型和配置必须固定。

## 3. 指标

### 端到端

- Exact Match / token F1 / benchmark official metric；
- category accuracy；
- abstention precision/recall；
- task success（执行型扩展）。

### 记忆中间层

- retrieval Recall@K、MRR、nDCG；
- Evidence Precision/Recall/F1；
- Entity Linking F1；
- Event Merge F1；
- Conflict Resolution Accuracy；
- stale-memory error rate；
- provenance coverage。

### 效率

- p50/p95 write latency；
- p50/p95 search latency；
- tokens/query；
- LLM calls/query；
- peak VRAM；
- storage growth per 1K turns。

## 4. 公平性

所有方法使用相同：

- data split；
- answer model；
- prompt；
- context budget；
- max retrieved items；
- temperature/seed（可用时）；
- judge model 与 judge prompt。

所有请求和结果写入不可变 JSONL，包含 git commit、config hash、dataset hash、model identifier 和时间戳。

## 5. 消融

至少：

- `- evidence constraint`；
- `- temporal validity`；
- `- graph relation`；
- `- query router`；
- fixed weighting vs QEMR；
- different memory/context budgets。

## 6. 结果门槛

不要预设夸张数字。适合写简历的方法贡献应至少满足其一：

- 总体公开指标有稳定提升并通过 bootstrap CI；
- 时间/更新关键子集有明显提升，同时总体不退化；
- 在相近效果下显著降低 token 或延迟；
- stale-memory error、Evidence F1 等机制指标显著改善。

### 当前实证状态（2026-08-14）

已落地的机制级强结果（`runs/` 产物，内容寻址报告）：
- 证据溯源覆盖率 100%（packed evidence 全部携带 `raw_turn_id`，确定性 span 定位闭环）；
- 0 分格修复 10→4（ETEC merge gate + 预算满装 packing）；
- LoCoMo 1986 题：记忆方法 142.2 vs full_context 4102.3 tokens/query（Δ −3959.9，p<0.001，约省 96.5%）；
- 33/33 失败人工复核：主因 answer_present_reader_wrong 26（reader 冗余措辞），真正检索/提取/预算失效仅 7/33。

未达成的门槛（如实记录，不伪装）：
- 无端到端 QA 增益声明：24 样本 `full` vs `vector_rag` 无正向显著差异（6m 报告 Δ −0.1667 为负，且受 run-to-run UUID 平局非确定性影响，见 `docs/STRONG_RESULTS_SMALL_SAMPLE.md` §6）；
- `etec` vs `event_no_etec` 在 single-session 切片 EM 逐题一致（Δ 0，CI [0,0]）；
- SUPERSEDE 与 temporal interval 排除尚未在任何切片触发（multi-session 切片仅 2 次 MERGE）。

结论口径：本项目的交付物是"机制证据链 + 可复现产物"，不是绝对分数竞争（竞品见 `docs/COMPETITIVE_ANALYSIS.md`）。
