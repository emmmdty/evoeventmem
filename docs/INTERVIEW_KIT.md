# EvoEventMem 面试资料包（Interview Kit）

> **一句话定位**：这不是一份"项目简介"，而是一份"决策能力证明"。
> 面试官不关心你写了多少行代码，关心你**怎么决策**——为什么这么选、考虑过什么替代方案、代价是什么、你怎么验证选择是对的。
> 本文档按"面试官提问逻辑"组织，先给结论，再给论据，你可以直接背、直接讲。

---

## 🎯 第一层：30 秒电梯陈述（先记住这个）

**中文**：
> 我做了一个框架无关的 Agent 长期记忆服务。它解决一个具体问题：Agent 的"记忆"会过期、会冲突、无法溯源。我的核心设计是"证据约束"——每条记忆必须指向精确的原始证据，合并或取代必须显式决策。为了验证这个设计，我在 LongMemEval（Long-term Memory Evaluation benchmark）和 LoCoMo（Long Conversation Memory benchmark）上做了完整评测，并实现了生产级部署（PostgreSQL/pgvector/多租户隔离）。

**英文**（备选）：
> I built a framework-agnostic long-term memory service for AI agents. It addresses three failure modes of agent memory: staleness, contradiction, and untraceability. The core design is "evidence constraint" — every memory must point to exact source evidence, and consolidation (merge/supersede) requires explicit decisions. I validated the design end-to-end on LongMemEval and LoCoMo benchmarks with production-grade deployment (PostgreSQL/pgvector, multi-tenant isolation).

**记忆要点**：问题（过期/冲突/不可溯源）→ 设计（证据约束+显式决策）→ 验证（双基准+消融）→ 工程（生产部署）。

---

## 🏗️ 第二层：关键决策清单（面试主战场）

> 面试官会从这里开始深挖。每个决策按统一模板讲：**背景 → 选项 → 为什么选这个 → 代价**。
> 一共 8 个决策，按重要性排序。每个都可以独立展开 5-10 分钟。

---

### 决策 1️⃣：为什么"事件记忆"而不是"向量 RAG"？

**背景**：Agent 记忆最常见做法是 RAG（Retrieval-Augmented Generation，检索增强生成）——把文档切片（chunk）做向量化存进向量库。

**选项对比**：
| 方案 | 能回答"现在是否有效"？ | 能处理事实冲突？ | 能溯源？ |
|---|---|---|---|
| 向量 RAG（chunk + embedding） | ❌ 所有 chunk 平等 | ❌ 新旧事实并存 | ⚠️ 弱（只能到 chunk） |
| 事件记忆（event-centric） | ✅ 有效区间（valid_from/valid_to） | ✅ 显式取代（supersede） | ✅ 精确到字符 span |

**为什么选事件记忆**：Agent 记忆的核心痛点不是"找不到"，而是"找到了过时的、冲突的、无法验证的信息"。向量相似度解决"语义相关"，解决不了"时间有效性和事实一致性"。

**代价**：事件提取需要额外处理（LLM 提取或规则提取），写入路径变重；而 RAG 写入很轻。

---

### 决策 2️⃣：合并决策用"显式 ADD/MERGE/SUPERSEDE/REJECT"而不是"ADD-only 叠加"？

**背景**：当时调研竞品 Mem0 的新算法明确采用 ADD-only（只新增、不删除不合并），靠检索排序天然淡化旧记忆。

**选项对比**：
| 方案 | 优点 | 缺点 |
|---|---|---|
| ADD-only（Mem0 做法） | 实现简单、无信息丢失风险 | 旧事实永远不会失效，靠排序掩盖问题 |
| 显式合并决策（ETEC: Evidence-Constrained Temporal Event Consolidation） | 旧事实被物理标记失效（superseded_by/valid_to），记忆库长期一致 | 决策错误会导致信息丢失 → 必须用证据约束兜底 |

**为什么选显式决策**：我们的研究主张是"证据约束能否防止错误合并"——**这个主张只有在显式决策系统里才能检验**。ADD-only 系统无法回答"合并决策做对了没有"。

**关键细节（加分点）**：我们设计了 `supersedes`（取代关系）+ `derived_from`（派生关系）字段，历史记录保留（superseded 的旧记忆不删除，只是失效），保证可回溯。

