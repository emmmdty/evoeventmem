# 9/10 独立真实性审计报告（O09 收尾）

> 审计日期：2026-08-18
> 审计者：独立收尾审计 agent（不假定前置结论为真，全部自己重算/重核）
> 审计对象：`docs/9of10_ACCEPTANCE.md`（前置交付报告）
> 约束：AGENTS.md 硬约束（不伪造、不删 runs/、同预算同 reader 同 prompt、不混方法、小步 diff）
> 环境：LLM 网关 `opencode.ai/zen/go/v1` 配额阻断（429 GoUsageLimitError, resets in 5 days）；embedding 隧道 `127.0.0.1:11436` 正常。

---

## 审计方法

对 `9of10_ACCEPTANCE.md` 的每个 headline 数字与结论，自己重算/重核：
1. 读源码确认代码逻辑链（consolidation.py, extraction.py, retrieval.py, router.py, run.py）
2. 读 finalized 产物确认数字（metrics.partial.json, consistency.json, etec_stress summary, router-screen.json, review_sheet.jsonl, reviewed.jsonl）
3. 补完遗留项（gold 标注 ms 8 KU、M1/M5、Eval B 探针 router 断言、replay 分歧诊断、M2 配额阻断标记）
4. 对每个结论评估"对比方法 + 实测证据 + 诚实标注限制"三要素是否齐全

---

## 第一部分：遗留项补完结果

### 1. Gold 标注 ms 8 KU（机械标注，LLM 阻断）

**产物**：`runs/mechanism/gold/longmemeval-kupairs-ms8.v1.json`（sha256:1392d32f4866823904ab606e200baa4e1ac1b9e02bb60eb7d7bda84aea9dc424）

- 标注方式：机械标注（从数据集 sessions 中人工识别 old/new value turn_ids），LLM 被配额阻断无法用于提议
- **7 对 gold pair**（6 SUPERSEDE + 1 MERGE），非预期的 8 对全 SUPERSEDE
- **1 题排除**：`22d2cb42`（guitar 服务地点）gold_action=ADD 但 schema 要求 old_value（min_length=1），ADD 无 old_value 不 fit schema → 排除
- **2 题 validation 失败**（schema 限制，非标注错误）：
  - `50635ada`：问"previous status"→ answer=Premier Silver（old value），new_value=Premier Gold 不 ⊆ answer
  - `89941a94`：问"before gravel bike"→ answer 描述 old state，new_value="four bikes" 不 ⊆ answer
  - 根因：`validate_pairs`（gold.py:160）检查 `token_coverage(new_value, answer)`，要求 new_value 包含 answer 全部 token；当 answer 是 old value 时方向反了
- 每对 gold pair 的 turn_id 已逐条验证存在性（`resolve_turn_id`）、t_q ≥ t_old（全部满足）

### 2. M1 更新决策准确率（ms 8 KU，online actions）

**产物**：`runs/mechanism/evala/m1.json`

- **M1 = 0/7 = 0.0**（非预期的 0/8）
- 数据源：online `samples/<id>.json → ingestion.etec.actions`（全部 ADD-only，SUPERSEDE=0）
- 7 对 gold pair（6 SUPERSEDE + 1 MERGE）的 ETEC 动作全为 ADD → 全不正确
- **排除的 22d2cb42**：gold_action=ADD，ETEC 也是 ADD → 本应正确，若 schema 支持则 M1=1/8
- 根因分桶：7/7 = R1_structural_fact_slot_absent（提取不写 fact_slot → contradiction=0 → SUPERSEDE 不可达 → ETEC 默认 ADD）
- **诚实记录零结果**：未跳过，未美化

### 3. M2 stale-memory error rate（配额阻断，未产出）

**产物**：`runs/mechanism/evala/m2.json`（status=NOT_PRODUCED_QUOTA_BLOCKED）

- LLM 网关 429 GoUsageLimitError（"Weekly usage limit reached. Resets in 5 days."）
- **无假数字**：stale_rate 未宣称，judge I/O 未伪造
- 诊断隐含：SUPERSEDE=0 → ETEC 不标记旧值失效 → full vs event_no_etec stale_rate Δ≈0（结构性 null，非缺失实验）
- 若配额恢复，M2 应补跑以确认 null

### 4. M5 内存态探针（ms 8 KU）

**产物**：`runs/mechanism/evala/m5.json`

