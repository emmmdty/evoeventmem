# 简历叙事与面试应对手册

> 本文档回答"作为简历项目，EvoEventMem 怎么立得住、怎么经得起面试拷打"。基于真实工程事实与竞品情报，所有叙事均可被代码/实验验证，不夸大。

## 1. 一句话简历版本（30 秒电梯陈述）

**中文版**：
> 设计并实现了一个框架无关的 Agent 时序事件记忆服务（EvoEventMem），含两个研究贡献：ETEC（证据约束时态整合，显式 ADD/MERGE/SUPERSEDE/REJECT 决策）和 QEMR（查询自适应混合检索，向量+时序+图）。整改 S0→S5 闭环后定位**分支 C 中间路线**：ETEC 的 SUPERSEDE 在真实 LongMemEval 数据上**可达**（109 fires across 40/50 samples，第一次在真实数据触发，四重 gate 可达性 PASS，replay/online 一致）但**不足以提升整体准确率**（`full` flagship EM=0.48 仍低于 `vector_rag`=0.56，Δ −0.08）。S3 三层机制级根因诊断定位到 router 误路由（500 题准确率 38% < 80% 阈值，**可修复**，future-work）+ operating surface 太窄（M2 stale-judge 74% tie，**结构性**——single-session-user 无时间显著性答案可改 reader 可见值），排除权重 profile（`qemr` 0.48 ≥ 全部消融臂）与 SUPERSEDE 消费（0% full-stale）。正面贡献是基础设施级：100% provenance 覆盖率、33/33 失败归因、不可篡改 FINALIZED.json、三层机制级根因诊断。**不声称翻盘 / ETEC 有效 / QEMR 有效**。详见 `docs/REMEDIATION_FINAL_REPORT.md`。

**英文版**：
> Built a framework-agnostic temporal event-memory service (EvoEventMem) with two research contributions: ETEC (evidence-constrained temporal consolidation with explicit ADD/MERGE/SUPERSEDE/REJECT decisions) and QEMR (query-adaptive hybrid retrieval across vector/temporal/graph signals). After the S0→S5 remediation closure, the honest framing is **branch C (intermediate route)**: ETEC's evidence-constrained SUPERSEDE is **reachable on real LongMemEval data** (109 fires across 40/50 samples, first time on real data, four-gate reachability PASS, replay/online consistent) but **insufficient to lift overall `full` EM above `vector_rag`** (0.48 vs 0.56, Δ −0.08). S3's three-layer mechanism-level root-cause diagnosis localized the failure to router mis-routing (full-500 accuracy 38% < 80% threshold, **fixable**, future work) + operating-surface narrowness (M2 stale-judge 74% tie, **structural** — single-session-user questions have no temporal-salient answer for consolidation to change), ruling out the weight profile (`qemr` 0.48 ≥ all ablation arms) and SUPERSEDE consumption (0% full-stale). Infrastructure contributions: 100% provenance coverage, 33/33 failure attribution, tamper-proof FINALIZED.json, three-layer mechanism diagnosis. Does **not** claim thesis翻盘 / ETEC有效 / QEMR有效. See `docs/REMEDIATION_FINAL_REPORT.md`.

## 2. 差异化叙事的三个支柱（都经得起拷问）

### 支柱一：证据约束是硬约束（独特主张）

**讲什么**：
- 每条记忆必须携带**精确到字符 span 的原始证据**（`evidence_refs` + `locator=chars=X:Y`）
- 无法通过确定性校验的记忆**被拒绝写入**（REJECT 决策或 rejection 记录）
- 提取的字符定位是**确定性算法**（`_exact_normalized_span`），不是模型输出——模型只提供语义内容

**为什么经得起拷问**：面试官问"证据有什么用？"→ 答：可审计性（每条记忆可查回源头）+ 约束合并（证据冲突时拒绝合并，防止错误事实融合）。这是 Mem0 ADD-only / Zep 边失效都没有的机制。

**潜在的拷问**："证据约束会不会降低召回？"→ 诚实答：会。这正是我们要**量化**的权衡——evidence_policy 消融因子就测这个（runs/ablation 中六因子全部产生决策变化，且报告如实渲染 factor_leak 诊断）。承认 trade-off 比假装完美更可信。

**已落地的机制证据**（可引用）：
- 证据溯源覆盖率 100%：LongMemEval 24 样本三方法 packed evidence 全部携带 `raw_turn_id`（r2/6m/ms run）；
- 确定性 span 定位修复在机制层闭环（对照 LoCoMo 旧缺陷 0/23434 条带 turn 引用）；
- 0 分格修复 10→4：ETEC merge gate（不同 fact_value 不再合并）+ 预算满装 packing；
- 33/33 失败人工复核：主因是 reader 精确输出（26/33），真正检索/提取/预算失效仅 7/33。

### 支柱二：模型无关的确定性内核（回应"DeepSeek bug"）