**代价**：合并阈值需要调优，证据冲突时可能过度拒绝（REJECT 太多）→ 用 `evidence_policy` 开关做消融来量化这个代价。

---

### 决策 3️⃣：检索用"查询自适应混合"（QEMR）而不是"固定权重融合"？

**背景**：多信号检索（向量+时序+图）融合有几种常见做法。

**选项对比**：
| 方案 | 机制 | 问题 |
|---|---|---|
| 固定权重加权（如向量 0.7 + 时序 0.3） | 所有查询同一套权重 | "When did X happen?"（时间问题）和"What is X?"（事实问题）用同一权重不合理 |
| 查询自适应（QEMR: Query-Adaptive Hybrid Retrieval） | 先路由查询意图（路由器），按意图用不同权重配置（weight profile） | 依赖路由器准确性 |
| 端到端可学习融合（learning-to-rank） | 训练排序模型 | 需要标注数据，且我们禁止"在测试集上调参" |

**为什么选 QEMR**：查询意图差异是记忆检索的核心特征——时间类问题（"什么时候"）应该让时序信号主导，事实类问题让语义信号主导。QEMR 是**先验声明权重、可解释、无训练数据依赖**的方案。

**关键细节（加分点）**：
- 权重配置（weight profiles）在跑基准**之前**声明，禁止事后调参（防止 overfitting 测试集）
- 路由器（router）是确定性规则（rule-based），不是学习模型——可解释、可测
- 我们用独立评测集验证路由器准确率（confusion matrix、Macro-F1），避免用开发集自证

**代价**：路由器出错会传导到检索；多一层延迟。

---

### 决策 4️⃣：检索融合用"加权 RRF"而不是"score 归一化后相加"？

**背景**：多源打分融合时，直接归一化（normalize）各源分数再相加是常见做法。

**关键细节（加分点）**：我们发现 per-source max-normalization 有个致命缺陷——**一个源只有 1 条弱相关结果时，会被归一化到 1.0，获得不该有的权威**。改用加权 Reciprocal Rank Fusion（RRF，倒数排名融合）：用排名（rank）而不是分数参与融合，天然免疫单源"矮子里拔将军"问题。

**为什么这是重要决策**：它体现了**"知道常见做法的坑在哪"**——面试官很看重这个。normalize-then-sum 是大多数人会写的，RRF 是知道问题后选的正解。

**代价**：丢失部分分数粒度信息（只有排名，没有分数强弱）。

---

### 决策 5️⃣：模型无关架构——LLM 只做"语义判断"，精确定位交给确定性算法？

**背景**：最初事件提取让 LLM 直接输出字符位置（start_char/end_char），发现模型输出不稳定（同一个输入，事件数从 0 到 169 随机波动）。

**选项对比**：
| 方案 | 问题 |
|---|---|
| 让 LLM 输出字符定位 | LLM 的字符定位能力差（长文本下退化严重），且输出随机性大 |
| LLM 输出语义内容 + 代码做确定性定位（`_exact_normalized_span` 大小写不敏感精确搜索） | ✅ 稳定 |
| 分块提取（30 turns/块） | ✅ 单次输出规模可控，随机性大降 |

**为什么这样设计**：这是**模型无关架构**（model-agnostic architecture）的核心原则——**LLM 做它擅长的事（语义理解），确定性算法做它擅长的事（精确计算）**。这保证换任何模型（甚至不用 LLM，用规则提取器）系统都能工作。

**面试价值**：这个决策可以讲成方法论故事——"我遇到不稳定 → 分析根因（不是模型问题，是任务分配问题）→ 重新划分职责 → 用数据验证（零事件率 90%→0%）"。**这比"我修了个 bug"高级一个维度**。

---

### 决策 6️⃣：存储用 PostgreSQL + pgvector 而不是专用向量数据库？

**背景**：向量检索的存储选项很多。

**选项对比**：
| 方案 | 优点 | 缺点 |
|---|---|---|
| 专用向量库（Milvus/Qdrant/Chroma） | 向量性能极致、功能全 | 引入额外基础设施、运维复杂 |
| PostgreSQL + pgvector 扩展 | 与业务数据同库（事务、关系、SQL 过滤）、单一基础设施 | 向量规模极大时性能不如专用库 |
| SQLite + 内存向量 | 极简 | 并发差、无生产能力 |

