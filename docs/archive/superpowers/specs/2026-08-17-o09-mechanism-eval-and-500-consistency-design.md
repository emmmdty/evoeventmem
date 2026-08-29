# O09 预注册实验方案：ETEC 机制指标实证（Eval A/B）+ 500 样本一致性验证

> 状态：预注册草案（待编排者拍板审核要点后冻结）
> 日期：2026-08-17
> 作者：Phase 2 设计子代理（供编排者使用的设计产出）
> 对应任务：`tasks/optional/O09_mechanism_evaluation.md`（由编排者创建，引用本 spec）
> 验收目标：简历评审 7/10 → 9/10，验收标准 (a) 机制评测、(b) 500 一致性、(c) 报告口径，见 §1。
> 本文件只做设计；所有"预期结论方向"均为预注册假设，不预设结果。

---

## 0. 文档约定与不可违背约束

- **不修改** `runs/` 任何已封存（finalized）产物；本方案的所有新计算只**读**已封存产物，写新产物到 `runs/mechanism/`、`runs/publication/`、`runs/analysis/`。
- **不修改**任何源码来"制造"结果：不修改 `src/evoeventmem/` 与 `benchmarks/longmemeval/run.py` 的既有行为；机制脚本全部是新增、纯只读重算或新增独立 runner。
- 所有 LLM 判断（M2 stale judge 等）**必须**缓存输入输出并固定 judge 模型。
- 任何新脚本必须可校验：内容寻址、哈希记录、回归测试。
- 引用数字必须带产物路径；本 spec 中引用的代码位置均为 2026-08-17 的 git HEAD（`e521e31`）事实核查结果。

---

## 1. 目的与成功定义

### 1.1 验收标准（与编排者的 (a)(b)(c) 对齐）

**(a) 机制评测**：回答"ETEC 的 SUPERSEDE/MERGE/REJECT 与 QEMR 的时序排除在**真实数据**上是否触发、触发时是否做对、不触发时根因是什么"。
由两个子评测构成：

- **Eval A（冲突更新，knowledge-update 类）**：核心指标 = ETEC 动作触发计数/触发率（真实数据）、M5 内存态探针、M2 stale-memory error rate、M3 Joint-Evidence Recall@k。
- **Eval B（时序有效性，temporal-reasoning 类）**：router 全量确定性预筛（零 LLM 成本）+ 合成时间窗探针（在既有 finalized 样本上按 gold 值对构造），核心指标 = M4 三件套 + historical packed 计数 + `temporal_interval_excluded` 计数，纯程序化。

**(b) 500 一致性**：全量 500 题 LongMemEval S、6 方法端到端（提取共享）finalize；验证小样本机制结论在大样本不漂移（溯源覆盖率、0 分格/失败归因分布、预算满装、机制触发率），不做显著性宣称（功效论证引用 `docs/METHODOLOGY_CHANGE.md` §1、§3）。

**(c) 报告口径**：所有机制数字进入 `README.md` / `docs/EVALUATION.md` / `docs/STRONG_RESULTS_SMALL_SAMPLE.md` / `docs/INTERVIEW_KIT.md`，每个数字可溯源到 finalized 产物或 content-addressed 报告；诚实标注未达成项（沿用现状口径：不做端到端增益声明）。

### 1.2 (a) 的通过判据（预注册）

Eval A + Eval B 产出 **5 类机制指标表**（M1/M2/M3/M5/M4，见 §5.3、§6.4），每张表附带**根因诊断**；结论方向不预设，但必须落在以下两种形态之一（二选一即可验收）：

1. **触发并正确**：某机制（SUPERSEDE/MERGE/interval 排除）在真实/探针数据上触发，且 M1 准确率或 M4 排除命中率呈明确优势（数值上 + 配对小样本统计）。
2. **机理诊断**（当触发率极低时，如实给出）：触发率低的结构性根因（见 §4.2 预筛发现的 R1 结构性 fact_slot 缺失假设），并用量化证据（rule_hits/features 分布、受控夹具对照）支撑。

### 1.3 预期结论方向（预注册，只用于预先声明，不用于事后调整）

| 项目 | 预注册预期方向 | 理由 |
|---|---|---|
| E1 SUPERSEDE 真实数据触发率 | 预期 ≈ 0 或极低 | 已预筛发现结构性假设：提取管线从不写 `fact_slot`/`fact_value` 元数据 → `_same_fact_slot` 恒 False → `contradiction_score ≡ 0` → consolidation.py:398-427 的 SUPERSEDE 分支结构上不可达（§4.2）。若成立，主结论 = 可达性诊断，非"ETEC 无效" |
| E2 MERGE 真实数据触发率 | 预期 > 0（ms 切片已观测 2/24 题） | MERGE 分支不依赖 fact_slot（consolidation.py:435-455），已被实测触发 |
| E3 M2 stale 率 | 预期 `full` ≤ `vector_rag` | event 方法把旧值标记失效/排除，vector_rag 新旧 chunk 并存且按相似度竞争 |
| E4 M3 Joint-Evidence Recall@8 | 预期 event 方法 ≥ vector_rag（方向开放，不做强声明） | 事件级记忆按语义+证据打包，可能同时带新旧证据；但预算竞争也可能只带一边 |
| E5 M5 under-edit | 预期在真实数据上偏高（与 E1 一致） | 无 SUPERSEDE 则旧值保持 ACTIVE |
| E6 Eval B M4（探针） | 预期在显式时间窗查询下 `full`/`etec`（etec 存储）可观测 `temporal_interval_excluded`，且 ValidRetention/Contamination 优于 `event_no_etec`（raw 存储） | 探针查询带显式年份，走 retrieval.py:712-819 interval 路径；etec 存储中旧值 SUPERSEDED/有效区间收窄，raw 存储两者并存 |
| E7 500 run | 预期无显著性（点估计+CI 仅描述），一致性校验 5 项预期通过 | METHODOLOGY_CHANGE.md §1 功效分析：最小可检测效应 ±0.018–0.039 > 观测 0.005–0.014 |

**统计功效预注册（引用既有分析，不重算）**：≤40 题配对 McNemar 单侧翻转 ≥10 → p≈0.043、≥12 → p<0.01；整体准确率 Δ <15–20 点不可分辨 → 端到端区分度不依赖 EM，区分度来自机制指标。Zep 全时态图在 KU 上 gpt-4o 仅 +6.5%（gpt-4o-mini −3.4%）→ 端到端 KU 增益天花板预期在报告里如实书写（引用 `docs/COMPETITIVE_ANALYSIS.md` 竞品情报）。

---

## 2. 非目标（明确不做）