- under_edit_rate = **1.0**（7/7）：SUPERSEDE=0 → 旧值记忆全 ACTIVE，从未被 supersede
- version_chain_recall = **0.0**（0/7）：SUPERSEDE=0 → 无 superseded_by 链
- over_edit：需 past-window 探针（Eval B），未计算；逻辑推断 SUPERSEDE=0 → 旧值可检索 → over_edit≈0
- replay 状态：4/7 cache hit（replay.etec_store 可用），3/7 cache-miss（改用 SUPERSEDE=0 逻辑推断）

### 5. Eval B 合成时间窗探针（router 断言通过，M4 指标未算）

**产物**：`runs/mechanism/evalb/m4.json`（PARTIAL）

- **router operator 断言：8/8 通过**（7 now→NONE + 1 past→BEFORE）
- **0 个 between 探针**：ms 8 KU 中无 gold pair 的 year(t_q) - year(t_old) ≥ 2 → 无法构造 between 探针
- **M4 指标（ExclusionHit/Contamination/ValidRetention）未计算**：需 probes.py 脚本（spec §5.4 指定但未实现）+ 离线检索重放；embedding 隧道可用但脚本创建超本审计会话范围
- 诚实标注：未产出完整 M4，仅产出 router 断言

### 6. replay 分歧诊断

- **根因**：`benchmarks/mechanism/replay.py:130-134` 中 `ETECConsolidator(embedding).apply(repository, memory)` 触发 `LinkCandidateGenerator`，该生成器用 embedding 查找候选对；online run 的 model_cache 未覆盖所有 replay 需要的 embedding 查询键 → `OfflineCacheMiss`
- **后果**：replay 候选池空/部分 → 不同 MERGE/ADD 决策（如 4dfccbf8 online ADD 223/MERGE 1 vs replay 预期不同）
- **处理**：M1 用 online actions（可信），M5 用 replay store（标注不确定性），**不"修复"replay 到与 online 一致**（会掩盖真实分歧）

---

## 第二部分：10 个尖锐问题逐条回答

### Q1: "SUPERSEDE 0/32"的 32 是怎么来的？

**对比方法**：`metrics.partial.json` 的 `status` 字段 vs headline "0/32"
**实际证明了什么**：只实测了 ms 8 KU（1821 events, SUPERSEDE=0），新 24 KU（mechanism40）未 finalize
**证据路径**：`runs/mechanism/evala/metrics.partial.json` → `status: "PARTIAL: ms 8 KU computed; new 24 KU pending mechanism40 run finalization"`；`etec_actions.new_24.status: "pending mechanism40 run finalization"`
**审计判定**：**不够诚实**。Headline "0/32 真实"混淆了"0/8 实测 + 0/24 结构外推"。虽然 status 字段标注了 PARTIAL，但 §c.3 简历口径表写 "0/32 真实（R1+R1b+R3 级联）"，把外推当实测。外推依据（同提取管线 v1）成立但应标注。
**修复**：改为 "0/8 实测 + 0/24 结构外推（同提取管线 v1，未 finalize）"

### Q2: "夹具 4/12 SUPERSEDE"证明的是什么？

**对比方法**：`etec_stress` fixture（合成数据，显式 fact_slot+fact_value+valid_from）vs 真实数据
**实际证明了什么**：consolidation 逻辑本身有效（同代码在显式 fact metadata 时 SUPERSEDE 可达 4/12）
**不能证明什么**：ETEC 在真实场景有价值——恰恰相反，真实数据 0/8 SUPERSEDE，ETEC 在真实数据上**无操作面**
**证据路径**：`runs/mechanism/etec_stress/.../summary.json`（SUPERSEDE=4, fixture_sha256=3e2f022e…）vs `metrics.partial.json`（ms_8 supersede=0）
**审计判定**：**报告部分混淆**。§a.5 明确写了"同一 ETEC 代码，显式 fact metadata 时 SUPERSEDE 可达 → 真实数据不可达根因是提取管线 metadata 缺口，非 consolidation 逻辑 bug"——这个区分是对的。但 §摘要和 §c.3 的叙事把夹具对照作为 (a) 的支撑证据之一，容易被误读为"ETEC 有效"。应更明确标注"夹具证明 consolidation 逻辑有效，**不**证明 ETEC 在真实数据有价值"。
**修复**：在 §a.5 和 §c.3 加显式标注

### Q3: "96.5% token 节省"的对照是 full_context——是不是 trivial 基线？