**为什么选 pgvector**：记忆服务是"小数据量、强关系、需事务"的典型场景——记忆记录本身（事件、证据、谱系）是关系型数据，向量只是其中一个索引维度。**一个库解决所有问题**，避免双写一致性（dual-write consistency）问题。而且 pgvector 支持 HNSW（Hierarchical Navigable Small World）索引，性能足够。

**代价**：记忆规模到千万级时可能需要迁移专用向量库（但这是优雅的扩展路径，不是推倒重来）。

---

### 决策 7️⃣：异步架构用 asyncpg 连接池而不是"线程包装同步库"？

**背景**：最初 PostgreSQL 接入是"事件循环线程 + 共享单连接"（把同步库塞进后台线程）。这是常见但错误的做法。

**选项对比**：
| 方案 | 问题 |
|---|---|
| 线程 + 共享连接 | 单连接串行化所有查询；线程切换开销；连接泄漏难排查 |
| asyncpg 连接池（asyncpg pool） | ✅ 原生异步、连接复用、并发查询并行 |

**为什么重写**：生产路径（FastAPI async 处理）必须用原生异步——线程包装在并发下是瓶颈且难调试。这个决策体现**"知道架构债，愿意重写而不是打补丁"**。

**加分细节**：我们保留了同步接口给研究代码用（sync `MemoryRepository`），新增 async 端口给生产用——**接口兼容演进，不是一刀切重构**。

---

### 决策 8️⃣：评测工程——"公平性"作为一等公民？

**背景**：Agent 记忆评测很容易作弊而不自知。

**关键决策清单**（这是我们的研究严谨性证明）：
- **无 oracle 泄漏（no oracle leakage）**：评测时官方答案绝不进入提取/检索输入；事件提取只用原始对话（raw turns）
- **统一预算（uniform budget）**：所有方法用同一 token 预算、同一 reader 模型、同一 tokenizer（token estimator）
- **共享提取快照（shared extraction snapshot）**：多个事件方法复用同一次提取结果，不重复调用模型
- **消融开关（ablation switches）**：每个模块都有独立开关（证据策略/时序/图/路由/权重/预算），可单独关闭验证贡献
- **不可变产物（immutable artifacts）**：跑批完成后 FINALIZED.json 锁死所有哈希，任何改动即失效；分析输出内容寻址（content-addressed）

**为什么这是面试王牌**：90% 的简历项目讲"我做了 X 提升 Y%"，但讲不出"我怎么保证 Y% 是真的"。**评测公平性控制是区分"会做实验"和"会做可信实验"的分水岭**。

---

## 🧩 第三层：指挥 AI 的能力（Agentic Engineering）

> 你问的"指挥 AI 做事"能力——这是 2026 年最值钱的能力标签，比"会写代码"高一个层级。术语：Agentic Engineering（代理工程）、AI Orchestration（AI 编排）、Agent Workflow Design（代理工作流设计）。这个项目就是活教材。

### 你在这个项目里实际指挥 AI 做的事（都是真实发生的）

**1. 任务分解（Task Decomposition）**
- 把大项目拆成 8 个 Task + 4 个并行工作流（workstreams A/B/C/D），按子系统（检索/基准/分析/生产）而不是按功能点划分
- 每个工作流一个独立 git worktree（Git 工作树，隔离开发环境），互不干扰

**2. 契约冻结（Interface Contract Freeze）**
- 在并行开发前，先冻结跨模块接口（TokenEstimator、检索 schema、manifest schema、scope 端口）
- **为什么重要**：4 个并行 worker 若各自改接口，合并时必然冲突。先定契约（contract），再各自实现——这是**软件工程里接口先行（interface-first）**的实践，面试官会懂

**3. 依赖顺序管理（Dependency Ordering）**
- 定义严格的依赖链：B 的压力测试 → A 的合并修复 → A 的策略冻结 → B 的最终实验 → C 的分析报告 → D 的生产集成
- 每个步骤有明确的"等待条件"和"验收门（approval gate）"

**4. 审批门（Approval Gate）机制**
- GPU 推理、线上模型调用、Docker 启动、发布跑批——全部需要"精确命令 + 预期效果 + 成本说明"的审批包（approval packet），**绝不自动执行**
- 这是**风险控制意识**：AI 会做，但"要不要做、花多少钱"是人的决策