1. **不修改 ETEC/提取/检索实现**来提升触发率（包括不"补写" fact_slot 元数据制造 SUPERSEDE 触发）。
2. **不把合成探针当作基准题**：探针只进入 `runs/mechanism/evalb/` 的受控机制产物，绝不混入 `runs/publication/`、不参与任何 benchmark 分数表。
3. **不做 500 run 的显著性宣称**；不做多轮调参（阈值、权重、prompt 全部冻结在既有 policy 版本）。
4. **不引入 M7 假前提拒答评测**：500 题 question_type 分布无 abstention（数据核实：single-session-user 70 / multi-session 133 / single-session-preference 30 / temporal-reasoning 133 / knowledge-update 78 / single-session-assistant 56），无自然触发载体；如需 M7 需另立任务（O04 EvoMemBench 候选）。
5. **不扩展 LoCoMo、不做双数据集 headline**（既有 blocked 项，保持如实记录）。
6. **不重跑/不回改** `runs/publication/longmemeval-test20*`、`runs/ablation/`、`runs/analysis/` 已封存产物；机制指标只从已封存产物确定性重算。
7. **不做跨 run 预测一致性证据**（提取含模型随机性，方法论变更记录 §7 已定论）；确定性证据以回归测试为准。

---

## 3. 2026-08-17 关键事实核查（预筛证据，先于本 spec 冻结）

本节全部为本次设计阶段的实测核查，非假设。

### 3.1 router 全量 500 题预筛（确定性、零 LLM 成本，已执行）

用 `QueryRouter.route(question, reference_time=question_date 解析)` 对 500 题全部执行，算子分布：

```
none: 391   duration: 26   latest: 29   earliest: 42   sequence: 11   at: 1
BETWEEN/BEFORE/AFTER: 0
```

**结论**：interval 算子（AT/BEFORE/AFTER/BETWEEN，即触发 `temporal_interval_excluded` 的唯一路径，retrieval.py:783-813）在自然 500 题中仅 1 例（AT）。→ Eval B 的真实数据部分必然是"近零触发"，**合成时间窗探针是 M4 的唯一有效证据来源**（预注册即此，非事后补救）。

### 3.2 SUPERSEDE 结构性不可达假设（预注册诊断假设，待 Eval A 验证）

核查证据链：

1. `_contradiction_score`（consolidation.py:869-886）开头 `if multi_valued or not _same_fact_slot(source, target) or _same_fact_value(...): return 0.0`。
2. `_same_fact_slot`（consolidation.py:943-946）要求 `metadata["fact_slot"]` 存在且相等；`fact_slot_key` 对缺失返回 None。
3. 真实提取管线 `_memory_record`（extraction.py:980-1003）只写 `extractor_prompt_version` / `source_dataset` / `source_sample_id` 三个 metadata 字段，**从不写 fact_slot/fact_value/multi_valued**（已 grep 全库确认；fact_slot 只出现在 `benchmarks/experiments/fixtures/etec_stress_v1.json` 与 `benchmarks/etec_smoke.py`）。
4. 因此真实数据上任意 pair 的 `contradiction_score = 0.0` → `_score_pair` 中 SUPERSEDE 分支（consolidation.py:398-427）条件 `contradiction_score >= 0.7` 恒不满足 → **SUPERSEDE 在真实数据上结构上不可达**。
5. 对照：受控夹具 `benchmarks/experiments/etec_stress.py` 的 fixture 显式声明 fact_value → SUPERSEDE 可达（该脚本要求 `supersede_count > 0`，是既有 Gate D 的一部分）。→ "同一 ETEC 代码在夹具可达、在真实提取输出不可达"构成**机理诊断的对照证据**。

**诊断分桶（预注册）**：Eval A 对每个非 gold 动作的决策读取其 `metadata.etec.decision.rule_hits`/`features`，归入：

- `R1 structural_fact_slot_absent`（主假设：无 fact_slot → contradiction≡0）
- `R2 contradiction_below_threshold`（同 slot 但 <0.7，排除 R1 后）
- `R3 effective_time_missing_or_equal`（rule_hits 含 `missing_fact_effective_time` / `equal_fact_effective_time`）
- `R4 extraction_miss`（新值事件未被提取）
- `R5 multi_valued`（slot 被标记 multi_valued）
- `R6 candidate_bounded_out`（新值记忆未被候选生成覆盖，link 索引/候选 cap 导致）
- `R7 merge_temporal_disjoint`（rule_hits 含 `disjoint_temporal_intervals`）

### 3.3 对照臂策略矩阵核查（发现一处既有混淆，需编排者拍板，见 §12 决策点 1）

`run.py:153-158` 的方法→检索策略映射：

| 方法 | 存储 | 检索策略 |
|---|---|---|
| `vector_rag` | raw turn chunks | FIXED_VECTOR |
| `event_no_etec` | 提取快照、无 ETEC | **QEMR** |
| `etec` | 提取快照、有 ETEC | **FIXED_VECTOR** |
| `full` | 提取快照、有 ETEC | QEMR |

**重要**：`etec` 与 `event_no_etec` 同时差在"ETEC 开/关"与"检索策略 QEMR/FIXED_VECTOR"两个因素上，**不是"只差 ETEC"的消融**（不符合 AGENTS.md 评审规则"Reject changes that mix benchmark methods under unequal … retrieval-budget settings"的精神）。真正等检索策略的 ETEC 对照是 **`full` vs `event_no_etec`（均 QEMR）**。本 spec 的机制对照一律以此为 ETEC 隔离臂（也即既有分析 config 的 `lme_etec_vs_raw_events` 主比较）。`etec` 方法只用于"同 ETEC 存储下 FIXED_VECTOR vs QEMR"的检索策略隔离（`full` vs `etec`）。

### 3.4 其他核查确认

- 动作计数：`samples/<id>.json → ingestion.etec.actions`（实测 4dfccbf8：`{"ADD": 223, "MERGE": 1}`）；`consolidation.jsonl` 只记主 action（ADD 被映射 keep），粒度不足 → M1 必须从 sample 级 actions + 存储重放读 `metadata.etec.decision`。
- 排除明细：run 根 `retrieval.jsonl` 只记 `exclusion_count`（实测 0bb5a684 temporal intent 为 131，多数为 budget_exceeded）；含明细的 payload 只有 ablation.py 的离线重放版（:931-937）→ Eval B 探针 runner 必须自带明细 payload（新脚本，不改 ablation.py）。
- `retrieval.jsonl` packed_items 的 evidence_refs 含 `raw_turn_id` 与 `historical` 字段（实测确认）→ M3/M4 可离线确定性重算。
- 数据集 78 KU + 133 TR；ms 切片 8 KU + 8 TR 已 finalized；`test20-ms.selection.json` 用 seed 42 + sha256 排序 + 轮询交织，清单冻结。
- 提取 = 每样本 1 次调用（`extract_event_snapshot`，单次截断到 max_extraction_tokens），reader 每方法 1 次 → 每样本 1+6=7 次 LLM 调用。
- 本地 embedding 隧道：`ssh -L 11436:127.0.0.1:11436 gpu-5090`（METHODOLOGY_CHANGE.md §7）；LLM 网关 `opencode.ai/zen/go/v1`，模型 `deepseek-v4-flash`（.env `EEM_LLM_BASE_URL/EEM_LLM_MODEL`）。Ark 备用端点 `minimax-m3` 配额不稳（每天 5h 窗口，见 .env 注释）。
- 缓存：`FileModelCache(run_dir/model_cache)`，key = sha256(namespace, payload)，按 `model_cache/<namespace>/<hash>.json` 存储 → 探针查询 embedding 可**预计算写入探针 run 自己的缓存副本**（base run 缓存只读复制），实现全离线确定性重放。