**讲什么**：
- 记忆核心（合并、检索、去重、span 定位、冲突仲裁）是**纯确定性代码**
- LLM 只出现在两个可插拔点：事件提取（有规则版 `RuleEventExtractor` 零 LLM 替代）和评测 reader
- 提取的稳定性问题（LLM 超长输出随机性）通过**方法论修复**解决：分块提取 + 确定性 span 定位，而非换模型

**为什么经得起拷问**：面试官会问"为什么依赖 DeepSeek？"→ 答：**不依赖**。LLM 是可替换组件（接口抽象），核心算法零 LLM；跑批用 DeepSeek 是成本/可用性选择。提取不稳定的问题我们通过算法修复（分块+确定性定位）而不是换供应商解决——**这恰恰证明了框架无关性**。

### 支柱三：研究级评测工程（证明方法论成熟度）

**讲什么**：
- 双基准（LongMemEval Small + LoCoMo）全流程：提取→检索→合并→评测→分析
- 评测公平性控制：统一 token 预算、无 oracle 泄漏、`vector_rag` 只索引原始对话、事件方法共享同一提取快照
- 消融矩阵：证据策略/时序源/图源/路由/权重/预算 6 因子
- 失败分类 + 人工复核交接表（50+ 失败样本带 reviewer 字段）
- 产物不可变（FINALIZED.json + 内容寻址 analysis_id）

**为什么经得起拷问**：大多数简历项目没有"我保证我的评测是对的"这个意识。讲出"我们拒绝 oracle 泄漏、统一预算、消融验证"直接区分于 90% 的候选人。

## 3. 面试拷问题库与应对（面试官视角）

### Q1："Mem0 在 LongMemEval 94.4，你多少？"

**应对**：
1. 不回避：承认绝对分数低于 Mem0（Mem0 是 62.8k stars 的商业产品，我们是独立实现）
2. 转定位：我们的研究主张不是"分数更高"，而是"证据约束能否防止错误合并"——这是一个 Mem0 没有做过的实验（Mem0 是 ADD-only，不做合并决策）
3. 证据：六因子消融（`runs/ablation/` 全部 finalized，受控夹具六因子全部 active）+ `etec` vs `event_no_etec` 对比（single-session 切片 EM 逐题一致，如实报告）+ 机制级强结果（溯源 100%、0 分修复 10→4、失败归因 33/33 复核）。**诚实效率数字**：vs `vector_rag`（公平基线）`full` 贵 41% 且 EM 更低；vs `full_context`（trivial 基线）省 96.5% 输入 token（仅供参照）。
4. 收尾：如果我要分数，我可以用更强的 reader 模型（Mem0 用生产级模型栈），但我们的目标是机制分析

### Q2："为什么不用现成的 Mem0/Zep，自己造轮子？"

**应对**：
- 研究目的：测试"证据约束"假设需要**可控的实验系统**（能开关证据策略、能消融合并决策），商业产品是黑盒
- 工程目的：完整实现一个记忆服务的全部环节（存储/检索/合并/评测）本身是能力证明
- 借鉴声明：我们调研了 Mem0（arXiv:2504.19413）、Zep、TencentDB，他们的设计验证了方向，但都没有"证据参与合并决策"这个开关

### Q3："你的创新点是不是已经被别人做了？"

**应对**（诚实+区分）：
- 部分重叠：时序检索（Mem0 有 temporal reasoning）、多信号融合（Mem0 有）——**承认**
- 独特部分：**证据约束参与合并决策**（ETEC 的 evidence_consistency 评分 + REJECT 决策）——没有竞品做
- 我们为此做了对照实验（证据开/关），这是区分点

### Q4："提取为什么用 DeepSeek？为什么之前不稳定？"

**应对**（用我们的真实教训，讲成方法论故事）：
- 记忆核心零 LLM；提取是可选组件（有规则版）
- 我们发现 LLM 超长输出不稳定（同一输入 0-169 事件随机）→ 根因是"让模型做字符定位"的设计缺陷，不是模型问题
- 修复：分块提取（30 turns/块）+ 确定性 span 搜索（`_exact_normalized_span`）→ 0 事件率从 90% 降到 0%
- **教训提炼**：LLM 做语义判断，确定性算法做精确定位——这是模型无关系统的设计原则

### Q5："多租户有必要吗？有真实用户吗？"

**应对**：
- 多租户是安全架构要求（记忆数据敏感），不是 SaaS 尝试
- 隔离正确性有测试证明（跨租户 404 不泄露存在性）
- 独立部署实际做过（容器化 + 服务器迁移 + 长期跑批稳定）
- 诚实：没有真实用户，但部署/运维/隔离的工程环节全部真实执行过

### Q6："速度怎么样？会不会太慢？"

**应对**：
- 检索路径：pgvector HNSW + 纯 SQL，**零 LLM 调用**，ms 级（有 EXPLAIN 验证）
- 写入路径：LLM 提取是主要成本（可选异步/规则提取器零成本）
- 竞品对照：Mem0 p50 0.88-1.09s（含 LLM 检索），我们的检索路径无 LLM 依赖
- 诚实：p95 延迟数字还没测，这是待补数据（如果时间允许可以测）