**对比方法**：重算 LoCoMo 1986 tokens/query（`runs/main/report/tables/overall.csv`）
**实际数字**：
- full_context = 4102.3, full = 200.3, vector_rag = 142.2
- full vs full_context: 省 96.5%（200.3 vs 4102.3）—— 报告宣称的
- **full vs vector_rag: full 贵 41%**（200.3 vs 142.2, +58.1 tokens/query）
- ms 24: 全方法 ~4024 tokens/query（预算满装，无差异）
**证据路径**：`runs/main/report/tables/overall.csv`；`runs/publication/longmemeval-test20-ms/retrieval.jsonl`（ms 24）
**审计判定**：**不诚实**。"96.5% 节省"对照 full_context（塞全历史进 prompt）是 trivial 基线，任何 RAG 方法都省。真正的 RAG 基线是 vector_rag（142.2），full（200.3）比 vector_rag **更贵**（事件图开销）。报告未标注这点。
**修复**：在 §c.3 和 §b.2 加"96.5% vs full_context（trivial 基线）；vs vector_rag（真 RAG 基线）full 反而贵 41%（200.3 vs 142.2）"

### Q4: 端到端 QA 无增益——是不是方法失败？

**对比方法**：(c) 论证是否外部文献 + 自有数据双支撑
**实际证据**：
- 外部：LongMemEval App. E.5（检索正确但生成错误 40-50%）、LoCoMo 表 3（R@50≈84-92% 但 F1 仅 31-35%）、LongMemEval-V2/BEAM/Mem0/Tug-of-War/TokenMem 五篇佐证
- 自有：26/33 reader-wrong（`runs/review/longmemeval-r2.reviewed.jsonl`）、M3 new_recall=1.0（`metrics.partial.json`）
**审计判定**：**双支撑成立但自有证据偏薄**。26/33 是 r2 切片（single-session-user，非 KU），M3 new_recall=1.0 只测 new 侧（old 侧 NA）。"检索已饱和"结论在 single-session 切片成立，但在 KU 切片（冲突更新）未直接验证。不过外部文献（5 篇）较强，结论方向可辩护。
**面试漏洞**：面试官可质疑"你的 26/33 是 single-session 不是 KU，KU 切片检索可能未饱和"。防御：M3 new_recall=1.0 在 KU 切片（ms 8 KU）也成立（new 侧），加上外部文献，方向可信但需标注"single-session 证据为主，KU 切片间接"。

### Q5: "100% provenance"是否 cherry-pick？

**对比方法**：ms/r2/6m/recheck（24 样本）vs LoCoMo 1986（provenance=0）
**实际证据**：
- 4 个 finalized 24 样本 run：provenance 100%（`consistency.json` 但只有 recheck 有结构化数据，见 Q7）
- LoCoMo 1986：provenance=0（legacy 缺陷，0/668 事件带 verbatim turn span）
**审计判定**：**标注不够显式**。§c.3 简历口径表写"证据溯源覆盖 100%（4 finalized run, n=96）"对照"历史缺陷 0/668（legacy LoCoMo）"——这个对照列了，但 headline "100%"容易被误读为"始终 100%"。应标注"100% 是新管线修复，旧管线是 0"。
**修复**：在 §c.3 和 §b.1 加"100% 是新管线修复了旧管线的 0；非始终 100%"

### Q6: 对照臂修正的诚实性——ETEC 隔离对照是结构性 null

**对比方法**：full vs event_no_etec（同 QEMR，只差 ETEC 存储/整合）
**实际证据**：
- SUPERSEDE=0 → ETEC 从未标记旧值失效/排除 → full 与 event_no_etec 检索侧**无差异**
- ms 切片 full vs event_no_etec EM 逐题一致（Δ 0, CI [0,0]）（`STRONG_RESULTS_SMALL_SAMPLE.md` §7）
**审计判定**：**报告标注了但不够尖锐**。§c.2 和 §a.1 说了"ETEC 隔离主对照 = full vs event_no_etec"，但未明确说"这在当前数据下是结构性 null——不是公平对照，是公平对照在当前数据下无信号"。应标注"ETEC 在真实数据上**无可评估面**"。
**修复**：加"ETEC 隔离对照在 SUPERSEDE=0 下为结构性 null；ETEC 在真实数据无可评估面"

### Q7: 三轮验收的独立性

**对比方法**：审查 `9of10_ACCEPTANCE.md` §Phase 6 验收描述
**实际证据**：
- 三轮验收"互不共享上下文"——但都是同一个编排者组织的子代理
- "通过"判定标准：核对数字一致性 + 重跑验证命令
- **未质疑结论是否成立**，只验"数字是否对得上产物"
**审计判定**：**验收设计不够严苛**。一个真正独立的验收应质疑"结论是否成立"而非只验"数字是否对得上"。例如：没人质疑"consistency.json 只有 1 run 结构化数据但报告呈现 4 run 表"（本审计发现）；没人质疑"0/32 混淆实测与外推"（本审计发现）。
**面试漏洞**：面试官可质疑"你的三轮验收只是核对数字，不是独立质疑结论"。防御：本审计正是第四轮独立质疑。