---

## 4. Eval A：冲突更新机制评测（knowledge-update）

### 4.1 样本选择（冻结清单，selection.json）

- 评测总集：**32 KU 题 = ms 切片已有 8 题（离线重算，不重跑）+ 新增 24 题（新 run）**。
- 新增 24 题抽取规则（确定性，预注册）：universe = 78 KU 中剔除 ms 切片 8 题后余 70 题；排序键 = `sha256("42" + question_id)`（seed 42，与 ms 切片同种子机制）；取前 24。
- **已冻结清单（2026-08-17 计算，写入新 selection.json）**：

```
KU(24): 6071bd76, 9ea5eabc, 10e09553, cf22b7bf, dad224aa, 9bbe84a2, 6aeb4375,
        b01defab, 69fee5aa, 2133c1b5_abs, ed4ddc30, 6aeb4375_abs, 0977f2af,
        f685340e, 01493427, 830ce83f, 603deb26, 42ec0761, 4b24c848, 7e974930,
        e61a7584, 06db6396, 07741c45, cc5ded98
TR(16): gpt4_93f6379c, 982b5123, b46e15ee, gpt4_4ef30696, gpt4_e072b769,
        gpt4_fe651585_abs, 0bc8ad93, 2a1811e2, b9cfe692, 982b5123_abs,
        gpt4_68e94288, bcbe585f, e4e14d04, gpt4_e414231f, gpt4_2487a7cb, gpt4_6dc9b45b
```

（与 ms 切片 0 重叠，已校验；含 `_abs` 后缀的 abstention 变体题，与 ms 切片先例一致，保留。TR 16 题服务于 Eval B 的真实数据部分，见 §5。）

- 产物：`configs/longmemeval/mechanism-evala.selection.json`（schema_version 沿用 `longmemeval.slice-selection.v1`，method 字段如实描述 seed+sha256 规则与"剔除 ms 切片"步骤）。
- runner 约束：只认 `--sample-ids` 或 manifest（已核实 run.py:306、363-365），selection.json 不直接驱动 runner，由编排者按冻结清单执行 `--sample-ids`。

### 4.2 gold 值对标注流程与产物格式

**标注对象**：32 KU 题（8 已有 + 24 新增），只依赖原始数据 `longmemeval_s_cleaned.json`，**可在 40 题 run 之前完成**（预注册先行）。

**流程**（参照 33/33 人工复核先例 `runs/review/longmemeval-r2.reviewed.jsonl`）：
1. `scripts/annotate_gold_pairs.py --dataset ... --question-ids <清单> --out runs/mechanism/gold/review_sheet.jsonl`：为每题生成标注工作表（question 全文、answer、answer_session_ids、相关会话 turn 文本、t_q 候选 = question_date 与 answer 首 turn 时间戳）。
2. 人工（编排者）填写 `subject / attribute / old_value / old_value_turn_ids / new_value_turn_ids / t_q / t_old / multi_valued / gold_action / notes`。
3. 校验脚本 `benchmarks/mechanism/gold.py`（含 `validate_pairs`）：
   - 必填字段非空；`new_value` 与官方 answer 归一化 token 子集一致；
   - turn ids 必须存在于数据（`haystack_sessions` 全量检索）；
   - `t_q ≥ t_old`；`t_old < t_q` 时 gold_action 才可为 SUPERSEDE；
   - `gold_action ∈ {SUPERSEDE, MERGE, ADD}`（REJECT 不是正常更新动作；M7 不在本评测）。
4. 校验通过后写 `runs/mechanism/gold/longmemeval-kupairs.v1.json`，记录 `sha256` 哈希到机制报告。

**产物格式（schema `mechanism.gold-pairs.v1`）**：

```json
{
  "schema_version": "mechanism.gold-pairs.v1",
  "seed": 42,
  "annotator": "orchestrator (human), 33/33-review precedent",
  "annotated_at": "2026-08-17T00:00:00Z",
  "pairs": [{
    "question_id": "6a1eabeb",
    "subject": "me",
    "attribute": "charity 5K personal best time",
    "old_value": "26 minutes 40 seconds",
    "new_value": "25 minutes and 50 seconds",
    "old_value_turn_ids": ["...", "..."],
    "new_value_turn_ids": ["answer_a25d4a91_1", "answer_a25d4a91_2"],
    "t_q": "2023-06-25T13:22:00+00:00",
    "t_old": "2023-05-01T09:00:00+00:00",
    "multi_valued": false,
    "gold_action": "SUPERSEDE",
    "notes": ""
  }]
}
```

**入库策略（决策点 5）**：标注产物放 `runs/mechanism/gold/`（gitignored，与 runs/ 其余产物一致；脚本入库、数据不入库，哈希进报告）。若编排者要求可复现审计，可另提交一份脱敏副本——建议维持 runs/ 方案。

### 4.3 指标定义与公式（全部确定性可重算，除 M2 外零 LLM）

记号：`q` = 题；gold 值对 `(subject_q, attribute_q, old_q, new_q, T_q, O_q)`；`old_turns_q` = old_value_turn_ids，`new_turns_q` = new_value_turn_ids（= answer_session_ids，数据自带）；`retrieved_turns(q, m)` = 方法 m 在题 q 的 packed_items 全部 evidence_refs.raw_turn_id 的并集（来自 finalized `retrieval.jsonl`，已实测含该字段）。

**M1 更新决策准确率（存储级，与检索策略无关）**
- 实际动作：从 **etec 存储重放**（见 §4.4 重放协议）中，取"匹配 gold 新值的记忆"的 `metadata.etec.decision.action`；匹配规则（预注册）：`fact_value_key(memory) == canonical(new_q)` 或 `normalize_memory_content(memory.content)` 与 `new_q` 归一化后 token 含盖 ≥0.5 且 event_time == T_q。匹配不到 → `R4 extraction_miss`，从准确率分母剔除并单独计数。
- `M1_accuracy = |{q : a_q == gold_action_q}| / N_found`；`N_found` = 匹配到新值记忆的题数。
- 触发率：`SUPERSEDE_rate = #{a_q == SUPERSEDE} / N_found`，同法 MERGE/REJECT/ADD。
- **根因诊断块（必出）**：对每个 `a_q ≠ gold_action_q` 输出该决策的 rule_hits + 特征向量（semantic_similarity / entity_role_overlap / temporal_overlap / structural_similarity / evidence_consistency / contradiction_score / multi_valued）+ 记忆 pair 的 fact_slot 元数据存在性，按 §3.2 的 R1–R7 分桶计数；分桶脚本与分桶说明一并入库。
- 诊断对照：受控夹具 `runs/ablation/` 的 controlled run 中 SUPERSEDE 决策数（既有产物，只读引用）作为"同代码、显式 fact_value 时可触发"的对照。

