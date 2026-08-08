# 竞品分析与生态位定位

> 本文档是 EvoEventMem 的竞争情报基线。所有竞品数据均来自各项目官方 README、论文或官方文档，截至 2026-08。引用数据标注来源，未经验证的推测不写入本文档。

## 1. 记忆系统竞争版图（2026-08）

Agent 长期记忆已成为 LLM Agent 架构的"基础组件"（腾讯官方表述："AI 的记忆层正在从可选插件变成 Agent 架构里绕不过去的基础组件"）。当前主要玩家分四类：

### 1.1 商业/开源记忆层（直接竞品）

| 项目 | 规模 | 定位 | 核心技术 | 基准数据（官方） |
|---|---|---|---|---|
| **Mem0**（mem0ai） | 62.8k stars, YC S24 | 通用 Agent 记忆层 | ADD-only 提取、实体链接、多信号检索（语义+BM25+实体+时序）、单次检索 | LoCoMo 92.5（+21）、LongMemEval 94.4（+27）、BEAM(1M) 64.1；p50 延迟 0.88-1.09s，单次检索 6.8-7.0K tokens（2026-04 新算法，managed platform） |
| **Zep** | 企业级 | 时序知识图谱记忆 | Context Graph（实体/关系/事实边）、Fact Invalidation、Context Block、<200ms 检索 | 未公开统一基准 |
| **Letta（前 MemGPT）** | 24.1k stars | 有状态 Agent 平台 | 自演进记忆层级、Agent 内存管理 | 未公开统一基准 |
| **TencentDB Agent Memory** | 17.8k stars, MIT | 团队级记忆中枢 | L0→L3 语义金字塔、四种记忆资产（Chat Memory/Skill/Wiki/CodeGraph）、Fixed Binding + ACL | PersonaMem 48%→76%（+59% 相对提升）；Token 节省最高 61.38%，通过率相对提升 51.52%（官方发布稿） |

### 1.2 学术/研究记忆系统

- **LongMemEval**（斯坦福等）：Agent 长时记忆评测基准，定义五类能力（跨会话一致性、时间理解、知识更新、多跳推理、拒绝回答）
- **LoCoMo**（Snap Research）：长对话记忆评测，含 temporal/recency/conflict 等细分类目
- **EvoMemBench**（DSAIL）：记忆演化评测——持续变化场景下的记忆更新
- **Mem0 论文**（arXiv:2504.19413）：明确提出"图记忆表示捕捉关系结构"，并论证 vs 全上下文 91% 更低 p95 延迟、90%+ token 节省

### 1.3 Agent 运行时内置记忆（间接竞品/互补品）

- **Claude Code / Codex / opencode 的 AGENTS.md、CLAUDE.md、skills**：项目级静态知识，Agent 自己维护
- 这些解决"项目是什么样"，不解决"事情的状态随时间怎么变"

### 1.4 结论：生态位正在被占位

**残酷事实**：我们宣称的两个创新点（时序有效记忆 + 查询自适应混合检索）已被竞品以不同形式实现并公开验证：
- Mem0 2026-04 新算法明确含 "Temporal Reasoning —— time-aware retrieval that ranks the right dated instance" 和 "Multi-signal retrieval fused"
- Zep 核心就是时序知识图谱 + Fact Invalidation
- TencentDB Agent Memory 有 L0→L3 分层 + 三路检索注入 + 冲突仲裁

## 2. EvoEventMem 的差异化定位

### 2.1 不可竞争的领域（诚实承认）

- **基准分数**：Mem0 在 LoCoMo 92.5 / LongMemEval 94.4，我们预计远低于此。我们不试图在"绝对分数"上竞争。
- **工程完备度**：TencentDB 有完整 Memory Hub 面板、ACL、四种资产；Mem0 有全平台 SDK。我们不竞争团队协作管理功能。

### 2.2 我们真正的差异化（可辩护的）

| 维度 | 竞品现状 | EvoEventMem |
|---|---|---|
| **证据约束强度** | Mem0/Zep/TencentDB 的证据是可选元数据；无法通过校验的记忆仍可入库 | **证据是硬约束**：每条记忆必须携带精确到字符 span 的原始证据（`evidence_refs` + `locator=chars=X:Y`），无法通过确定性校验的记忆**被拒绝写入**（REJECT）或转入 rejection 记录 |
| **合并/取代决策的显式化** | Mem0 是 ADD-only（不做 UPDATE/DELETE，靠检索排序解决）；Zep 是边失效 | **ETEC 显式输出 ADD/MERGE/SUPERSEDE/REJECT 四类决策**，且**证据一致性参与决策**（证据冲突→REJECT，证据支持→MERGE/SUPERSEDE） |
| **研究严谨性** | 竞品主要报告"提升"，多数不披露评测公平性控制 | **我们拒绝 oracle 泄漏、统一 token 预算、消融验证、逐失败分类**——按学术标准评测 |
| **模型无关的确定性内核** | 竞品核心依赖 LLM 判断 | 检索/合并/去重/span 定位是**确定性算法**；LLM 只做提取（可替换为规则提取器） |
| **多租户严格隔离** | TencentDB 是团队级（共享池 + ACL） | **tenant/user/session 三级强制隔离**，UUID 查询必须带 scope，跨租户不泄漏存在性 |

### 2.3 一句话定位

> **EvoEventMem 是"证据约束的时序事件记忆"**：不追求"记住更多"，而追求"记住的每条都可审计、不冲突、不过期"。与 Mem0 的"增量叠加"哲学相反，我们主张"显式合并/取代 + 证据仲裁"能长期维持记忆库一致性。

这是一个**可检验的研究主张**（research thesis），不是产品竞争主张。

## 3. 研究假设的可检验性（面试核心弹药）

### H1：显式 supersedes 可以减少过期事实召回
- **验证方式**：LoCoMo 的 knowledge-update 类问题子集（新旧事实冲突时检索到哪条）；`etec` vs `event_no_etec` 消融
- **竞品对照**：Mem0 用 ADD-only + 时序排序近似解决，我们主张"物理取代 + 历史保留"更优

### H2：查询自适应混合检索优于固定策略
- **验证方式**：`qemr` vs `fixed_hybrid` vs `fixed_vector` 消融；router 消融（规则 vs 强制）
- **竞品对照**：Mem0 也做多信号融合，但我们的差异是**按查询意图动态加权**（QEMR weight profiles），且权重先验声明不事后调参

### H3：证据绑定能约束错误合并
- **验证方式**：`evidence_policy=constrained` vs `provenance_only` 消融——证据参与决策 vs 仅保留溯源
- **这是 Mem0/Zep/TencentDB 都没有做的实验**（它们没有"证据参与决策"的开关）

### 4. 生态位结论

我们处于**"研究驱动的证据约束记忆"**生态位：
- 不上不下：不碰团队协作/产品化（TencentDB 主场），不碰绝对分数（Mem0 主场）
- 独特点：**证据约束作为一等公民**——这是学术上未充分探索、商业上无人强制的方向
- 支撑材料：两个公开基准（LongMemEval/LoCoMo）+ 消融矩阵 + 失败分类 + 独立实现

**如果面试官问"和 Mem0 有什么区别"，答案骨架**：
1. 承认 Mem0 是成熟产品，我们不是竞品，是研究对照
2. 我们的主张是"H3：证据约束能防止错误合并"——Mem0 的 ADD-only 恰恰**不做**合并决策，我们的 ETEC 显式决策 + 证据仲裁是它没有的
3. 我们愿意被消融检验：每个模块有开关、有对照、有失败分类