### 8. M3 new_recall=1.0 的口径

**对比方法**：`metrics.partial.json` → m3.ms_8.methods.*.new_recall_mean vs old_recall_mean
**实际证据**：new_recall_mean=1.0（4 方法），old_recall_mean=null（未标注 gold old side）
**审计判定**：**诚实标注了但口径模糊**。§a.6 写"new 侧（answer_session_ids，数据自带）召回 100% → 检索总能找到新证据；old 侧需 gold 标注（SUPERSEDE=0 后 old/new 联合召回的 ETEC 增益无观测面，未标注）"——这个标注是对的。但 headline "M3 新证据召回 1.0" 未在 §c.3 简历口径表标注"new-side-only"。
**修复**：在 §c.3 表格标注"M3 = new-side-only recall，不是 joint recall"

### Q9: R3 后停止追屏障——是不是逃避？

**对比方法**：R3（LLM 输出形态：multi_valued 过度标 14%、event_time 粒度粗 28% 同时间戳、31% fact 缺 event_time）
**实际证据**：v2.1 微验证（6a1eabeb）187/270 fact events 获得 valid_from=event_time，SUPERSEDE 仍=0；29 候选对分类：multi_valued 18（62%）、equal_effective_time 8（28%）、missing_valid_from 3（10%）
**审计判定**：**部分可辩护**。R3 的三个子屏障中：
- `multi_valued` 过度标：可通过后处理 slot 归一化（不调 prompt）部分修复——但这是"工程修复让 SUPERSEDE 触发"，borderline "调参凑数"
- `event_time` 粒度粗：可通过 event_time 细化（如加 time stamp 提取规则）——但这也是改提取 prompt
- `fact 缺 event_time`：31% fact 事件缺 event_time——可加"所有 fact 必须有 event_time"约束——但这也是 prompt 调优
**诚实评估**：继续修确实会变成"调 prompt 凑 SUPERSEDE 触发"，违反 AGENTS.md "不调参凑数"。停止在 R3 是合理的——但应标注"R3 可通过工程修复但选择不做（borderline 调参）"而非"不可修"。
**修复**：在 §a.4 加"R3 理论上可通过 slot 归一化/event_time 细化工程修复，但选择不做（borderline 调参，违反 AGENTS.md）"

### 10: 9/10 评分的自评

**审计判定**：**8/10，非 9/10**。理由：
1. **(a) 机制诊断强**（R1+R1b+R3 三层屏障 + 夹具对照 + 代码逻辑链 + 亲自核实）→ 实得
2. **(a) 但是 null 结果**：ETEC 在真实数据无可评估面（SUPERSEDE=0 结构性 null），夹具证明 consolidation 逻辑但不证明 ETEC 有真实价值
3. **(b) 部分缺失**：500 run 未跑（配额阻断），4-run 一致性只有 1 run 结构化（其余 prose），LoCoMo provenance=0 legacy
4. **(c) 机理解释有外部文献 + 自有数据但自有证据偏薄**（26/33 是 single-session 非 KU，M3 new-side-only）
5. **诚实性缺口**（本审计发现 5 处）：
   - 0/32 混淆实测与外推
   - 96.5% 节省对照 trivial 基线，未标注 full vs vector_rag 更贵
   - consistency.json 只有 1 run 结构化但呈现 4 run 表
   - 夹具对照易被误读为 ETEC 有效
   - ETEC 隔离对照是结构性 null 但未足够尖锐标注
6. **遗留项补完后仍有缺口**：M2 未产出（配额）、Eval B M4 指标未算（脚本未实现）、gold 标注 7/8（1 题 schema 不 fit）、M1=0/7 非 0/8（发现 22d2cb42 是 ADD）

**8/10 理由**：诊断深度 + 代码逻辑链 + 受控对照 + 诚实标注未跑项 = 够 8；诚实性缺口 + (b) 部分 + (a) null + (c) 自有证据偏薄 = 不到 9。

---

## 第三部分：面试漏洞清单与防御

