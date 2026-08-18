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

### 当前实证状态（2026-08-18）

已落地的机制级强结果（`runs/` 产物，内容寻址报告）：
- 证据溯源覆盖率 100%（packed evidence 全部携带 `raw_turn_id`，确定性 span 定位闭环；**新管线修复了旧管线 0，非始终 100%——LoCoMo legacy provenance=0**）；
- 0 分格修复 10→4（ETEC merge gate + 预算满装 packing）；
- LoCoMo 1986 题：记忆方法 142.2 vs full_context 4102.3 tokens/query（Δ −3959.9，p<0.001，约省 96.5% **vs full_context trivial 基线**；**注意 full（200.3）比 vector_rag（142.2）贵 41%，事件图开销**）；
- 33/33 失败人工复核：主因 answer_present_reader_wrong 26（reader 冗余措辞），真正检索/提取/预算失效仅 7/33。

O09 机制诊断增量（2026-08-18，详见 [`docs/9of10_ACCEPTANCE.md`](9of10_ACCEPTANCE.md) §a/§b）：
- **SUPERSEDE 诊断**：真实数据 0/8 实测 + 0/24 结构外推（同管线 v1，mechanism40 未 finalize）= "0/32"（**非全实测，headline 需标注外推**），根因 = R1 提取不写 fact_slot → R1b 不写 valid_from（point-interval 不重叠）→ R3 LLM 过度标 multi_valued + event_time 粒度粗 + 缺 event_time 三层级联屏障；同代码在受控夹具 `runs/mechanism/etec_stress/…/summary.json`（fixture_sha256 `3e2f022e…`）上 4/12 SUPERSEDE、invariant_pass_rate 1.0 → **证明 consolidation 逻辑有效，不证明 ETEC 在真实数据有价值**（真实 0/8 说明 ETEC 无操作面）；v2.1 单测证明 R1b 闭合后机制层可达。独立审计后：M1=0/7（`runs/mechanism/evala/m1.json`，22d2cb42 gold_action=ADD 排除），M5 under_edit=1.0/version_chain_recall=0.0（`runs/mechanism/evala/m5.json`），M2 未产出（配额阻断，`runs/mechanism/evala/m2.json`）。
- **interval 算子预筛**：router 全量 500 题确定性预筛（零 LLM，预注册方法 `reference_time=question_date` 解析）`none 382 / earliest 42 / latest 23 / duration 26 / between 15 / sequence 11 / at 1`、BEFORE/AFTER = 0（`runs/mechanism/router_screen/router-screen.json`，content_hash 字段 sha256:74d18a1d…）→ 真实数据 interval 过滤近零触发（16/500=3.2%），与既有三个切片一致。
- **M3 新证据召回**：ms 8 KU 四方法 new_recall_mean = 1.0（full / event_no_etec / etec / vector_rag）→ 检索侧无瓶颈（**new-side-only recall，非 joint recall；old 侧 gold 已标注 ms 8 KU 但 SUPERSEDE=0 使 ETEC 增益无观测面**）。
- **大样本一致性**：4 个 finalized 24 样本 run（n=96）provenance 100%（Wilson 95% CI 两两重叠于 1.0）+ 记忆方法预算饱和 1.0 + SUPERSEDE=0 全 run（`runs/mechanism/consistency/consistency.json`，sha256:5764711a…）；失败归因分布结构同构（extr+budget 主导，baselines 加 absent=48），r2 33/33 复核口径下 26/33 = reader_wrong（auto-vs-reviewer 一致率 7/33=21.2%）；1986-LoCoMo 失败分布（answer_not_recoverable 4423 / adversarial_no_gold 2664 / recoverable_wrong 2454 / no_memory 1940 / budget_truncation 1409）方向一致。
- **500 run 状态**：网关 429/403 配额阻断，`configs/longmemeval/main500.toml` 已入库待续；预注册 §6.3 末段兜底路径 + 功效论证已交付（n=500 最小可检测效应 ±0.018–0.039 > 观测 0.005–0.014，500 run 无显著性是预期内，作稳定性检查不作决策信号）。