**M2 stale-memory error rate（LLM judge，必须缓存 + 固定模型）**
- judge 输入（缓存键）：`{question_id, method, question, prediction, gold_pair}`；judge 输出：`{verdict ∈ {STALE, NOT_STALE, UNANSWERABLE}, rationale}`。I/O 全部写入 `runs/mechanism/judge/`（输入 + 输出 + 缓存 key，逐条记录）。
- 判定规则（预注册，对齐 LongMemEval 官方 judge 的更新容忍）：答案**断言旧值为当前真值且未给出新值** → STALE；答案含新值（即便同时带旧信息）→ NOT_STALE；无法从答案判断 → UNANSWERABLE（单列，不进分子）。
- prompt：官方 update-sensitive judge prompt 的改写版，**写死在脚本内并注明出处**；judge 模型固定 = `deepseek-v4-flash`（与 reader 同网关，决策点 4 备选 minimax-m3）。
- `M2_m = #{STALE} / (#{STALE} + #{NOT_STALE})`，对方法 `m ∈ {full, event_no_etec, etec, vector_rag}`，在 32 KU 上判定（128 次调用）。

**M3 Joint-Evidence Recall@k（纯程序化，离线）**
- 分侧：`old_recall(q,m) = |old_turns_q ∩ retrieved_turns(q,m)| / |old_turns_q|`；`new_recall(q,m) = |new_turns_q ∩ retrieved_turns(q,m)| / |new_turns_q|`（gold 空侧 → 该侧 NA 剔除）。
- `JERecall@8(q,m) = 1[old_recall > 0 AND new_recall > 0]`（新旧证据都在 top-k 打包上下文才算命中；k=8 对应 max_items_per_source=8，实际以 packed 上下文全集计）。
- 每方法汇总 + 配对对比（full vs vector_rag、full vs event_no_etec、full vs etec）。

**M5 内存态探针（存储级，纯程序化，与 Eval B 共用探针机制）**
- 从 etec 存储与 raw 存储分别重放后计算：
  - `under_edit(q, s) = 1` 当且仅当槽位匹配（见下）的 **ACTIVE** 记忆中，`fact_value_key` == `canonical(old_q)`（真实管线无 fact_slot，fallback：内容 token 覆盖 ≥0.5 且实体名覆盖 ≥1，规则预注册并在报告披露命中数）；
  - `over_edit(q, s) = 1` 当且仅当 past-window 探针（§5.2 的 kind=past）检索结果 **未** 命中 `old_turns_q`（旧信息不可恢复）；
  - `VersionChainRecall(q, s) = 1` 当且仅当旧值记忆（若存在）存在 `superseded_by` 链指向新值记忆，或单一 ACTIVE 记忆同时携带两值（MERGE 结果）。
- 输出 etec vs raw 两存储的 `under_edit_rate / over_edit_rate / VersionChainRecall`。

### 4.4 对照臂与统计方法

**重放协议（确定性，零 reader 调用）**：新脚本 `benchmarks/mechanism/replay.py` 复用 ablation.py 的离线模式（ablation.py:470-495 的 `CachedEmbeddingModel(_OfflineOnlyEmbedding, base_run/model_cache)`），对每个样本从 finalized run 的 `samples/<id>.extraction_snapshot.json` 重建 raw store / etec store；embedding 全部命中 base run 缓存，miss 即报错。**不接触** base run 的 model_cache（只读）。

**对照臂（预注册主对比，3 个）**：

| # | 对比 | 隔离因素 | 指标 | 方法 |
|---|---|---|---|---|
| C1 | `full` vs `event_no_etec` | ETEC（同 QEMR 检索） | M1*、M5、M2、M3 | 描述 + 配对 McNemar |
| C2 | `full` vs `vector_rag` | 端到端（事件+ETEC+QEMR vs 原始 chunk 向量） | M2、M3 | 配对 McNemar |
| C3 | `full` vs `etec` | 检索策略 QEMR vs FIXED_VECTOR（同 etec 存储） | M2、M3、M4 | 配对 McNemar（探索性） |

\* M1 为存储级指标：etec/full 共享同一 etec 存储，M1 每个 (q, 存储) 只算一次；raw 存储恒为 ADD（无 ETEC 决策），作为退化对照。

**统计（全部预注册）**：
- McNemar 精确二项（配对 2×2，单侧）；n=32 时翻转 ≥10 → p≈0.043，≥12 → p<0.01；报告精确 p 与 discordant 计数。
- 主对比 C1/C2 不做多重校正（预注册主对比清单）；C3 及分侧 recall 标记为探索性。
- 率指标附 Wilson CI（n≤40）；EM/准确率类用既有 `benchmarks/analysis/bootstrap.py` 配对 bootstrap（沿用 configs/analysis 的 n_boot=10000, seed=0, alpha=0.05 惯例）。
- 只报告点估计 + CI + 精确 p；不做效应量宣称。

### 4.5 代码改动清单（新增，不改既有行为）

| 文件 | 内容 | 是否入库 |
|---|---|---|
| `benchmarks/mechanism/__init__.py` | 包初始化 | 是 |
| `benchmarks/mechanism/gold.py` | gold pair schema、校验、canonicalize 规则 | 是 |
| `benchmarks/mechanism/replay.py` | 离线存储重放（读 sealed snapshot + base model_cache） | 是 |
| `benchmarks/mechanism/eval_a.py` | M1/M3/M5 确定性重算 + 诊断分桶 | 是 |
| `benchmarks/mechanism/stale_judge.py` | M2 judge（固定 prompt/模型、I/O 缓存、输出存档） | 是 |
| `scripts/annotate_gold_pairs.py` | 标注工作表生成 | 是 |
| `tests/mechanism/test_gold.py`、`test_eval_a.py`、`test_replay.py`、`test_stale_judge.py` | 回归测试 | 是 |
| `configs/longmemeval/mechanism-evala.selection.json` | 冻结清单 | 是 |
| `configs/longmemeval/mechanism-40.toml` | 40 题 run 配置（run_id_prefix `m13-longmemeval-mechanism40`） | 是 |
| `runs/mechanism/gold/…`、`runs/mechanism/evala-<ts>/`、`runs/mechanism/judge/…` | 产物（含 FINALIZED.json） | 否（gitignored） |

M2 judge 的输入/输出与缓存 key 记录在 `runs/mechanism/judge/`；M2 结论只引用可重放记录。

---

## 5. Eval B：时序有效性评测（temporal-reasoning）

### 5.1 router 全量预筛（已执行，见 §3.1）

- 命令（确定性，零成本）：对 500 题跑 `QueryRouter.route`，输出算子分布 + 每题 `rule_hits/matched_spans` 明细 → `runs/mechanism/router_screen/router-screen.json`。
- 预期输出（已实测，直接冻结为预注册基线）：`none 391 / earliest 42 / latest 29 / duration 26 / sequence 11 / at 1 / between·before·after 0`。
- 用途：(1) 如实记录"自然数据 interval 约束近零"；(2) earliest/latest/sequence 在 ms 8 TR + 新增 16 TR 上的触发明细（historical packed 计数）。

### 5.2 合成时间窗探针构造规则（预注册模板，一次性冻结）

**载体**：32 KU 题（gold 值对已标注）+ 16 新增 TR 题（仅当注解能从 question/answer 导出 subject/attribute 才构造；不可导出 → 排除并记录，预注册此规则）。