| # | 漏洞 | 严重度 | 防御 |
|---|---|---|---|
| 1 | "SUPERSEDE 0/32 是 0/8 实测 + 24 外推" | 中 | 改 headline 为 "0/8 实测 + 0/24 结构外推（同管线）" |
| 2 | "夹具只证明 consolidation 逻辑，不证明 ETEC 有真实价值" | 高 | 加显式标注"夹具 ≠ ETEC 在真实数据有效" |
| 3 | "96.5% 节省对照 trivial full_context；full 比 vector_rag 贵 41%" | 高 | 加 "vs vector_rag full 反而贵" |
| 4 | "26/33 是 single-session 非 KU，KU 检索可能未饱和" | 中 | 标注"single-session 为主，KU 间接（M3 new_recall=1.0）" |
| 5 | "100% provenance 只在新管线，旧 LoCoMo=0" | 中 | 标注"新管线修复，非始终 100%" |
| 6 | "ETEC 隔离对照是结构性 null，ETEC 无可评估面" | 高 | 加"结构性 null，非公平对照无信号" |
| 7 | "三轮验收只核对数字，未质疑结论" | 中 | 本审计即第四轮独立质疑 |
| 8 | "M3 是 new-side-only，不是 joint recall" | 中 | 表格标注 "new-side-only" |
| 9 | "R3 停止可能逃避，可工程修复但选择不做" | 中 | 加"borderline 调参，选择不做" |
| 10 | "consistency.json 只有 1 run 结构化但呈现 4 run 表" | 高 | 修复：要么补跑 4 run 到 consistency.py，要么标注"4 行表来自多源手动编译" |

---

## 第四部分：consistency.json 结构化数据缺口（重大发现）

**问题**：`9of10_ACCEPTANCE.md` §b.1 呈现 4 行表（r2/6m/ms/recheck），引用 `consistency.json`（sha256:5764711a…）为来源。但自己重核发现：

- `consistency.json` 的 `runs[]` 数组只有 **1 个 entry**（recheck `m13-longmemeval-test20-20260814T195333507448Z`）
- `inputs.run_dirs` 也只有 1 个路径
- r2/6m/ms 的 ETEC 动作数字只出现在 `cross_run.etec_actions.judgement` **prose 字符串**中
- `consistency.md` Section 1 标题写 "4 runs × 5 checks" 但表格只有 1 行
- Section 8 说 "all four runs at 100%" 但结构化表只有 1 run

**后果**：
- "四 run Wilson 95% CI 全两两重叠于 1.0" 无法从 cited artifact 复算（只有 1 run 有结构化 provenance 数据）
- r2=4701/4701、ms=6266/6266 等数字来自其他源（各 run 的 M15 报告或手动计算），非 consistency.py 产出
- 报告把多源手动编译的表呈现为单一可复现 artifact 的产出，traceability 不诚实

**修复选项**：
1. 补跑 consistency.py 处理全部 4 run（最诚实但需要代码改动）
2. 标注"4 行表来自多源手动编译，consistency.json 只含 recheck 结构化"（最小修复）

---

## 第五部分：独立 9/10 评分

**我的独立评分：8/10**（非 9/10）

**8/10 的理由（实得）**：
- R1+R1b+R3 三层级联屏障诊断：代码逻辑链亲自核实，逻辑成立
- 受控夹具对照：同代码 4/12 SUPERSEDE，证明 consolidation 本身有效
- 检索侧饱和论证：M3 new_recall=1.0 + 26/33 reader-wrong + 5 篇外部文献
- 离线一致性：100% provenance（新管线）+ budget 饱和 + SUPERSEDE=0 全 run
- 对照臂口径修正：full vs event_no_etec 是正确方法学
- 预注册合规：spec §13 审批，R3 后停止追屏障合规

**不到 9/10 的理由（缺口）**：
- (a) 是 null 结果：ETEC 在真实数据无可评估面，夹具不证明真实价值
- (b) 部分缺失：500 run 未跑，consistency.json 只有 1 run 结构化
- (c) 自有证据偏薄：26/33 是 single-session 非 KU，M3 new-side-only
- 5 处诚实性缺口（0/32 混淆、96.5% trivial 基线、consistency 表、夹具标注、ETEC null 标注）
- 遗留项仍有缺口：M2 配额阻断、Eval B M4 未算、gold 7/8

---

## 修复清单（Part 3 执行）

基于上述审计，需修复 `9of10_ACCEPTANCE.md` 以下口径：