未达成的门槛（如实记录，不伪装）：
- 无端到端 QA 增益声明：24 样本 `full` vs `vector_rag` 无正向显著差异（6m 报告 Δ −0.1667 为负，且受 run-to-run UUID 平局非确定性影响，见 `docs/STRONG_RESULTS_SMALL_SAMPLE.md` §6）；
- `etec` vs `event_no_etec` 在 single-session 切片 EM 逐题一致（Δ 0，CI [0,0]），且该对照混入检索策略因素（§7）；
- SUPERSEDE 与 temporal interval 排除在真实数据上结构性不可达（机制诊断完成，见 `docs/9of10_ACCEPTANCE.md` §a）；
- stale-memory error 机制诊断完成（SUPERSEDE 结构性不可达，R1+R1b+R3 级联屏障）；M2 stale judge 因网关配额阻断未跑，由诊断隐含无 with/without-ETEC 差异（SUPERSEDE 不触发则旧值不会被标记失效），见 `docs/9of10_ACCEPTANCE.md` §b 风险 2。

结论口径：本项目的交付物是"机制证据链 + 可复现产物"，不是绝对分数竞争（竞品见 `docs/COMPETITIVE_ANALYSIS.md`）。

## 7. 方法学：对照臂口径修正（O09）

来源：`docs/9of10_ACCEPTANCE.md` §a.1/§c.2、`benchmarks/longmemeval/run.py:153-158`（9of10 报告原文记作 `run.py:153-158`）、spec §3.3/§13 决议 1。

**问题**：既有 `etec` 与 `event_no_etec` 对照臂在 `benchmarks/longmemeval/run.py:153-158` 上配置为：
- `etec` → 检索策略 `FIXED_VECTOR`、ETEC 存储/整合 off（即纯向量基线，没有事件图/QEMR 路径）；
- `event_no_etec` → 检索策略 `QEMR`、ETEC 存储/整合 off（即 QEMR 检索但不做冲突更新/时序整合）。

因此既有 "etec vs event_no_etec" 对照同时混入两因素：(1) ETEC 开/关、(2) 检索策略 QEMR/FIXED_VECTOR，**不构成"只差 ETEC"的纯净消融**。在此口径下观测到的 Δ 0（CI [0,0]）不能解读为"ETEC 无收益"，因为两臂在检索策略上也不等价。

**修正**：ETEC 隔离主对照改为 `full` vs `event_no_etec`：
- `full` → 检索策略 `QEMR`、ETEC 存储/整合 **on**；
- `event_no_etec` → 检索策略 `QEMR`、ETEC 存储/整合 **off**；
- 两者仅差 ETEC 存储/整合，**同 QEMR 检索** → 满足"只差 ETEC"的消融口径。

`full` vs `etec` 则是检索策略隔离（同 ETEC 存储）：`full` 用 QEMR、`etec` 用 FIXED_VECTOR。

**理由**：(1) 控制变量法要求消融臂只差被消融因子，避免方法混杂（AGENTS.md 代码评审规则："Reject changes that mix benchmark methods under unequal model, context-budget, or retrieval-budget settings"）；(2) 在 ETEC 真实不可达的诊断结论下（`docs/9of10_ACCEPTANCE.md` §a.7），`full` vs `event_no_etec` 是 ETEC 隔离的唯一可用纯净对照，既有 multi-session 切片该对照 EM 逐题一致（Δ 0）与机制诊断（SUPERSEDE 不触发则 ETEC 在检索侧无操作面）相互佐证；(3) `etec` vs `event_no_etec` 对照不丢弃，但定位为"检索策略 + ETEC 两因素联合对照"，不作 ETEC 单因素结论。