**探针模板**（针对每个 gold 值对）：

| kind | 查询模板 | reference_time | 窗口 W |
|---|---|---|---|
| `now` | `What is {subject}'s {attribute} now?` | `question_date`（T_q 之后） | 无（NONE 算子） |
| `past` | `What was {subject}'s {attribute} before {year(t_old 的下一年)}?` | `question_date` | `(-∞, 12-31 of t_old 的年份]`（BEFORE 算子） |
| `between` | `What was {subject}'s {attribute} between {year(t_old)} and {year(t_q)}?` | `question_date` | `[year(t_old)-01-01, year(t_q)-12-31]`（BETWEEN 算子） |

- 约束：`between` 仅当 `year(t_q) - year(t_old) ≥ 2` 才构造（保证窗口非退化）；`past` 仅当 `year(t_old) ≤ year(T_q) - 1`。
- 每个探针带 `probe_id = sha256(kind + question_id)`、`gold_inside_window = old_turns_q`（past/between）、`gold_outside_window = new_turns_q`（past 时新值断言在窗口外）、`store 标记`。
- 探针清单在跑之前冻结为 `configs/longmemeval/mechanism-probes.json`（含每条探针的完整文本与预期窗口）。

**合法性论证（为什么不算操纵数据）**：
1. 探针事实内容（subject/attribute/old/new/t_q/t_old）全部来自**真实对话的人工标注**，没有虚构事实；
2. 探针只改变**查询措辞与 reference_time**，用于触发既有的、未修改的 interval 过滤路径（retrieval.py:712-819），不引入任何新检索能力；
3. 探针**不产生答案**（无 reader 调用），只度量"检索决策是否把窗口外的项排除、把窗口内的项保留"——这是对已有机制路径的受控观测，等同单元级探针；
4. 探针结果**永不进入** benchmark 分数表，产物命名空间 `runs/mechanism/evalb/` 与报告口径严格隔离；
5. 全部模板与清单在 run 前预注册于本 spec 与 `mechanism-probes.json`，事后不可增删改。

### 5.3 M4 指标定义（纯程序化）

对每个探针 p、臂（方法/存储）a，从探针重放 payload（含 exclusions 明细，仿 ablation.py:931-937 格式）计算：

- `ExclusionHit(p,a) = 1[#exclusions(reason == "temporal_interval_excluded") > 0]`；臂级 = 命中探针数 / 探针数。
- `Contamination(p,a) = #{packed item 携带 gold_outside_window 证据} / #{packed items}`（past 探针：新值证据进入"过去窗口"上下文 = 污染）；臂级 = 均值 + CI。
- `ValidRetention(p,a) = 1[packed 证据 ∩ gold_inside_window ≠ ∅]`（过去窗口查询能找回旧值）；臂级 = 保留率。
- `HistoricalPackedCount(p,a)` = packed 中 `historical=true` 项数（均值）。
- 附加：`temporal_interval_excluded` 计数的**明细**（memory_id、operator、bound）存档；真实数据部分（500 题 1 个 AT 题）单独一行记录。

**臂**：4 存储×策略组合 = `(etec_store, QEMR)`, `(etec_store, FIXED_VECTOR)`, `(raw_store, QEMR)`, `(vector_store, FIXED_VECTOR)`，对应方法 full / etec / event_no_etec / vector_rag。主对比：full vs event_no_etec（同 QEMR，ETEC 存储差异）+ full vs etec（同存储，检索差异）。

### 5.4 离线重放方式（探针 run）

- runner：新脚本 `benchmarks/mechanism/probes.py`（新增，不改 ablation.py）。
- 每个探针 run 目录 `runs/mechanism/evalb/probes-<ts>-<arm>/`：
  1. 复制 base run（`longmemeval-test20-ms` 或 `mechanism40`）的 `model_cache` 到探针 run 内（只读源）；用本地 embedding 端点（`127.0.0.1:11436`，qwen3-embedding-0.6b，无配额）预计算探针 query 的 embedding 并写入探针 run 缓存（记录所用 embedding 模型 identity）；
  2. 用 `_OfflineOnlyEmbedding` 模式重放：任何未命中缓存的 embedding 查找立即报错（复用 ablation.py 的严格离线约定）；
  3. 检索 harness 照常（QEMR/FIXED_VECTOR），`budget_tokens=4096`、`reference_time` = 探针 reference；
  4. **零 reader / 零 extractor 调用**（run manifest 的 reader/extractor identity 记录为 `n/a`，执行期断言 0 次生成）。
  5. 写 manifest + `retrieval.jsonl`（含 exclusions 明细）+ FINALIZED.json（复用 `benchmarks/common/artifacts` 封存机制，artifact_class=PUBLICATION）。
- 校验：`validate` 步骤断言 (i) 全部探针有行；(ii) reader 调用数 = 0；(iii) cache miss = 0；(iv) exclusions 明细字段完整。

---

## 6. 500 样本一致性 run（(b)）

### 6.1 配置

- 新配置 `configs/longmemeval/main500.toml`：复制 `main.toml`，`run_id_prefix = "m13-longmemeval-s500"`；方法 6 个全量；`max_input_tokens = 4096`、`max_extraction_tokens = 262144`、`max_candidates_per_source = 128`、`max_items_per_source = 8`；provider 沿用 `openai_compatible`（EEM_LLM_BASE_URL 网关覆盖 base_url，已核实 providers.py:144）。
- 运行：`uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/main500.toml --output-root runs/publication`（新 run 目录）；无 sample_ids → 全量 500。
- 与既有主 run 的关系：全新 run（新提取快照），**不要求**与 6m/ms 预测一致（提取含模型随机性）；机制一致性以指标分布而非逐题预测为准。

### 6.2 调用量估算（预注册）

- 每样本 7 次 LLM 调用（1 提取 + 6 reader，已核实 `extract_event_snapshot` 单次截断 + 每方法 1 次 generate）→ 500 × 7 = **3,500 次**。
- 提取输入按 `max_extraction_tokens=262144` 截断（超长样本 `extraction_truncated=true`，在样本级记录，报告中披露截断样本数）。
- embedding 走本地端点（无配额）；resume 重试损耗另计（§6.3）。
- Eval A/B 追加调用：40 题 run 280 次 + M2 judge 128 次 ≈ **410 次**。合计 ≈ 3,900 次 + 重试。

### 6.3 配额降级预案（预注册阶梯，不可静默缩小）

- **L0（目标）**：500 × 6 方法全部 finalize。
- **L1**：遇 `GoUsageLimitError`/网关错误 → `--resume-dir` 断点续跑（per-sample 写一次不可变，manifest drift 拒跑，已有机制，run.py:357-397）；配额恢复后继续，不改变设计。
- **L2（声明式降级，必须写入报告）**：若配额连续中断无法完成 6 方法 → 只跑 4 个记忆方法（dropped `no_memory`/`full_context`，即 500 × (1 提取 + 4 reader) = 2,500 次），方法集在 manifest/report 中如实声明，绝不在同一表格混并不同范围。
- **L3（声明式降级）**：若仍不足 → 采用**预冻结**的分层子集（seed 42 + sha256 排序，按 question_type 等比抽取 ≤250 题，清单预写入 `configs/longmemeval/main500-fallback.selection.json`）；报告如实标注范围与配额原因。
- **兜底（不消耗配额）**：机制指标（M1/M3/M5 及 500 run 中 78 KU 的 JERecall、触发率）全部可离线确定性重算（§4.4 协议），500 run 即使降级到 L2/L3 也**不阻断 (a) 交付**。
- 降级决策记录在报告"方法学披露"节（config hash、manifest、实际样本数、方法与原因）。