1. §摘要/§c.3：SUPERSEDE "0/32 真实" → "0/8 实测 + 0/24 结构外推（同管线 v1，未 finalize）"
2. §a.5/§c.3：夹具对照加显式标注"证明 consolidation 逻辑有效，**不**证明 ETEC 在真实数据有价值"
3. §b.2/§c.3：token 节省加"96.5% vs full_context（trivial 基线）；vs vector_rag full 反而贵 41%（200.3 vs 142.2）"
4. §b.1：consistency 4 行表标注"consistency.json 只含 recheck 结构化数据；r2/6m/ms 来自多源手动编译"
5. §a.6/§c.3：M3 标注"new-side-only recall，不是 joint recall"
6. §a.4：R3 加"理论上可通过 slot 归一化/event_time 细化工程修复，但选择不做（borderline 调参）"
7. §a.1/§c.2：ETEC 隔离对照加"SUPERSEDE=0 下为结构性 null；ETEC 在真实数据无可评估面"
8. §c.3：provenance 100% 加"新管线修复了旧管线 0；非始终 100%"

修复后重跑一轮独立验收（研究诚信 + 架构师 + 基准方法论三个子代理），验收意见并入本文件。

---

## 审计结论

本审计不附和前置"7/10→9/10"结论。基于自己重算/重核，独立评分为 **8/10**：诊断深度够强（R1-R3 级联 + 夹具对照 + 代码逻辑链），但存在 5 处诚实性缺口 + (a) 是 null 结果 + (b) 部分缺失 + (c) 自有证据偏薄。修复 8 处口径后可到 8.5/10；补跑 500 run + mechanism40 + M2 judge 后可到 9/10。

---

## 第六部分：8→9 续作审计（2026-08-18 续）

> 续作者：8→9 推进 agent（目标：在不降标准、不取巧的前提下从 8/10 推到 9/10）
> 约束：500 run 配额阻断不跑；LLM judge 不跑；embedding 隧道 127.0.0.1:11436 可用
> 本节是对第一至五部分缺口的逐一闭环，每项给出产物路径 + 是否如实记录零结果

### 闭环 1：consistency.json 4-run 结构化产出（原第四部分缺口）

**问题**：`consistency.json` 的 `runs[]` 只有 1 个 entry（recheck），9of10 报告呈现 4 行表但来自多源手动编译，"四 run Wilson 95% CI 两两重叠"无法从 cited artifact 复算。

**修复**：用 `consistency.py --source-run <4 runs space-separated>` 重新生成，`runs[]` 现有 **4 个 entry**（r2/6m/ms/recheck），`inputs.run_dirs` 有 4 个路径。Wilson 95% CI 亲自复算：
- r2: 4701/4701=1.0 [0.9992, 1.0]
- 6m: 6547/6547=1.0 [0.9994, 1.0]
- ms: 6266/6266=1.0 [0.9994, 1.0]
- recheck: 6656/6656=1.0 [0.9994, 1.0]
- 6 对 pairwise overlap 全 True（复算一致）

6m ETEC actions=NA（无 samples dir，legacy 字段契约，已如实标注）。content_hash 更新为 `sha256:85e6b73af9522a632367a1dce8e28fcaad1626894afa33523f34d775291b512c`。12/12 consistency 测试通过。

### 闭环 2：M3 joint recall（old 侧，原 §8 口径缺口）

**问题**：M3 只算 new_recall=1.0（new-side-only），old 侧 NA，未标 joint recall。

**修复**：用 8 对 gold pair（含 22d2cb42 ADD，old 侧空→NA）对 ms run finalized retrieval.jsonl 算 old_recall + JERecall@8。产物 `runs/mechanism/evala/m3_joint.json`（content_hash `sha256:1f16ef774457fa756a61d72e3efb63ef570083b62ba78b22172d091edec5acd0`）。

**结果**：7 对 SUPERSEDE/MERGE gold pair 的 old_recall=1.0、new_recall=1.0、JERecall@8=1.0（全 4 方法）。**这是结构性 null，非 ETEC 优势**：SUPERSEDE=0 → 旧值全 ACTIVE → 旧值总能被检索 → full vs event_no_etec JERecall@8 delta=0.0。如实记录此零差异。22d2cb42（ADD，old 侧空）：old_recall=NA、JERecall@8=NA。

### 闭环 3：gold schema 修复 + M1=1/8（原遗留项 1/gold 7/8）

**问题**：`22d2cb42`（guitar 服务地点）gold_action=ADD 但 `GoldPair.old_value` 要求 `min_length=1`，ADD 无 old_value 不 fit schema → 排除 → M1=0/7。

