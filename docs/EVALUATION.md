# 评测协议

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