### 6.4 resume 协议

- 续跑必须指向同一 `--resume-dir`；`--run-dir`/`--resume-dir`/`--output-root` 互斥（run.py:321-341）；manifest drift 拒绝启动。
- 已封存样本不可变；续跑只补齐缺失样本。
- 完成条件：`FINALIZED.json` 存在且 `validate_report`/`load_finalized` 通过；`sample_validation.valid == true`。

### 6.5 一致性校验项（(b) 的验收判据，预注册）

1. **溯源覆盖率**：packed evidence 携带 `raw_turn_id` 的比例 = 1.0（ms 为 6656/6656）。
2. **预算满装**：记忆四方法 `packing_bound` 题占比 ≈ 1.0（ms 为 24/24）。
3. **0 分格与失败归因分布**：自动 taxonomy 分布（taxonomy.py 九类）在 500 与 ms/6m 结构同构（含 answer_present_reader_wrong 为主）；分布以列联表 + 占比报告，**不做显著性**。
4. **机制指标不漂移**：500 run 内 78 KU 的 M1 触发率/M3 JERecall 与 32 KU 子集一致（点估计 + Wilson CI 重叠即可，不做假设检验）；ETEC 动作总计数（ADD/MERGE/SUPERSEDE）报告。
5. **efficiency**：tokens/query、llm_calls/query 与既有 run 同量级（描述性）。
- 输出：`runs/mechanism/consistency/consistency.json + .md`，逐项引用产物路径。

---

## 7. 公平性约束清单（沿用既有 Gate，逐条核对）

- 同数据（`longmemeval_s_cleaned.json`，dataset hash 进 manifest）；同 reader（deepseek-v4-flash via opencode.ai/zen/go/v1）；同提取 prompt 版本（`shared-snapshot.v1`）；同预算 4096；同 seed 机制（42）；同 judge（固定模型 + 缓存）。
- 事件方法共享同一 extraction snapshot（run.py:385、420-426）。
- 机制指标全部绑定**同一 run 内**的 finalized 快照；跨 run 预测差异不作确定性证据。
- 新脚本不改变任何既有方法的行为路径（只读重算 / 新增独立 runner）。

---

## 8. 产物清单（(a)/(b)/(c)）

### (a) 机制评测

| 产物 | 路径 | 格式/校验 |
|---|---|---|
| 40 题 run | `runs/publication/m13-longmemeval-mechanism40-<ts>/` | 6 方法、FINALIZED、git clean |
| selection 冻结清单 | `configs/longmemeval/mechanism-evala.selection.json` | 入库，冻结 ids |
| gold 值对 | `runs/mechanism/gold/longmemeval-kupairs.v1.json` | schema v1、校验脚本通过、哈希记录 |
| Eval A 指标表 | `runs/mechanism/evala/metrics.json` + `metrics.md` | 确定性重算、每数带行号引用 |
| M2 judge 存档 | `runs/mechanism/judge/`（inputs/outputs/cache keys） | 逐条可重放 |
| Eval B 探针 run | `runs/mechanism/evalb/probes-<ts>-<arm>/`（4 臂） | FINALIZED、零 reader、明细 exclusions |
| M4 指标表 | `runs/mechanism/evalb/m4.json` + `m4.md` | 纯程序化 |
| router 预筛 | `runs/mechanism/router_screen/router-screen.json` | 500 题算子分布 + rule_hits |
| **机制报告（content-addressed）** | `runs/mechanism/reports/sha256:<…>/report.md + report.json` | `benchmarks/mechanism/report.py` 生成，含自校验（模仿 analysis 封存模式）；或手写报告且**每个数字带产物路径**（决策点 7） |
| 一致性产物 | `runs/mechanism/consistency/consistency.json/.md` | 5 项判据逐项 |

### (b) 500 一致性

| 产物 | 路径 |
|---|---|
| run 配置 | `configs/longmemeval/main500.toml`（+ 可选 fallback selection） |
| 500 run | `runs/publication/m13-longmemeval-s500-<ts>/`（FINALIZED） |
| 完整 M15 报告 | `runs/analysis/sha256:<…>/`（既有 report.py + `configs/analysis/main500.toml` 变体，config 需入库避免重演 a0907e94 的 config 未入库问题） |

### (c) 报告口径（README / EVALUATION 必须更新的数字）

1. ETEC 动作触发率（SUPERSEDE/MERGE/REJECT 计数与率，32+78 KU 两个口径）+ 不触发根因分桶（R1–R7）。
2. M2 stale 率（full vs event_no_etec vs etec vs vector_rag，n=32，judge 模型/缓存路径）。
3. M3 JERecall@8（32 KU 与 78 KU 两个口径）。
4. M5 under/over-edit、VersionChainRecall。
5. M4 ExclusionHit/Contamination/ValidRetention（探针 4 臂）+ 自然数据 interval 触发（500 题仅 1 AT）。
6. 500 run：总体/分能力点估计 + CI（不做显著性）、5 项一致性判据结果、配额降级声明（若发生）。
7. 对照臂修正说明（§3.3：etec vs event_no_etec 非等检索策略，ETEC 隔离用 full vs event_no_etec）——**必须写进 EVALUATION.md 方法学节**。

更新文件：`README.md`、`docs/EVALUATION.md`、`docs/STRONG_RESULTS_SMALL_SAMPLE.md`（新节）、`docs/INTERVIEW_KIT.md`（Q3/Q7 与状态表）；每处数字带 `runs/...` 路径或 content-addressed 报告链接。

---

## 9. 风险与已知限制（诚实清单）

1. **提取随机性**：跨 run 快照不一致（已记录 118b2229 235 vs 263 事件先例）→ 机制指标只绑定各自 finalized 快照，跨 run 只比指标分布不比逐题。
2. **interval 题目近零**：500 题仅 1 AT、0 BETWEEN/BEFORE/AFTER（已实测）→ 真实数据 M4 必然近零，结论依赖探针；探针的受控性论证见 §5.2。
3. **judge 模型可用性与偏差**：默认 deepseek-v4-flash 与 reader 同模型，存在同源偏差风险；Ark minimax-m3 配额不稳。缓解：固定模型 + 缓存 + 官方 judge prompt + 判定规则二元化（STALE/NOT_STALE 均需给出依据）；决策点 4。
4. **配额中断**：周配额曾耗尽（GoUsageLimitError 先例）→ resume + L2/L3 预注册阶梯 + 机制指标离线兜底（§6.3）。
5. **本地 embedding 隧道依赖**：探针预计算 query embedding 需要 gpu-5090 隧道（`ssh -L 11436:...`）；隧道不可用 → 探针 run 阻塞，需先恢复隧道（不影响 Eval A 的 M1/M3/M5 存储重放，因为那些全部命中 base run 缓存）。
6. **gold 标注主观性**：subject/attribute 归一化、old_value 选择存在主观空间 → 标注校验脚本 + 预注册 fallback 匹配规则 + 报告披露匹配命中数；标注数据不入库（决策点 5）。
7. **M5 槽位匹配无 fact_slot**：fallback 规则（token 覆盖 + 实体覆盖）可能误匹配 → 预注册阈值并报告误匹配候选。
8. **500 run 提取截断**：超长样本截断可能影响机制触发率 → 逐样本记录 `extraction_truncated`，一致性校验按截断/未截断分层报告。
9. **机制报告基建**：`report.py` 现有模板不含机制表 → 新增机制报告脚本或手写；若新增，须独立于 analysis 管线且带自校验（决策点 7）。