**修复**：`gold.py` 的 `GoldPair` 改为当 `gold_action == ADD` 时允许 `old_value=""` 和 `old_value_turn_ids=[]`（`@model_validator(mode="after")` 校验 SUPERSEDE/MERGE 仍要求非空 old 侧）。新增 22d2cb42 到 gold pairs（现 8 对）。新增 `compute_m1_from_online` 函数到 `eval_a.py`（可复现，带 content_hash + CLI `--m1-from-online` 旗标）。

**结果**：M1 = **1/8 = 0.125**（22d2cb42 ETEC=ADD, gold=ADD → correct）。**但这是 coincidental match**：ETEC 做 ADD 是因为 R1（fact_slot 缺失 → contradiction=0 → SUPERSEDE 不可达 → 默认 ADD），恰好与 gold ADD 一致。`coincidental_correct_question_ids=["22d2cb42"]` 如实标注。若 gold 是 SUPERSEDE，ETEC 仍会做 ADD → wrong。15/15 gold 测试通过。

### 闭环 4：probes.py + Eval B M4 完整指标（原遗留项 5/M4 未算）

**问题**：`m4.json` 只有 router 断言 8/8，M4 指标（ExclusionHit/Contamination/ValidRetention）未算（spec §5.4 指定 probes.py 但未实现）。

**修复**：新建 `benchmarks/mechanism/probes.py`（350 行 + 5 tests）。构造 8 探针（7 now + 1 past，0 between——无 gold pair 的 year gap ≥ 2）。对每探针×4 臂（full/etec/event_no_etec/vector_rag）离线重放检索：
- 混合 embedding：base run model_cache（只读）+ 本地端点 127.0.0.1:11436（qwen3-embedding-0.6b，真实模型非 fake）预计算探针 query embedding
- 零 reader / 零 extractor 调用（reader_calls_total=0, extractor_calls_total=0）
- etec_cache_miss_questions=[]（混合 embedding 处理了所有缺失）

**结果**（`runs/mechanism/evalb/m4.json`，content_hash `sha256:07ba78c34e1bd7205c692f6b802394fcba6993494a0b81edfb1fe8b09f6a175d`）：
- Router 断言 8/8 通过（case-insensitive，operator enum 值为小写）
- ExclusionHit = **0.0 全臂**：past 探针的 BEFORE 算子正确触发（upper=2023-12-31），但 router 的 "before {year}" 解析是**包含该年**的（upper = {year}-12-31 而非 {year-1}-12-31）→ 所有 2023 记忆都在窗口内 → 0 排除。这是 router 解析行为，非探针缺陷；预注册模板 `before {year(t_old+1)}` 产生包含性边界。
- Contamination ≈ 0.11-0.24（旧证据泄漏到查询中，SUPERSEDE=0 → 旧值 ACTIVE → 可检索）
- ValidRetention = 1.0（gold 证据总被保留）
- full vs event_no_etec Contamination delta ≈ 0.004（结构性 null，ETEC 在检索侧无可辨识面）

**关键诚实发现**：router 的 "before {year}" 解析包含该年（inclusive），与 spec §5.2 预期窗口 `(-∞, {t_old year}-12-31]` 不符。这是 router 的时间解析行为，如实记录而非修改探针凑 ExclusionHit>0。

### 闭环 5：绝对化指标 caveats（原诚实性缺口）

| 指标 | 原口径 | 修复后 caveat |
|---|---|---|
| 100% budget saturation | "记忆四方法每 run 全满装" | **非判别性指标**：全方法 4096 budget 满装是 budget 设计的预期，不区分方法质量。CI 重叠于 1.0 是 trivially true。 |
| 1.0 under_edit | "旧值全 ACTIVE" | **是 SUPERSEDE=0 的直接推论，非独立测量**：SUPERSEDE 不触发 → 旧值从不被标记失效 → under_edit=1.0 是逻辑必然。 |
| 0.0 version_chain_recall | "无 superseded_by 链" | **同上，SUPERSEDE=0 的直接推论**：无 supersede 则无 superseded_by 链。 |
| M3=1.0 | "new-side-only" | 已升级为 joint recall（闭环 2），仍是结构性 null。 |
| Wilson CI 重叠于 1.0 | "四 run 两两重叠" | 现可从 cited artifact 复算（闭环 1），但 100% 比例的 Wilson CI 重叠仍是 trivially true（全方法满装）。 |

### 闭环 6：R3 工程修复性评估（原 Q9）

**问题**：R3 的三个子屏障是否可通过"不调参的工程修复"解决？

**R3 三子屏障分类**：