**5. 验证优先（Verification-First）**
- 每完成一步，先跑验收命令（pytest/ruff/mypy/契约测试）再继续
- 用"只读清查（read-only reconnaissance）→ 基线保存 → 再动手"的流程保护已有资产

**6. 从失败中提炼方法论**
- 提取不稳定 → 不是换模型，而是**重新划分 LLM 与确定性算法的职责边界**
- 这体现的是"系统思维"：问题在架构，不在单点

### 面试怎么讲这块

**面试官问**："你一个人怎么完成这么大的项目？"

**回答**（用我们的真实流程）：
> 我是这个项目的 Integration Lead（集成负责人）。我的角色不是写所有代码，而是**设计工作流让多个 AI 子系统并行协作**：我定义接口契约、划分文件所有权、设置审批门、按依赖顺序合并。这教会我的是——**在 AI 时代，核心能力是把问题分解到 AI 能可靠执行的最小单元，并用工程机制（契约、验收、审批）保证可靠性**。这个项目的规模一个人不可能手写，但通过 agentic engineering（代理工程）方法论，一个人可以 orchestrating（编排）完成。

**术语补充**（面试时用对词）：
| 中文 | 英文全称 | 缩写 |
|---|---|---|
| 检索增强生成 | Retrieval-Augmented Generation | RAG |
| 长期记忆评测基准 | Long-term Memory Evaluation benchmark | LongMemEval |
| 长对话记忆基准 | Long Conversation Memory benchmark | LoCoMo |
| 证据约束时序整合 | Evidence-Constrained Temporal Event Consolidation | ETEC |
| 查询自适应混合检索 | Query-Adaptive Adaptive Hybrid Retrieval | QEMR |
| 倒数排名融合 | Reciprocal Rank Fusion | RRF |
| 分层可导航小世界索引 | Hierarchical Navigable Small World | HNSW |
| 代理工程 | Agentic Engineering | — |
| AI 编排 | AI Orchestration | — |
| 接口契约 | Interface Contract | — |
| 审批门 | Approval Gate | — |
| 集成负责人 | Integration Lead | — |

---

## ❓ 第四层：高频面试问答（直接背）

### Q1："和 Mem0 比，你的优势是什么？"

> Mem0 是一个 62.8k stars 的商业产品，我在调研后明确**不把它当竞品**，而是当研究对照（research baseline）。Mem0 的核心是 ADD-only（只叠加不合并），而我的研究主张是"证据约束能否防止错误合并"——这个假设在 ADD-only 系统里无法检验。我的系统有显式 ADD/MERGE/SUPERSEDE/REJECT 决策 + 证据参与决策的开关，可以量化"证据约束"的独立贡献。这是 Mem0 没有的实验能力。

**要点**：承认对方强 → 区分研究/产品目标 → 指出自己的独特实验能力。

### Q2："为什么不直接用现成的记忆系统，自己造轮子？"

> 两个原因。第一，研究目的：验证"证据约束"假设需要一个能开关证据策略的实验系统，商业产品是黑盒。第二，工程目的：完整实现记忆服务（存储/检索/合并/评测）本身就是能力证明——如果我用 Mem0，我学不到任何东西。

### Q3："你的消融怎么证明每个模块的贡献？"

> 六个独立开关：证据策略、时序源、图源、路由（规则 vs 强制）、权重策略、预算。每个开关只改变一个变量，其他全部固定，然后看决策差异（selection/exclusion/ranking）。结果（`runs/ablation/`，全部 finalized）：受控夹具六因子全部 active（Gate D 通过），LongMemEval 24 样本上六因子均产生决策变化（routing 22/24，其余 24/24）。诚实补充：这是**检索决策级诊断**，不是端到端 QA 增益；报告还如实渲染了 analysis 侧对 budget/evidence 等 arm 的 factor_leak 诊断（字段契约局限，不改变结论）。

### Q4："时间有效性（valid time）为什么向量解决不了？"

> 向量相似度回答"语义上像不像"，不回答"这件事现在是否成立"。比如"用户之前用 pnpm，现在改用 uv"——两条记忆语义上都相关，但只有带 valid_from/valid_to 和 supersedes 关系的存储才能知道"pnpm 那条已失效"。向量库存两条相似的 chunk，无法表达"一条取代了另一条"。