---

## 10. 执行顺序与检查点

依赖：Phase 3（小样本机制，可先于 500 run 全部完成并 finalize）→ Phase 4（500 run，配额密集型）→ Phase 5（报告）。Phase 3 与 Phase 4 的**设计冻结**（selection、probes、配置）都必须在 Phase 3 开始前完成（预注册原则）。

```
Phase 3 设计冻结（本 spec 冻结后即生效）
  ├─ 3.1 冻结 selection.json / probes.json / 三份 toml 配置 / judge prompt 文本
  ├─ 3.2 gold 标注（32 KU）+ 校验脚本通过                    ── C1 门
  ├─ 3.3 新机制代码（gold/replay/eval_a/stale_judge/probes/report + 测试）
  │     uv run pytest tests/mechanism -q 全绿                ── C0 门
  ├─ 3.4 mechanism40 run（40 题 × 6 方法，~280 调用）finalize ── C2 门
  ├─ 3.5 Eval A 离线指标（M1/M3/M5 对 32 KU；M2 judge 128 调用）
  │     与 ms 切片已知事实核对（MERGE 计数 = 4dfccbf8/f0853d11 各 1）
  │                                                         ── C3/C4 门
  ├─ 3.6 Eval B 探针（4 臂离线重放，零 reader）finalize        ── C5 门
  ├─ 3.7 (a) 机制报告 content-addressed + validate 通过        ── C6 门
Phase 4（配额密集，可与 3.7 并行启动）
  ├─ 4.1 main500 run（L0/L2/L3 阶梯 + resume）finalize        ── C7 门
  ├─ 4.2 一致性校验 5 项 + 78 KU 机制重算                     ── C7b 门
Phase 5
  ├─ 5.1 500 M15 报告（既有 report.py）+ 机制报告并存          ── C8 门
  ├─ 5.2 README/EVALUATION/STRONG_RESULTS/INTERVIEW_KIT 更新  ── C9 门（验收 (c)）
```

**检查点判据**：

| 门 | 通过 | 不通过 |
|---|---|---|
| C0 | `pytest tests/mechanism -q` 全绿；ruff/mypy 通过（`uv run ruff check .`、`uv run mypy src`） | 修复到绿为止 |
| C1 | 32 KU pairs 全部标注且校验脚本 0 error；哈希记录 | 补标注/修正后重验 |
| C2 | 40/40 样本、FINALIZED、`sample_validation.valid`、git clean | 续跑修复，不得改清单 |
| C3 | M1/M3/M5 表格生成且与 ms 已知事实一致（MERGE 2 次位置吻合） | 修脚本，不修数据 |
| C4 | 128 条 judge 全部有缓存 I/O 记录、无不可判 | 缺缓存记录 → 重跑 judge |
| C5 | 探针 4 臂全部 FINALIZED、reader 调用 0、cache miss 0、exclusions 明细完整 | 补/重放 |
| C6 | 机制报告 validate valid=true；结论方向与预注册一致或如实标注变更 | 补数据/如实声明 |
| C7 | 500 run finalize（L0）或按预注册阶梯 + 声明（L2/L3）；5 项一致性判据逐项通过 | 按阶梯继续，绝不静默缩小 |
| C7b | 78 KU 机制指标 CI 与 32 KU 重叠；溯源 1.0；满装 ≈1.0 | 差异如实披露并诊断 |
| C8 | M15 报告 validate valid=true（config 入库） | 修 config 再生成 |
| C9 | 四份文档更新，每个数字可溯源 | 补路径 |

**每阶段结束报告**：改动的文件、测试结果、产物路径、未解决风险（沿用 AGENTS.md 任务协议）。

---

## 11. 附录

### A. 命令速查

```bash
# C0: 新代码质量门
uv run pytest tests/mechanism -q
uv run ruff check .
uv run mypy src

# 3.4 40 题 run（按冻结清单）
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/mechanism-40.toml \
  --output-root runs/publication \
  --sample-ids $(cat configs/longmemeval/mechanism-evala.selection.json | jq -r '.sample_ids[]')

# 3.6 探针重放（4 臂，零 reader）
uv run python -m benchmarks.mechanism.probes \
  --config configs/longmemeval/mechanism-probes.json \
  --base-run runs/publication/longmemeval-test20-ms \
  --base-run runs/publication/m13-longmemeval-mechanism40-<ts> \
  --output-root runs/mechanism/evalb

# 4.1 500 run
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/main500.toml --output-root runs/publication
# 续跑：uv run python -m benchmarks.longmemeval.run --config ... --resume-dir runs/publication/m13-longmemeval-s500-<ts>

# 4.2 一致性 + 5.1 报告
uv run python -m benchmarks.mechanism.consistency --source-run <s500> --out runs/mechanism/consistency
uv run python -m benchmarks.analysis.report --config configs/analysis/main500.toml \
  --source-run runs/publication/m13-longmemeval-s500-<ts> --output-root runs/analysis
```

### B. 指标公式速查

| 指标 | 定义 | 数据源 | LLM |
|---|---|---|---|
| M1 | 见 §4.3；`M1_accuracy = 正确动作/N_found`，R1–R7 分桶 | 存储重放（sealed snapshot + cache） | 无 |
| M2 | `#STALE/(#STALE+#NOT_STALE)`，按方法 | sealed predictions + judge（缓存） | 是（固定模型） |
| M3 | `JERecall@8 = 1[old_recall>0 ∧ new_recall>0]` | finalized retrieval.jsonl | 无 |
| M4 | ExclusionHit / Contamination / ValidRetention / HistoricalPackedCount | 探针重放 payload（含 exclusions 明细） | 无 |
| M5 | under_edit / over_edit / VersionChainRecall | 存储重放 + past 探针 | 无 |

### C. 参考文件

- `docs/METHODOLOGY_CHANGE.md`（功效、一致性职责、运行约束）
- `docs/STRONG_RESULTS_SMALL_SAMPLE.md`（机制证据链现状）
- `docs/EVALUATION.md`（协议与门槛、诚实状态表）
- `configs/longmemeval/test20-ms.selection.json`（seed 机制先例）
- `benchmarks/experiments/ablation.py`（离线重放与 exclusions 明细先例）
- `benchmarks/analysis/taxonomy.py`（失败归因九类）
- `docs/archive/RESUME_METRICS.md`、`docs/INTERVIEW_KIT.md`（简历口径约束）