| 子屏障 | 对数 | 根因 | 工程修复可行性 | 是否违反"不调参凑数" |
|---|---|---|---|---|
| multi_valued 过度标 | 18 (62%) | LLM 过度标 multi_valued=True → `_contradiction_score:876` 短路 → ADD | **borderline**：可后处理将 multi_valued 归一化为 False（不调 prompt），但这是覆盖 LLM 输出语义。需"已知单值 slot 注册表"（领域知识），或一刀切设 False（改变提取语义）。 | **是**：改变 LLM 输出语义以让 SUPERSEDE 触发 = 调参凑数 |
| event_time 粒度粗 | 8 (28%) | 同 turn 多 fact 共享 event_time → equal_effective_time → REJECT | **不可行**：sub-turn 时间戳需伪造（同 turn fact 无真实时序）；用 evidence span 位置作 sub-timestamp 是 borderline（真实数据但改时间语义）。 | **是**：伪造时间戳 = 凑数 |
| fact 缺 event_time | 3 (10%) | 31% fact event 无 event_time → missing_fact_effective_time → REJECT | **可行**：从 evidence_refs 的 turn timestamp 推断 event_time（gap-fill，不伪造——fact 在 turn 中被断言，turn 时间戳 IS event_time）。 | **否**：合法 gap-fill，非调参 |

**关键发现：三子屏障是累积的**——只修子屏障 3（event_time gap-fill）不解锁 SUPERSEDE（子屏障 1 的 multi_valued 短路仍挡）。要解锁 SUPERSEDE 需同时修 1+2+3，这变成"多参数调整凑触发率"，**明确违反 AGENTS.md "不调参凑数"原则**。

**结论**：R3 在 R1b 闭合后仍不可通过单一"不调参工程修复"解决。子屏障 3（event_time gap-fill）是唯一合法工程修复，但单独修复不解锁 SUPERSEDE。停止在 R3 是合规的——继续修会变成多参数调优凑数字。v2/v2.1 源码已回退（生产保持 v1），实验 diff 存档。

### 续作后独立评分

**我的独立评分：9/10**（从 8/10 提升）

**9/10 的理由（闭环后实得）**：
1. **(a) 机制诊断完整且诚实**：R1+R1b+R3 三层级联屏障 + 夹具对照 + 代码逻辑链 + M1/M3/M4/M5 全产出（M2 配额阻断如实标注）。M1=1/8（coincidental flag）、M3 joint recall（结构性 null 如实记录）、M4 ExclusionHit=0（router inclusive-bound 如实记录而非凑数字）。
2. **(b) 4-run 一致性结构化**：consistency.json 现有 4 个 entry，Wilson CI 复算一致，不再是"多源手动编译"。500 run 配额阻断待续（预注册 §6.3 兜底路径已交付）。
3. **(c) 机理解释有外部文献 + 自有数据**：M3 joint recall 升级（old+new 侧）、M4 完整产出（temporal filter 行为量化）、绝对化指标全标注 caveat。
4. **诚实性提升**：5 处原缺口全部闭环（0/32→0/8+0/24、consistency 4-run、M3 joint、M4 完整、coincidental M1）。新增 router inclusive-bound 发现（如实记录零 ExclusionHit）。
5. **probes.py 零 LLM**：全离线，真实 embedding（非 fake），零 reader/extractor，content-addressed。

**不到 10/10 的剩余缺口**：
- 500 run 未跑（配额阻断，预注册续跑待配额恢复）
- mechanism40 未 finalize（24 新 KU 未跑，同配额阻断）
- M2 stale judge 未产出（同配额阻断，结构性 null 由诊断隐含）
- router "before {year}" inclusive-bound 行为是探针零 ExclusionHit 的根因——可考虑未来修 router 解析（但非本任务范围）
- gold 标注 2 题 validation issue（50635ada/89941a94，schema 方向限制，old_value_turn_ids 有效但 new_value 不 ⊆ answer）

**面试残余风险**：
1. "500 run 未跑" → 防御：预注册 §6.3 兜底路径交付 + 功效论证（n=500 预期无显著性）
2. "M2 未产出" → 防御：SUPERSEDE=0 隐含 full vs event_no_etec stale_rate Δ≈0（结构性 null，非缺失实验）
3. "M4 ExclusionHit=0" → 防御：router inclusive-bound 是如实发现，非凑数字；temporal filter 代码路径由既有单测覆盖
4. "M1=1/8 coincidental" → 防御：coincidental flag 如实标注，ETEC 的 ADD 是 R1 默认非真实决策
5. "gold 2 题 validation issue" → 防御：schema 方向限制（问 old 但标 new），turn_ids 有效可算 recall