### Q7："你的结果提升来自哪个模块？"（消融追问）

**应对**：ablation 已跑完，**但不能声称端到端 QA 提升**——24 样本上 `full` vs `vector_rag`
无正向显著差异，机制诊断也没有给出"XX 因子提升 QA"的证据。诚实回答模板：
- "六因子消融（证据/时序/图/路由/权重/预算）已全部跑完：受控夹具六因子全部 active，LongMemEval
  切片上均产生检索决策变化（routing 22/24，其余 24/24）。但这是**决策级诊断**，不是 QA 增益；
  报告同时如实披露了 factor_leak 诊断。"
- "真正的强结果是机制级的：溯源覆盖率 100%、0 分格修复 10→4、失败归因 33/33 复核。效率数字诚实标注：vs `vector_rag`（公平基线）`full` 贵 41% 且 EM 更低；vs `full_context`（trivial 基线）省 96.5% 输入 token（仅供参照）。"
- 用实际数据说话，绝不编造"XX 提升 Y%"。

## 4. 目前必须补齐的证据（决定叙事是否成立）

| 证据 | 状态 | 缺失影响 |
|---|---|---|
| LongMemEval 结果 | ✅ 已完成（24 样本小样本闭环；500 样本降级为一致性验证，见 `docs/METHODOLOGY_CHANGE.md`） | 无 |
| LoCoMo 结果 | ✅ 已完成（1986 题主 run，`runs/main/report`） | 无 |
| 6 因子消融 | ✅ 已完成（`runs/ablation/controlled` + `longmemeval-test20`，六因子全部 active） | 无 |
| 失败分类 + 复核 | ✅ 已完成（33/33 人工复核，`runs/review/longmemeval-r2.reviewed.jsonl`） | 无 |
| p95 延迟 | 未测 | Q6 回答不了（检索路径零 LLM，可讲架构不承诺数字） |
| 提取质量数字 | ✅ 已有（24 样本 172–292 事件/样本，0 零事件） | 可先讲 |

> 注意：小样本闭环是**机制级强结果**（修复验证、机制指标、效率、失败归因），不是显著性声明；
> `full` vs `vector_rag` 在 24 样本上无正向显著差异（6m 报告 Δ −0.1667 为负，且受 run-to-run
> 非确定性影响，见 `docs/STRONG_RESULTS_SMALL_SAMPLE.md`）。叙事重心是"机制证据链 + 可复现产物"，
> 不是"分数更高"。

## 5. 简历最终表述（推荐）

**项目名**：EvoEventMem —— 证据约束的 Agent 时序事件记忆服务

**要点**：
1. 实现框架无关记忆服务：事件存储（证据+有效区间+合并谱系）、查询自适应混合检索（向量+时序+图）、显式合并决策（ADD/MERGE/SUPERSEDE/REJECT）。
2. 两个研究贡献：ETEC（证据约束时态整合）+ QEMR（查询自适应混合检索）。整改 S0→S5 闭环，**分支 C 中间路线**定位：ETEC 的 SUPERSEDE 在真实 LongMemEval 数据上**可达**（109 fires across 40/50 samples，第一次在真实数据触发，四重 gate 可达性 PASS，replay/online 一致）但**不足以提升整体准确率**（`full` flagship EM=0.48 仍低于 `vector_rag`=0.56，Δ −0.08）。
3. S3 三层机制级根因诊断定位根因：router 误路由（500 题准确率 38% < 80% 阈值，**可修复**，future-work，N9 scope）+ operating surface 太窄（M2 stale-judge 74% tie，**结构性**——single-session-user 无时间显著性答案可改 reader 可见值）；排除权重 profile（`qemr` 0.48 ≥ `no_temporal`/`no_graph`/`uniform` 全部消融臂）与 SUPERSEDE 消费（0% full-stale）。
4. 基础设施级贡献：溯源覆盖率 100%、33/33 失败归因复核、不可篡改 FINALIZED.json、三层机制级根因诊断、离线 `deterministic_fake` 复现配置（`configs/longmemeval/offline10.toml`，benchmark 运行零网络调用）。
5. 工程完备：FastAPI + PostgreSQL/pgvector + 多租户隔离 + fail-closed 降级 + Docker Compose + 独立部署（服务器迁移实战）。
6. 方法论教训：LLM 语义判断 + 确定性精确定位（分块提取修复 90%→0% 零事件率）。

**诚实红线**：**不声称翻盘 / ETEC 有效 / QEMR 有效**——分支 C 是"可达但不足以提升"。整改定稿见 `docs/REMEDIATION_FINAL_REPORT.md`，S3 根因见 `docs/QEMR_FAILURE_DIAGNOSIS.md`，独立审查见 `docs/STAGE4a5_REVIEW.md`。**不跨模型对比**：v1 vs v2 都用 mimo-v2.5（同 4096 预算，可对比）；24 题 deepseek-v4-flash run 已停服、禁止与 mimo-v2.5 对比（AGENTS.md N8）。