---

## 12. 设计审核要点（给编排者拍板）

| # | 决策点 | 我的建议 |
|---|---|---|
| 1 | **对照臂修正**：现状 `etec`=FIXED_VECTOR、`event_no_etec`=QEMR，"etec vs event_no_etec"同时混入检索策略与 ETEC 两个因素，不构成"只差 ETEC"。是否批准以 `full vs event_no_etec`（同 QEMR）作为 ETEC 隔离主对照，并保留 `etec` 仅用于"同存储下检索策略"（full vs etec）？ | **批准**（这是本 spec 最重要的口径修正；否则 M1/M5 的归因无效） |
| 2 | **SUPERSEDE 结构性不可达**：预筛已确认提取管线从不写 fact_slot/fact_value → contradiction_score≡0 → SUPERSEDE 在真实数据上不可触发。是否接受 (a) 以"可达性诊断 + R1–R7 分桶 + 受控夹具对照"作为主结论形态，而不修改提取/整合代码制造触发？ | **接受**（机理诊断本身就是研究结论；"同代码夹具可达、真实管线不可达"是可发表的诊断故事） |
| 3 | **Eval B 证据来源**：500 题 interval 约束仅 1 题（AT），合成时间窗探针是 M4 唯一有效证据。是否批准探针方案（受控、不进基准题集、pre-registered 模板）？ | **批准**（§5.2 合法性论证；探针不产生答案，只是检索路径观测） |
| 4 | **judge 模型**：M2 默认 deepseek-v4-flash（与 reader 同模型，网关同源）；备选 Ark minimax-m3（独立于 reader，但每日配额不稳）。 | **默认 deepseek-v4-flash + 缓存 + 官方 prompt**；若你想规避同源偏差且配额允许，再切 minimax-m3（单次切换，不得混合） |
| 5 | **gold 标注产物入库**：放 `runs/mechanism/gold/`（gitignored，脚本入库、数据不入库、哈希进报告）还是提交仓库？ | **runs/ 方案**（与 33/33 review 先例一致；数据不入库符合 AGENTS.md 的 data/raw 惯例） |
| 6 | **新增样本构成**：24 KU + 16 TR（保持与 ms 切片同构、TR 支撑 earliest/latest 真实触发率）还是纯 KU 40 题（强化 M1/M2/M3 功效）？ | **24 KU + 16 TR**（机制评测的 TR 维度必须有真实数据记录，哪怕只是"近零"；且与 ms 切片可合并成 32 KU + 24 TR 统一口径） |
| 7 | **机制报告形态**：新增 content-addressed 机制报告脚本（`benchmarks/mechanism/report.py`，独立于 analysis 管线，带自校验）还是手写 Markdown（每个数字带产物路径）？ | **新增脚本**（与既有 FINALIZED/content-addressing 惯例一致，可审计；工作量小） |
| 8 | **500 run 配额确认**：6 方法 × 500 ≈ 3,500 次 LLM 调用 + Eval A/B ≈ 410 次，合计 ≈ 3,900 次。是否批准此配额预算与 L0→L3 降级阶梯（L2/L3 必须声明，机制指标离线兜底）？ | **批准**（阶梯已预注册；即便降级到 L2，(a) 交付不受影响） |

---

## 13. 编排者审批（2026-08-17，冻结后即生效）

编排者（主 agent）逐项核实了 §3 的三条承重断言（亲自读码确认，非转述）：

1. `_contradiction_score` 在 `not _same_fact_slot` 时返回 0.0（consolidation.py:876），`_same_fact_slot`
   要求 `metadata["fact_slot"]` 存在且相等（:943-946）；提取管线 `_memory_record`（extraction.py:984-1003）
   只写三个 metadata 字段、从不写 fact_slot/fact_value → **SUPERSEDE 结构性不可达的假设成立**。
2. `_contradiction_score = min(1.0, 0.6 + entity_role*0.2 + structural*0.2)`（:886），fact_slot 匹配后
   可达 ≥0.7 阈值 → 修复 fact_slot 缺失后 SUPERSEDE 在真实数据上可触发（Phase 3B 的可行性基础）。
3. 方法→检索策略矩阵 `etec`=FIXED_VECTOR、`event_no_etec`=QEMR（run.py:153-158）→ "etec vs
   event_no_etec"确为混因素对照，**ETEC 隔离主对照必须用 full vs event_no_etec**。

审批决议（对应 §12 决策点 1-8）：

1. **批准**对照臂修正：`full vs event_no_etec`（同 QEMR）为 ETEC 隔离主对照；`full vs etec` 为检索策略
   隔离（探索性）。EVALUATION.md 方法学节必须写入此修正及原因。
2. **批准**"可达性诊断 + R1–R7 分桶 + 受控夹具对照"为 (a) 的主结论形态（预注册方向 E1）；**追加
   Phase 3B（条件预注册）**：若 R1 被实证确认，则实施最小定向修复——提取输出 fact_slot/fact_value
   元数据（prompt 版本号递增、随代码入库、测试覆盖），重跑 mechanism 切片，如实报告
   "before: SUPERSEDE 0 → after: X/Y、M1 决策准确率 Z%"的对照。该修复是填补真实管线的元数据缺口
   （fixture 已有、schema 已定义），不是调参凑数；前后数字均入报告。Phase 3B 触发与否取决于 R1 实证
   结果，不允许在 R1 未确认时提前实施。
3. **批准**合成时间窗探针（受控、独立命名空间 `runs/mechanism/evalb/`、模板预注册、不产生答案）。
4. **批准**M2 judge = `deepseek-v4-flash`（与 reader 同源，如实标注同源偏差风险），全部 I/O 缓存 +
   固定 prompt + 二元判定规则；不引入 minimax-m3（配额不稳，避免方法混杂）。
5. **批准**gold 标注产物放 `runs/mechanism/gold/`（gitignored，哈希进报告）；脚本入库。
6. **批准**新增样本 = 24 KU + 16 TR（冻结清单 §4.1），与 ms 切片合并为 32 KU + 24 TR 统一口径。
7. **批准**新增 content-addressed 机制报告脚本 `benchmarks/mechanism/report.py`（独立于 analysis
   管线，带自校验）。
8. **批准**配额预算与 L0→L3 降级阶梯；降级必须声明式记录。

追加执行约束（本审批补充）：

- **探针→算子映射断言**：C5 门必须断言每个探针经 router 确定性解析出的算子与预注册模板一致
  （now→NONE、past→BEFORE、between→BETWEEN），不一致即该探针 fail，确保 M4 确实测到了 interval 路径。
- **500 run 提取版本**：500 run 在 Phase 3 设计冻结（含 Phase 3B 的提取版本决定）之后启动；manifest
  记录 extractor_prompt_version，一致性校验按版本口径报告。
- **报告口径**：(a) 的最终结论必须落在 §1.2 两种形态之一；Phase 3B 若触发，before/after 都必须入
  报告与 README，不得只写 after。
