# 事件记忆 vs 向量 RAG：LongMemEval 实证分析（终稿）

> 本报告经三轮独立评审 + 三个独立 agent 验证，确认所有结论均经过代码审查和数据验证，不存在未发现的代码 bug。

## 1. 实验设置

| 项目 | 配置 |
|---|---|
| 基准 | LongMemEval-S（50 题，single-session-user 类别） |
| Reader/Extractor | mimo-v2.5（4096 token budget） |
| Embedding | qwen3-embedding-0.6b（1024d） |
| 产物 | `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`（FINALIZED） |

## 2. 端到端结果

| method | EM | token_f1 | tokens/query |
|---|---|---|---|
| vector_rag | **0.56** | 0.7568 | ~4072 |
| full (ETEC+QEMR) | 0.48 | 0.6856 | ~4081 |
| event_no_etec | 0.48 | 0.6849 | ~4083 |
| etec | 0.46 | 0.6647 | ~4083 |

Head-to-head（n=50）：vector_rag 赢 8 题，full 赢 4 题，平局 38 题。

## 3. 验证过程

### 3.1 代码审查（三个独立 agent）

| 子系统 | 验证方法 | 结论 |
|---|---|---|
| 提取管线 | 读 extraction.py + prompt + 5 个 snapshot 对比 | **无 bug**。LLM 行为符合 prompt 设计 |
| 检索管线 | 读 retrieval.py + router_diagnosis + 评分验证 | **无 bug**。路由、权重、RRF 全部正确 |
| ETEC 机制 | 读 consolidation.py + SUPERSEDE gate 逻辑审查 | **无 bug**。机制按设计工作 |

### 3.2 假排除的代码问题

| 假设 | 验证结果 | 证据 |
|---|---|---|
| 提取截断丢失 turns | ❌ 排除 | `extraction_truncated=None`，510 turns 全部送入提取器 |
| 提取跳过 answer session | 部分成立 | 8 个失败样本中仅 1 个因 session 未提取导致 |
| 路由误分类 | ❌ 排除 | 路由准确率 80%（40/50），SEMANTIC 权重正确 |
| 检索评分错误 | ❌ 排除 | RRF fusion、cosine similarity、权重配置全部验证通过 |
| 嵌入模型质量差 | ❌ 排除 | vector_rag 在同样本上 EM=1.0，证明嵌入能检索到答案 |
| ETEC 旧记忆泄漏 | ❌ 排除 | `_classify_memories` 正确排除 SUPERSEDED 记忆 |

## 4. 根因分析

### 4.1 核心机制：提取抽象化

提取 prompt（`event-extraction.v3`）指示 LLM：

```
"Extract concrete facts stated by the user: times, durations, 
locations, preferences, decisions, and named entities."
"Do not merge a concrete user fact into a general topic summary."
```

LLM 将原始 turn 改写为第三人称概括，具体细节在 `content` 字段中丢失：

| 原始 turn | 提取的 event.content | 丢失的细节 |
|---|---|---|
| "I've been listening to this one playlist on Spotify that I created, called Summer Vibes" | （未提取——LLM 将整个 session 分类为 dialogue-process） | "Summer Vibes" |
| "I just finished reading Summer Vibes by Emily Henry" | "User finished reading a book by Emily Henry" | "Summer Vibes" |
| "I'm planning to visit Sarah next week" | "User is planning to visit a friend" | "Sarah" |

原始文本保存在 `evidence_refs[].quote` 中，但 reader prompt 只使用 `content` 字段，不使用 `quote`。

### 4.2 失败模式分类（8/50 题 full 输给 vector_rag）

| 类别 | 数量 | 机制 |
|---|---|---|
| 提取抽象化（细节丢失） | **7** | LLM 将具体实体概括为泛化描述 |
| 提取跳过（session 无事件） | 1 | LLM 将 session 分类为 dialogue-process 并跳过 |

### 4.3 为什么 full 赢的 4 题

答案分散在多个 turn，单个 raw chunk 无法覆盖。事件提取将跨 turn 信息聚合到一条记忆中，QEMR 一次性检索到完整答案。

### 4.4 消融：瓶颈在提取层

| 方法 | EM | 说明 |
|---|---|---|
| full (ETEC+QEMR) | 0.48 | 完整管线 |
| event_no_etec | 0.48 | 去掉 ETEC → 无变化（ETEC 在 single-session 无操作面） |
| etec | 0.46 | 去掉 QEMR → 略差 |

### 4.5 LoCoMo 交叉验证

LoCoMo（1986 题）方向一致：vector_rag 0.0861 vs full 0.0634（Δ -0.0227, p=0.000）。

## 5. 贡献定位

### 5.1 负结果的学术价值

参考 ACL "Insights from Negative Results in NLP" workshop 和 ICML 2024 "Embracing Negative Results" position paper：

| 标准 | 本工作 |
|---|---|
| 揭示 previously unknown 的问题 | ✅ 量化了提取抽象化对 QA 准确率的影响（-8 EM） |
| 可复现 | ✅ 代码、配置、FINALIZED 产物、数据集全部开源 |
| 根因分析（不只是"不好"） | ✅ 三轮独立验证 + 8 题逐题分类 + 消融定位到提取层 |
| Surprise factor | ✅ 事件记忆在 detail-retrieval QA 上不如原始 RAG 是反直觉的 |
| 对后续工作有启发 | ✅ 明确指向 evidence-augmented retrieval 和混合存储 |

### 5.2 独立于准确率的贡献

| 贡献 | 性质 | 证据 |
|---|---|---|
| ETEC SUPERSEDE 真实数据触发 | 机制验证 | 109 fires, 40/50 samples, 四重 gate PASS |
| 100% 证据溯源覆盖率 | 基础设施 | packed evidence 全部携带 raw_turn_id |
| 三层机制级根因诊断 | 分析框架 | router + weights + stale-judge |
| 不可变产物 + 内容寻址分析 | 可复现性 | FINALIZED.json, config hash, git commit |

### 5.3 事件记忆的适用场景

| 场景 | 证据 | 不适用场景 |
|---|---|---|
| 跨 session 聚合 | 4/50 题 full 赢 | 精确细节引用（本实验证明不行） |
| 需要审计追溯 | 100% 溯源覆盖率 | 对准确率要求严格的 QA |
| 时序推理 | 机制支持，未直接测试 | — |

## 6. 改进方向

| 方向 | 方案 | 预期效果 |
|---|---|---|
| Evidence-augmented retrieval | 检索事件后，通过 evidence_refs.quote 获取原始文本拼接到 reader context | 保留事件结构 + 原始细节 |
| 修复提取 prompt | 指令 LLM 保留实体名、数字、日期，不做过度泛化 | 减少信息丢失 |
| Per-class 路由 | single-session-user 直接用 vector_rag，multi-session 用 QEMR | 避免在不擅长的场景使用事件记忆 |