### Q5："LLM 提取不稳定你怎么解决的？"

> 根因不是模型，是任务分配错了——我最初让 LLM 输出字符定位，这是它不擅长的。修复分两层：一是确定性 span 定位（代码搜索证据文本，不依赖模型数位置），二是分块提取（30 turns/块）控制单次输出规模。结果：零事件率从 90% 降到 0%。这教会我：LLM 做语义判断，确定性算法做精确计算——模型无关架构的起点。

### Q6："多租户有必要吗？有真实用户吗？"

> 多租户是安全架构要求，不是 SaaS 尝试——记忆数据是敏感的（用户偏好、项目决策），隔离是默认设计。我用测试证明了隔离正确性（跨租户返回 404 不泄露存在性）。我没有真实用户，但独立部署和服务器迁移都实际做过。我把它定位为"工程完备性"，不是"产品功能"。

### Q7："你的性能数据？"

> 检索路径是纯数据库操作（pgvector HNSW），零 LLM 调用，毫秒级。写入路径的成本在 LLM 提取（可选规则提取器零成本）。效率数字有两组真实数据：LongMemEval 上所有方法都打满 4096 token 预算（等预算可比性成立）；LoCoMo 主 run（1986 题）上记忆方法 142 tokens/query vs full_context 4102 tokens/query（Δ −3959.9，p<0.001，约省 96.5% 输入 token）。诚实地说：精确的 p95 延迟还没测完，这是我要补的数据。**（诚实承认未完成项，不编数据）**

---

## 📊 第五层：项目状态诚实标注（面试前必读）

> 面试叙事成立的前提是"我知道什么完成了、什么没完成"。以下标注必须诚实，**不要**把未完成当已完成讲。

| 模块 | 状态 | 面试时可讲 |
|---|---|---|
| 核心算法（ETEC/QEMR/路由器） | ✅ 完成 + 单元测试 | 设计决策、机制、测试 |
| 提取方法（分块+确定性定位） | ✅ 完成 + 验证 | 方法论故事（90%→0%） |
| 生产部署（pgvector/async/多租户/fail-closed） | ✅ 完成 + Compose/PG 集成测试 | 架构选型、隔离证明 |
| 评测工程（无泄漏/统一预算/不可变产物） | ✅ 完成 | 公平性控制方法论 |
| LongMemEval 跑批 | ✅ 完成（24 样本小样本闭环；机制级强结果） | 机制证据链（见 `docs/STRONG_RESULTS_SMALL_SAMPLE.md`） |
| LoCoMo 跑批 | ✅ 完成（1986 题主 run） | 效率证据（~96.5% token 节省） |
| 6 因子消融 | ✅ 完成（`runs/ablation/` 全部 finalized） | 决策级诊断 + factor_leak 诚实披露 |
| 最终分析报告 | ✅ 完成（3 份内容寻址 M15 报告，validate valid=true） | 配对 bootstrap、失败归因 33/33 复核 |
| p95 延迟测量 | ⏳ 未做 | 讲为什么还没做 |

**面试纪律**：任何"提升 X%"的说法，必须来自跑批完成后的真实数据。当前没有端到端 QA 增益声明——24 样本上 `full` vs `vector_rag` 无正向显著差异（6m 报告 Δ 为负，且受 run-to-run 非确定性影响）；能讲的是机制级强结果（溯源 100%、0 分修复 10→4、效率 96.5%、归因 33/33）。定位是"机制证据链 + 可复现产物"，不是"分数更高"。

---

## 🎤 第六层：面试表达技巧（加分项）

1. **先结论后论据**：每个回答先给一句话结论（30 秒内），再展开。面试官可能只给你 2 分钟。
2. **用对比讲故事**：永远给"我考虑了 A/B/C，选了 B，因为…，代价是…"——这是架构师思维的样子。
3. **承认未知**：不知道就说"这个我还没测/没验证，我的计划是…"——比编造可信得多，也符合研究者的诚实。
4. **术语准确**：能用全称和缩写（见第三层术语表），显示你不是背概念。
5. **准备一张图**：手绘系统架构图（写入→提取→ETEC→存储→检索→QEMR→reader），面试时画出来，胜过千言。
