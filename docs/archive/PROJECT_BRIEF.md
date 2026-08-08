# 项目立项：EvoEventMem

## 1. 一句话定义

EvoEventMem 是一个框架无关的 Agent 长期记忆服务：把对话与执行轨迹转为带证据、时间有效性和版本关系的事件记忆，并通过查询自适应的混合检索，为下游 Agent 提供紧凑、可解释、不过期的上下文。

## 2. 招聘视角下的项目价值

### Agent 算法

- 明确的问题定义：重复、冲突、过期、多跳和时间推理。
- 两个可独立消融的方法模块：ETEC 与 QEMR。
- 两个公开主基准：LongMemEval、LoCoMo。
- 中间指标：事件合并、冲突更新、证据定位和检索排序。

### Agent 开发

- 独立 FastAPI/MCP 服务；
- API 与本地 OpenAI-compatible 模型双后端；
- PostgreSQL/pgvector、缓存、异步写入与可观测性；
- OpenCode 适配和故障降级；
- 可复现实验与 CI。

## 3. 核心研究假设

H1：与纯向量记忆相比，显式事件、时间有效区间和 supersedes 关系可以减少过期事实被召回。

H2：与固定检索策略相比，按查询类型动态组合 dense、temporal、graph、episodic 和 procedural 信号，可在相同 token budget 下提高回答与证据质量。

H3：证据绑定不仅提升可解释性，也能约束错误合并和冲突更新，从而改善记忆库的长期稳定性。

## 4. 方法贡献

### ETEC

Evidence-Constrained Temporal Event Consolidation：

1. 候选实体/事件召回；
2. 语义、实体、角色、时间与证据一致性评分；
3. 输出 ADD、MERGE、SUPERSEDE、REJECT；
4. 更新 valid_from/valid_to 与 provenance；
5. 保留完整决策日志。

### QEMR

Query-Adaptive Event Memory Retrieval：

1. 查询类型识别；
2. 生成 dense、temporal、graph、episode、procedure 候选；
3. 按查询类型重加权；
4. 去冗余和证据覆盖约束；
5. 在固定 token budget 下完成 context packing。

## 5. 明确非目标

- 不从零训练基础模型；
- 不重新实现 OpenCode/Pi；
- 不在主线中做 Agentic RL；
- 不把多 Agent 共享记忆、复杂前端、Neo4j 和 Skill 自进化同时塞入第一版；
- 不使用私有聊天记录作为公开评测数据。

## 6. 12 周里程碑

| 周 | 交付 |
|---|---|
| 1 | 脚手架、数据下载、规范化 fixture |
| 2–3 | 评测器、No Memory、Full Context、Vector RAG |
| 4–5 | 事件抽取、证据绑定、存储 schema |
| 6 | ETEC v1 与中间标注集 |
| 7 | QEMR v1 与固定预算检索 |
| 8–9 | LongMemEval、LoCoMo 主结果与消融 |
| 10 | 错误分析、效率和成本分析 |
| 11 | OpenCode MCP、生产化 API、追踪 |
| 12 | 复现脚本、Demo、技术报告、简历材料 |
