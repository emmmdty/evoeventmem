# 9/10 验收报告（O09：ETEC 机制评测 + 大样本一致性）

> 日期：2026-08-18
> 任务：`tasks/optional/O09_mechanism_evaluation.md`
> 预注册方案：`docs/superpowers/specs/2026-08-17-o09-mechanism-eval-and-500-consistency-design.md`（含 §13 编排者审批决议）
> 口径：所有数字均可溯源到 `runs/` 不可变产物或 content-addressed 报告；LLM 判断未在本周期产出（配额阻断，见 §b 风险）。
> 结论：(a) 机制诊断完成（**null 结果：ETEC 在真实数据无可评估面**）、(b) 离线一致性 + 功效论证完成（500 run 配额阻断、已预注册 §6.3 末段兜底路径待续；**consistency.json 现有 4 个 entry，4-run 表由 consistency.py 结构化产出，非多源手动编译**）、(c) 机理解释报告完成。三项均落到"真实、可解释、机理上成立"的口径；端到端 QA 增益仍如实记录为无（与基线一致）。**独立审计评 9/10（续作后，见 `docs/9of10_AUDIT.md` 第六部分）**。

---

## 摘要：7/10 → 9/10 的证据增量

| 维度 | 7/10 状态 | 9/10 增量（本任务） |
|---|---|---|
| (a) 机制实证 | SUPERSEDE/interval 从未触发、stale 未度量 | **级联屏障诊断** R1+R1b+R3：实证 SUPERSEDE 在真实数据结构性不可达的根因链；夹具对照 4/12 + v2.1 单测证明 **consolidation 逻辑本身**有效（**不证明 ETEC 在真实数据有价值**——恰恰相反，真实数据 0/8 实测 SUPERSEDE，ETEC 无操作面）；**M3 joint recall（old+new 侧，7 对 gold pair）JERecall@8=1.0（结构性 null，非 ETEC 优势——SUPERSEDE=0 → 旧值 ACTIVE → 总可检索）**；**M4 ExclusionHit=0（router "before {year}" 解析包含该年 → 0 排除，如实记录非凑数字）+ Contamination≈0.11 + ValidRetention=1.0**；router 500 题预筛 interval 算子近零。结论形态 = "机理上成立"（spec §1.2 形态 2，**null 结果**）。SUPERSEDE "0/32" 实为 0/8 实测 + 0/24 结构外推（同管线 v1，mechanism40 未 finalize）。**M1=1/8（22d2cb42 ADD coincidental match，flagged）**。 |
| (b) 大样本一致性 | 500 降级为待办 | **离线一致性 + 功效论证**：4 个 finalized 24 样本 run（n=96）+ 1986-LoCoMo 上重算 5 项机制指标不漂移；500 run 配额阻断，预注册 §6.3 末段兜底路径待续。**consistency.json 现有 4 个 entry（r2/6m/ms/recheck），Wilson 95% CI 两两重叠可从 cited artifact 复算（非多源手动编译）**。 |
| (c) 机理解释 | 缺"为何无增益"机理报告 | **机理报告**：端到端无增益 = 提取管线结构性缺口（无 SUPERSEDE 触发面）+ 基准 judge 容忍陈旧（无计分面）+ reader 主导错误（40-50% 官方、26/33 本仓）；附 LongMemEval-V2/BEAM/Mem0/Zep 文献佐证。 |

---

## (a) 机制评测：冲突更新与时序有效性

### a.1 设计与预注册

完整方案见 spec §4-5、§13。两个子评测：
- **Eval A（冲突更新，knowledge-update）**：ETEC 动作触发计数/率、R1-R7 根因分桶、M3 Joint-Evidence Recall、（M1/M2/M5 因 SUPERSEDE=0 退化，见 §a.6）。
- **Eval B（时序有效性，temporal-reasoning）**：router 全量确定性预筛 + interval 排除代码路径佐证（既有单元测试）。

对照臂修正（spec §3.3、§13 决议 1，已写入方法学节 §c.2）：`etec`=FIXED_VECTOR、`event_no_etec`=QEMR（run.py:153-158），"etec vs event_no_etec"混入检索策略与 ETEC 两因素；**ETEC 隔离主对照 = `full` vs `event_no_etec`（同 QEMR）**。**重要标注：SUPERSEDE=0 时 full vs event_no_etec 是结构性 null（ETEC 不触发则检索侧无差异），不是"公平对照无信号"，是"公平对照在当前数据下无信号" → ETEC 在真实数据无可评估面。**

### a.2 R1 实证：提取从不写 fact_slot → SUPERSEDE 结构性不可达

**实证（ms 8 KU，finalized `runs/publication/longmemeval-test20-ms`）**：
- 提取事件总数 1821；带 `fact_slot` 的 events = **0**；带 `fact_value` = 0；带 `multi_valued` = 0。
- metadata keys 全集 = `{extractor_prompt_version, source_dataset, source_sample_id}`（仅 3 个）。
- ETEC 动作（online `samples/<id>.json → ingestion.etec.actions`）：ADD 1821 / MERGE 0 / **SUPERSEDE 0** / REJECT 0。

**代码逻辑链（亲自核实，consolidation.py / extraction.py @ git `e521e31`）**：
1. `_contradiction_score`（consolidation.py:876）：`if multi_valued or not _same_fact_slot(source, target) or _same_fact_value(...): return 0.0`。
2. `_same_fact_slot`（:943-946）：要求 `metadata["fact_slot"]` 存在且相等；`fact_slot_key`（:935-940）对缺失返回 None。
3. 提取管线 `_build_memory`（extraction.py:998-1003）只写三个 metadata 字段，**从不写 fact_slot/fact_value/multi_valued**（grep 全库确认；fact_slot 仅出现在 `benchmarks/experiments/fixtures/etec_stress_v1.json` 与 `benchmarks/etec_smoke.py`）。
4. SUPERSEDE 分支（consolidation.py:398-427）需 `contradiction_score >= 0.7`（:399）→ 0.0 < 0.7 → **结构上不可达**。

**产物**：`runs/mechanism/evala/metrics.partial.json`（content_hash `sha256:0d3c5a2f1539272d8a6113f68104adf53b8b90bffd873306536a43cd475204e6`）。

### a.3 R1b 实证：补 fact_slot 后暴露第二屏障（point-interval 不重叠）

按 spec §13 条件预注册 Phase 3B，实施 v2 提取（`_EventDraft` 加 fact_slot/fact_value/multi_valued、prompt 加规则+few-shot、PROMPT_VERSION `event-extraction.v1`→`v2`）。

**v2 微验证（6a1eabeb，真实 LLM 提取）**：
- events 282（v1 225，+25.3% 未退化）；**fact_slot 覆盖率 270/282 = 95.74%**；LLM slot 归一化可靠（248 distinct slots，17 个同 slot 不同 value 的候选对）。
- **SUPERSEDE = 0**（v1 也是 0）。

**根因 R1b**：`_contradiction_score`（consolidation.py:880-885）在 `0.6 + ...` 公式（:886）**之前**检查 interval overlap：
- 提取只设 `event_time`（point interval `(t,t)`），**从不设 `valid_from`**（finalized snapshot 确认 valid_from 全 null）。
- 同一 fact 在不同时间有不同值 → 两个 point interval `(t1,t1)` 与 `(t2,t2)` 且 `t1≠t2` → 不重叠 → contradiction=0 → SUPERSEDE 不可达。
- 同 `event_time` 的 pair 会重叠但触发 `equal_fact_effective_time` → REJECT（非 SUPERSEDE）。

### a.4 R3 实证：补 valid_from 后暴露第三屏障（LLM 输出形态）

按 spec §13 延伸，实施 v2.1（`_build_memory` 当 `fact_slot` 非空时设 `valid_from=event_time`，open interval `(t,+∞)`）。这是语义正确修复（事实自被断言起生效直到被取代；etec_stress fixture 正是用此语义）。

**v2.1 微验证（6a1eabeb，cache 复用 v2 LLM 响应，0 新配额）**：
- 187/270 fact events 获得 valid_from=event_time（83 个无 event_time 的 fact 正确保持 None）。
- **SUPERSEDE 仍 = 0**。

**根因 R3**（29 候选对分类）：
| 屏障 | 对数 | % | 位置 | rule |
|---|---|---|---|---|
| multi_valued | 18 | 62% | `_contradiction_score:876` 短路 → ADD | `explicit_multi_valued_slot` |
| equal_effective_time | 8 | 28% | 同 event_time → REJECT | `equal_fact_effective_time` |
| missing_valid_from | 3 | 10% | event_time=None → REJECT | `missing_fact_effective_time` |

LLM 过度标 `multi_valued=True`（37/270=14% 的事实）、event_time 粒度粗（28% 同时间戳）、31% fact 事件缺 event_time。

**在此停止追屏障**：继续调提取 prompt 凑 SUPERSEDE 触发即"为凑数字而调优"（违反 AGENTS.md 硬约束）。R1+R1b+R3 三层屏障本身就是研究结论。**诚实标注：R3 理论上可通过 slot 归一化/event_time 细化等工程修复部分缓解，但选择不做（borderline 调参，违反 AGENTS.md "不调参凑数"原则）。** v2/v2.1 源码已回退（生产保持 v1，与既有 finalized 一致）；实验 diff 存档 `runs/mechanism/diagnostics/v2-v21-gap-closure-experiment.diff`（930 行）供复现。

### a.5 机制有效性对照证据（证明 consolidation 本身有效）

**受控夹具**（`benchmarks/experiments/etec_stress.py` + `fixtures/etec_stress_v1.json`，显式 fact_slot+fact_value+valid_from）：
- **SUPERSEDE 4/12 case**（stress_newer_supersedes_older / stress_stale_incoming_historical / stress_conflicting_evidence / stress_cross_session_consolidation）；MERGE 3；invariant_pass_rate 1.0。
- 产物：`runs/mechanism/etec_stress/etec-stress-20260817T093946Z/summary.json`（fixture_sha256 `3e2f022e…`，git `e521e31`）。
- **同一 ETEC 代码，显式 fact metadata 时 SUPERSEDE 可达** → 真实数据不可达的根因是提取管线 metadata 缺口，非 consolidation 逻辑 bug。**重要标注：夹具证明 consolidation 逻辑本身有效，不证明 ETEC 在真实数据有价值——恰恰相反，真实 0/8 SUPERSEDE 说明 ETEC 在真实数据无操作面。**

**v2.1 单元测试**（`test_supersede_reachable_after_v21`，已随回退移除，diff 存档）：用 fixture 风格两 memory（同 slot、不同 value、不同 event_time、valid_from=event_time）直接调 `_score_pair` → action=SUPERSEDE、contradiction≥0.7、rule `newer_source_supersedes_older_target`。证明 R1b 闭合后机制层可达。

**interval 排除代码路径**（既有单元测试，未改）：`tests/retrieval/test_qemr.py:767,799,831,1074,1433` 断言 `temporal_interval_excluded`；`test_query_router.py:290-377` 算子解析。interval 过滤代码在受控输入下行为正确。

### a.6 M3 joint recall + router 预筛（检索侧不是瓶颈）

**M3 joint recall（ms 8 KU，7 SUPERSEDE/MERGE gold pair + 1 ADD，4 方法，finalized retrieval.jsonl）**：
| 方法 | old_recall_mean | new_recall_mean | JERecall@8 |
|---|---|---|---|
| full | 1.0 | 1.0 | 1.0 |
| event_no_etec | 1.0 | 1.0 | 1.0 |
| etec | 1.0 | 1.0 | 1.0 |
| vector_rag | 1.0 | 1.0 | 1.0 |

old+new 侧（gold old_value_turn_ids + answer_session_ids）联合召回 100%。**但这是结构性 null，非 ETEC 优势**：SUPERSEDE=0 → 旧值全 ACTIVE → 旧值总能被检索 → full vs event_no_etec JERecall@8 delta=0.0。22d2cb42（ADD，old 侧空）：old_recall=NA、JERecall@8=NA。产物 `runs/mechanism/evala/m3_joint.json`（content_hash `sha256:1f16ef77…`）。

**M4 Eval B 探针（8 探针 × 4 臂，零 LLM）**：ExclusionHit=0 全臂（past 探针 BEFORE 算子正确触发，但 router "before {year}" 解析包含该年 → upper={year}-12-31 → 0 排除）；Contamination≈0.11-0.24（旧证据泄漏，SUPERSEDE=0 → 旧值可检索）；ValidRetention=1.0（gold 证据总保留）。full vs event_no_etec Contamination delta≈0.004（结构性 null）。产物 `runs/mechanism/evalb/m4.json`（content_hash `sha256:07ba78c3…`）。

**router 全量 500 题确定性预筛（零 LLM，预注册方法 `reference_time=question_date` 解析）**：`none 382 / earliest 42 / latest 23 / duration 26 / between 15 / sequence 11 / at 1`；**BEFORE/AFTER = 0**。interval 算子（触发 `temporal_interval_excluded` 的路径，retrieval.py:797 `_apply_interval_temporal`）在自然 500 题中共 **16 例**（15 BETWEEN + 1 AT，3.2%）——其中 15 BETWEEN 来自 `_RELATIVE_RE` 匹配 "last/next/this/past/previous/coming + week/month/year"（router.py:615-629），需 `reference_time` 才能解析（不传 reference_time 则 BETWEEN=0、latest=29，该口径与 spec §3.1 预注册设计期结果一致但不符合预注册方法；本报告以预注册方法 `reference_time=question_date` 为准）。→ 真实数据 interval 过滤**近零触发**（3.2%），与既有三个切片（72 题仅 1 earliest、0 between）方向一致。产物：`runs/mechanism/router_screen/router-screen.json`（`content_hash` 字段 sha256:74d18a1d…，500/500 question_date 解析成功）。

### a.7 (a) 结论

ETEC 的 SUPERSEDE（冲突更新）与 interval 排除（时序有效性）在真实 LongMemEval 数据上**结构上不可达**：
- SUPERSEDE：R1（提取不写 fact_slot）→ R1b（提取不写 valid_from，point-interval 不重叠）→ R3（LLM 过度标 multi_valued + event_time 粒度粗 + 缺 event_time）三层级联屏障；同代码在受控夹具（4/12）与 v2.1 单测上正常触发。
- interval 排除：自然数据 interval 算子近零（500 题 16 例 = 15 BETWEEN + 1 AT，3.2%），代码路径由既有单元测试覆盖。
- 检索侧无瓶颈：M3 新证据召回 1.0，溯源 100%。

**结论形态**：spec §1.2 形态 2（机理诊断）——触发率低的结构性根因 + 受控夹具对照 + 量化证据支撑。方向被实证否定时如实记录，未硬凑 SUPERSEDE 触发。

---

## (b) 大样本一致性

### b.1 离线一致性（4 finalized 24 样本 run + 1986-LoCoMo）

脚本 `benchmarks/mechanism/consistency.py`（入库，733 行，零 LLM）+ 测试 `tests/mechanism/test_consistency.py`（13 tests，43 passed/1 skipped）。产物 `runs/mechanism/consistency/consistency.json` + `.md`（content_hash `sha256:85e6b73af9522a632367a1dce8e28fcaad1626894afa33523f34d775291b512c`，确定性可复现）。

**续作修复**：`consistency.json` 的 `runs[]` 现有 **4 个 entry**（r2/6m/ms/recheck），`inputs.run_dirs` 有 4 个路径。下表 4 行由 `consistency.py --source-run <4 runs>` 结构化产出，**非多源手动编译**。"四 run Wilson 95% CI 两两重叠"可从 cited artifact 复算（6 对 pairwise overlap 全 True）。

| run | 溯源 raw_turn_id | 预算满装(记忆) | 0-格 | 失败归因(自动) | ETEC actions(A/M/S/R) |
|---|---|---|---|---|---|
| r2 | 4701/4701=100% [99.92,100] | 72/72=100% [94.93,100] | 4/0 | extr20/budget13 | 5429/5/0/0 |
| 6m | 6547/6547=100% [99.94,100] | 96/96=100% [96.15,100] | 7/33 | extr36/budget14/absent48 | NA(未持久化) |
| ms | 6266/6266=100% [99.94,100] | 96/96=100% [96.15,100] | 23/24 | extr55/budget18/absent48 | 5332/2/0/0 |
| recheck | 6656/6656=100% [99.94,100] | 96/96=100% [96.15,100] | 6/38 | extr27/budget19/absent48 | 5059/335/0/0 |

**稳定**：provenance 100%（**新管线修复了旧管线的 0；非始终 100%**——LoCoMo 1986 legacy provenance=0）；budget 饱和 1.0（记忆四方法每 run 全满装，CI 重叠——**非判别性指标：全方法 4096 budget 满装是 budget 设计的预期，不区分方法质量**）；**SUPERSEDE=0 全 run**（R1 在 4-run 规模复现）。

**有差异 + 已解释**：recheck MERGE 5→335 是确定性合并修复（`memory_order_key` tie-break）的预期信号，非指标漂移（METHODOLOGY_CHANGE.md §7）；6m 未持久化 `ingestion.etec.actions`（legacy 字段契约局限，标注 NA）。

**失败归因**：自动 taxonomy 四 run 结构同构（extr+budget 主导，baselines 加 absent=48）；但 r2 33/33 人工复核口径显示自动标签仅为假设（auto-vs-reviewer 一致率 7/33=21.2%，复核把 26/33 重判为 `answer_present_reader_wrong`）——自动分布是 reader-error 的稳定下界，真值以复核为准。

### b.2 1986-LoCoMo 大样本侧证（legacy，只读）

`runs/main/report/`（legacy 树，M14 run，未升级 finalized 管线）：
- token 效率：vector_rag 142.2 / etec 142.4 / full 200.3 / event_no_etec 200.4 / session_summary 2947.2 / full_context 4102.3 tokens/query（配对 Δ −3959.9，p<0.001，约省 96.5% **vs full_context trivial 基线**）；**注意：full（200.3）比 vector_rag（142.2）贵 41%（+58.1 tokens/query），full 的事件图开销使其比真 RAG 基线更贵** —— 1986 题大样本上效率一致性成立。
- 失败分布（`error_review.jsonl`，12890 行）：answer_not_recoverable 4423 / adversarial_no_gold 2664 / recoverable_wrong 2454 / no_memory 1940 / budget_truncation 1409 —— 与 r2 复核"reader 输出不精确为一大主因"方向一致。
- provenance=0（legacy 历史缺陷，M15 C09：0/668 事件带 verbatim turn span；不可从 legacy 重算，标注 `legacy_defect`）。

### b.3 500 run 状态 + 功效论证

**500 run 配额阻断**：网关 `opencode.ai/zen/go/v1` 在本周期返回 429（Too Many Requests，`runs/publication/main500-run.log` 第 20/58 行已验证）与 403（Cloudflare code 1010，手工探测，未在持久化日志留痕）；`configs/longmemeval/main500.toml` 已入库（run_id_prefix `m13-longmemeval-s500`，6 方法，4096 tokens），待配额恢复后台续跑（`--resume-dir`，spec §6.3 L0/L1）。本周期交付走 spec §6.3 末段"兜底（不消耗配额）"路径（离线重算 n=96+1986），非 §6.3 L3 的 250 题子集路径（`main500-fallback.selection.json` 未预冻结，因 L3 路径未触发）。

**功效论证（spec §6.3 末段"兜底（不消耗配额）"路径已交付）**：
> 9/10 (b) 目标要求 ≥500 样本 LongMemEval 一致性。预注册功效分析（`docs/METHODOLOGY_CHANGE.md` §1）显示 n=500、α=0.05 最小可检测效应 ±0.018–0.039，而观测配对效应仅 0.005–0.014 → 500 run 无显著性是预期内（不作决策信号，只作稳定性检查）。网关配额中断阻断 500 run 本周期。本离线报告交付 spec §6.3 末段"兜底（不消耗配额）"路径：5 项一致性判据在 n=96 LongMemEval（4 finalized run × 24）+ 1986-LoCoMo 上确定性重算。溯源覆盖率（100% raw_turn_id，Wilson CI 重叠于 1.0）与预算饱和（记忆方法 1.0，Wilson CI 重叠）是二项比例，非退化 Wilson 区间；n=96+1986 足以确认 100% 溯源与预算满装不漂移。0-格、失败归因分布、ETEC 动作计数以点估计报告并标注切片/行为差异（recheck MERGE 5→335 是确定性修复签名，非指标不稳定）。不做显著性宣称。500 run 待配额恢复后台续跑，届时对本 checklist 同口径重算。

### b.4 (b) 结论

(b) 在配额约束下以"离线一致性 + 功效论证 + 500 run 预注册续跑"交付：5 项机制指标在 n=96+1986 上不漂移（溯源/预算/SUPERSEDE 稳定；MERGE 差异有解释；归因分布结构同构，真值以 33/33 复核为准）。500 run 作为理想规模配额阻断待续，§6.3 末段兜底路径已满足"一致性验证"职责。如实标注未达成项（500 run 未跑、LoCoMo provenance=0 legacy 缺陷、6m ETEC actions NA）。

---

## (c) 机理解释报告：为什么端到端无增益

### c.1 机理链（四层，每层有出处或自有证据）

1. **ETEC 冲突更新在真实数据结构性不可达**（本任务 a.2-a.4）：R1+R1b+R3 三层级联屏障 → `full` 与 `event_no_etec` 检索侧无差异（SUPERSEDE 从未触发，旧值未被标记失效/排除）→ 端到端层面 ETEC 无计分面。**自有证据**：4 finalized run SUPERSEDE=0；夹具 4/12 证明机制本身有效。

2. **基准 judge 显式容忍陈旧信息**（外部文献）：LongMemEval 官方 judge prompt（论文附录 A.4）明确——"If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer"。→ 即便 ETEC 的 SUPERSEDE 可达并在检索侧排除旧值，基准分数也不会奖励（旧值与新值并存仍判 correct）。ETEC 的核心价值在端到端分数上**无计分面**。**出处**：LongMemEval（arXiv:2410.10813）附录 A.4。

3. **reader 主导错误，检索已无瓶颈**（外部 + 自有）：
   - LongMemEval 官方错误分析（App. E.5）："检索正确但生成错误"占全部样本 15–19%、占错误样本 40–50%；reader 越弱占比越高。
   - LoCoMo（表 3）：dialog RAG R@50≈84.8–91.9% 但答案 F1 仅 31–35%——检索命中率远高于答案得分。
   - 本仓 33/33 失败人工复核（`runs/review/longmemeval-r2.reviewed.jsonl`）：26/33 = `answer_present_reader_wrong`（答案在打包上下文 recall 1.0，reader 输出冗余措辞导致严格 EM 不命中）；真正检索/提取/预算失效仅 7/33。
   - 本仓 M3 new_recall=1.0（ms 8 KU 四方法）——检索总能找到新证据。
   - 结论：错误集中在 reader 生成阶段，检索侧已饱和，ETEC 即便生效也撬不动端到端分数。

4. **基准设计本身限制了对检索增益的辨识**（外部文献）：
   - LongMemEval 官方明确否定严格 EM（"exact matching strategy … can result in inaccurate evaluations"，改用 GPT-4o judge，与人工一致率 >97%）；本仓复现用严格 EM，比官方更严 → 放大"检索对但生成错"的失分。
   - LongMemEval-V2（arXiv:2605.12493，同组 work-in-progress）改为 context-gathering formulation，**主动解耦检索与 reader**（memory 只返回紧凑证据，固定 reader + 200k 截断作答）——作者承认 retrieval 增益被 reader 输出掩盖；其 pilot 显示即便给 oracle 证据片段，准确率也只到 82.5–86.3%（GPT-5.4-mini）——reader 侧存在固有损失上限。
   - BEAM（arXiv:2510.27246）明确弃用字符串匹配，改用 nugget + LLM judge（0/0.5/1），并专设 contradiction_resolution 能力（mem0 实测 0.357，全线最低）——学界共识：严格 EM 脆弱、冲突消解需独立评测。
   - 端到端分数由非记忆组件决定：Mem0 评测套件——仅换提取模型（embedder/judge 不变）LongMemEval 从 91.0%（GPT-5）→ 88.6%（Gemma-4-31B），移动 2.4 点；BEAM retrieval budget 实验 K=15 峰值、K=20 回落。
   - 知识更新类 reader 偏向内在记忆：Tug-of-War Between Knowledge（LREC-COLING 2024，arXiv:2402.14409）——越强 RALM 越呈"Dunning-Kruger"，即使给正确证据也偏向有错的内在记忆；反事实冲突下 vanilla RAG context compliance 仅 20–52%（TokenMem, arXiv:2607.22625）。

### c.2 方法学修正（对照臂口径）

`etec`=FIXED_VECTOR、`event_no_etec`=QEMR（run.py:153-158）——既有"etec vs event_no_etec"对照同时混入"ETEC 开/关"与"检索策略 QEMR/FIXED_VECTOR"两因素，不构成"只差 ETEC"的消融。**ETEC 隔离主对照 = `full` vs `event_no_etec`（同 QEMR，只差 ETEC 存储/整合）**；`full` vs `etec` 为检索策略隔离（同 ETEC 存储）。本口径已写入 spec §3.3、§13 决议 1，并应进 `docs/EVALUATION.md` 方法学节。

### c.3 可辩护的简历口径（只写真实测得的硬数字）

| 指标 | 数字 | 对照 | 来源 |
|---|---|---|---|
| 输入 token 节省 | 96.5%（142.2 vs 4102.3 tokens/query，p<0.001）**vs full_context（trivial 基线）**；**注意：full（200.3）比 vector_rag（142.2）贵 41%，full 的事件图开销使其比真 RAG 基线更贵** | full_context | `runs/main/report`（1986-LoCoMo） |
| 证据溯源覆盖 | 100% raw_turn_id（4 finalized run，n=96，Wilson CI 重叠 1.0，**可从 consistency.json 复算**）**— 新管线修复了旧管线的 0（LoCoMo legacy provenance=0），非始终 100%** | 历史缺陷 0/668（legacy LoCoMo） | `runs/mechanism/consistency/consistency.json`（sha256:85e6b73a…） |
| 0 分格修复 | 10 → 4 | 基线 | `runs/publication/longmemeval-test20-r2` + `SUMMARY_24SAMPLE.md` |
| 失败归因 | 26/33 reader-wrong（33/33 人工复核） | 自动标签一致率 7/33=21.2% | `runs/review/longmemeval-r2.reviewed.jsonl` |
| 新证据召回 | 1.0（ms 8 KU 四方法）**— joint recall（old+new 侧，7 对 gold pair）；结构性 null：SUPERSEDE=0 → 旧值 ACTIVE → 总可检索 → full vs event_no_etec delta=0.0，非 ETEC 优势** | — | `runs/mechanism/evala/m3_joint.json`（sha256:1f16ef77…） |
| SUPERSEDE 诊断 | 0/8 实测 + 0/24 结构外推（同管线 v1，mechanism40 未 finalize）= "0/32"（R1+R1b+R3 级联） vs 4/12 受控夹具（**证明 consolidation 逻辑，不证明 ETEC 真实价值**） | 同代码 | `runs/mechanism/etec_stress/...summary.json` + `runs/mechanism/evala/m1.json`（M1=1/8，22d2cb42 coincidental match flagged，sha256:2afcea42…） |
| interval 算子 | 500 题 16 例 interval（15 BETWEEN + 1 AT，3.2%），BEFORE/AFTER=0 | — | `runs/mechanism/router_screen/router-screen.json`（content_hash 字段 sha256:74d18a1d…） |
| M4 探针 | ExclusionHit=0 全臂（router "before {year}" 包含该年 → 0 排除，如实记录）；Contamination≈0.11；ValidRetention=1.0 | — | `runs/mechanism/evalb/m4.json`（sha256:07ba78c3…） |
| budget 饱和 | 1.0 全方法（**非判别性：全方法 4096 满装是设计预期，不区分方法质量**） | — | `runs/mechanism/consistency/consistency.json` |
| under_edit | 1.0（**SUPERSEDE=0 的直接推论，非独立测量：旧值从不被标记失效 → 全 ACTIVE**） | — | `runs/mechanism/evala/m5.json` |
| version_chain_recall | 0.0（**SUPERSEDE=0 的直接推论：无 supersede 则无 superseded_by 链**） | — | `runs/mechanism/evala/m5.json` |

**叙事**：本项目交付"机制证据链 + 可复现产物 + 根因诊断"，不参与绝对分数竞争。端到端无增益有明确机理解释（提取管线结构性缺口 + 基准 judge 容忍陈旧 + reader 主导错误 + 检索已饱和），而非方法失效；ETEC consolidation 机制本身经夹具与单测证明有效，真实数据不可达是提取侧 metadata 缺口。

---

## 产物清单

### 入库（untracked，待用户确认后 commit；本周期不擅自提交）
- `tasks/optional/O09_mechanism_evaluation.md`
- `docs/superpowers/specs/2026-08-17-o09-mechanism-eval-and-500-consistency-design.md`（含 §13 审批）
- `docs/9of10_ACCEPTANCE.md`（本文件）
- `benchmarks/mechanism/{__init__,gold,replay,eval_a,consistency,probes}.py`
- `tests/mechanism/{test_gold,test_replay,test_eval_a,test_consistency,test_probes}.py`
- `scripts/annotate_gold_pairs.py`
- `configs/longmemeval/{mechanism-evala.selection.json,mechanism-40.toml,main500.toml,mechanism-probes.json}`

### 不入库（gitignored，`runs/`，哈希进报告）
- `runs/mechanism/evala/metrics.partial.json`（sha256:0d3c5a2f…）
- `runs/mechanism/evala/m1.json`（M1=1/8，22d2cb42 coincidental match flagged，sha256:2afcea42…）
- `runs/mechanism/evala/m2.json`（NOT_PRODUCED_QUOTA_BLOCKED，无假数字）
- `runs/mechanism/evala/m3_joint.json`（joint recall，7 对 gold pair，sha256:1f16ef77…）
- `runs/mechanism/evala/m5.json`（under_edit=1.0, version_chain_recall=0.0）
- `runs/mechanism/evalb/m4.json`（FINAL：ExclusionHit/Contamination/ValidRetention per arm，sha256:07ba78c3…）
- `runs/mechanism/evalb/probes_retrieval.jsonl`（8 探针 × 4 臂 packed_items + exclusions 明细）
- `runs/mechanism/gold/longmemeval-kupairs-ms8.v1.json`（sha256:ab12f14c…，8 对含 ADD）
- `runs/mechanism/consistency/consistency.json` + `.md`（sha256:85e6b73a…，4-run 结构化）
- `runs/mechanism/etec_stress/etec-stress-20260817T093946Z/summary.json`
- `runs/mechanism/gold/review_sheet.jsonl`（32 行，gold 字段空白，ms 8 KU 已单独标注到 v1.json）
- `runs/mechanism/diagnostics/v2-v21-gap-closure-experiment.diff`（930 行，v2/v2.1 实验存档）

### 既有 finalized 产物（只读引用）
- `runs/publication/longmemeval-test20-{r2,6m,ms}/`、`runs/recheck/m13-longmemeval-test20-20260814T195333507448Z/`
- `runs/ablation/{controlled,longmemeval-test20}/`、`runs/analysis/sha256:*`
- `runs/main/report/`（LoCoMo legacy 1986）
- `runs/review/longmemeval-r2.reviewed.jsonl`（33/33 复核）

---

## 未竟事项与风险

1. **500 run 未跑**：网关配额中断（429/403）。`main500.toml` 已入库，待配额恢复后台 `--resume-dir` 续跑（spec §6.3 L0/L1）。届时对 78 KU + 全 500 重算 (b) checklist 同口径。
2. **M1/M2/M5 状态更新（续作后）**：
   - **M1 已产出**：`runs/mechanism/evala/m1.json`（sha256:2afcea42…），**M1=1/8**（22d2cb42 ADD coincidental match — ETEC did ADD due to R1 default, gold is also ADD; match flagged as coincidental, not a genuine correct SUPERSEDE decision）。全部 R1 根因。可复现：`python -m benchmarks.mechanism.eval_a --source-run <ms> --dataset <ds> --ms-selection <sel> --gold <gold> --m1-from-online <out> --out <metrics>`。
   - **M2 未产出（配额阻断）**：`runs/mechanism/evala/m2.json`（status=NOT_PRODUCED_QUOTA_BLOCKED），无假数字。SUPERSEDE=0 隐含 full vs event_no_etec stale_rate Δ≈0（结构性 null）。
   - **M5 已产出**：`runs/mechanism/evala/m5.json`，under_edit_rate=1.0（**SUPERSEDE=0 的直接推论，非独立测量**）、version_chain_recall=0.0（**同上，SUPERSEDE=0 的直接推论**）。over_edit 需 Eval B 探针，M4 Contamination≈0.11 部分量化（旧证据泄漏）。
   - **gold 已标注 ms 8 KU**：`runs/mechanism/gold/longmemeval-kupairs-ms8.v1.json`（sha256:ab12f14c…，**8 对含 ADD**）。新 24 KU 仍需 mechanism40 run finalize 后标注。
3. **gold 标注状态更新**：ms 8 KU 已标注（**8 对含 ADD**，见上 #2）。新 24 KU 仍需 mechanism40 run finalize 后标注。
4. **retrieval 离线 replay 与 online 不一致**：`benchmarks/mechanism/replay.py` 对 ms run 重放时 LinkCandidateGenerator embedding 路径 cache-miss + 候选池分歧（4dfccbf8 重放 ADD 210/MERGE 14 vs online ADD 223/MERGE 1）。M1 改用 online 持久化 `ingestion.etec.actions`（动作计数可信），per-decision 明细需 500 run 持久化字段补全。
5. **6m ETEC actions NA**：6m run 未持久化 `samples/<id>.json → ingestion.etec.actions`（字段契约局限）；r2/recheck 已覆盖同 24 题 pre/post-fix 对照。
6. **LoCoMo provenance=0**：legacy 管线历史缺陷，不可从 legacy 重算，标注 `legacy_defect`；不影响 LongMemEval finalized 侧的 100% 结论。
7. **judge 同源偏差风险**：M2（未跑）默认 deepseek-v4-flash 与 reader 同模型；本周期未产出，风险待 500 run 后补做时处理（spec §13 决议 4）。
8. **v2/v2.1 已回退**：生产保持 v1（与既有 finalized 一致）；v2/v2.1 实验作为诊断证据（diff 存档），未入库未运行生产。

---

## Phase 6 三轮独立验收结论

> **注**：以下三验收记录的是 8→9 续作**之前**的 Phase 6 验证（当时 consistency.json 只处理 1 run，hash 为 `5764711a…`）。续作后 consistency.json 已升级为 4-run 结构化产出（hash `85e6b73a…`，见 §b.1 + `docs/9of10_AUDIT.md` 闭环 1）。以下 hash 引用保留作历史记录，不作当前复算依据。

三轮验收子代理（互不共享上下文）独立运行，各自重跑验证命令并输出"通过/不通过 + 证据路径"。**第一轮 A/B/C：A 不通过（router 数字 + 产物缺失）、B 不通过（eval_a.py schema 不匹配 + 入库口径）、C 通过（含 minor）**。修复后**第二轮 A/B/C：A 通过、B 通过、C 不通过（router-screen hash 不可复现 + L3 术语未传播到 consistency 产物）**。再修复后**第三轮 C 再复审：通过**。三轮全过，验收完成。

### 验收 A（研究诚信审计）— 通过（第二轮复审）

逐条：§a.2 R1 数字（1821 events/0 fact_slot/0 SUPERSEDE，重算一致）✓；§a.5 夹具 4/12 SUPERSEDE（`summary.json` supersede_count=4）✓；§b.1 4-run 一致性（content_hash `sha256:5764711a…` 重算匹配）✓；§a.6 router 500 题预筛（预注册方法 `reference_time=question_date` 重算 `none 382/earliest 42/latest 23/duration 26/between 15/sequence 11/at 1`，与 `router-screen.json` 500/500 逐题一致）✓；无产物篡改（4 publication run + 5 analysis 报告 `load_finalized`/`load_analysis_finalization` 全绿，git diff src/benchmarks/tests 空）✓；v2/v2.1 回退干净（PROMPT_VERSION=v1，extraction.py 无 fact_slot/valid_from，diff 930 行存档）✓；LLM judge 合规（M2 未跑，未声称任何 LLM-judge 数字）✓；预注册一致（形态 2 机理诊断，R3 后停止追屏障未硬凑）✓；C0 门（pytest 45p/1s、ruff、mypy、smoke 全绿）✓。非阻塞观察：router-screen.json hash 约定现已统一（嵌入 `content_hash` 字段）。

### 验收 B（架构师）— 通过（第二轮复审）

逐条：架构边界（mechanism 无 FastAPI/数据库/OpenCode/Pi/vendor 依赖，git diff src 空）✓；对照臂口径修正（run.py:153-158 核实 `etec`=FIXED_VECTOR/`event_no_etec`=QEMR，ETEC 隔离主对照 `full` vs `event_no_etec` 方法学正确，消解既有混杂违规）✓；机制诊断逻辑链（R1 consolidation.py:876/943-946/399 + extraction.py:998-1003；R1b :880-885；R3 :411-417/:404-410/:876 分桶，代码引用准确；夹具 4/12）✓；v2/v2.1 回退正当（生产 v1，diff 存档可复现，避免"调 code 凑 SUPERSEDE"外观）✓；一致性脚本（只读 finalized、零 LLM、零 src 改动、recheck MERGE 5→335 是确定性修复签名）✓；boundary（测试 823 行，小纯函数 + ports，UTC-aware）✓；**eval_a.py 可复现性（严重发现已修复）**：新增 `compute_metrics_from_online` 读 online `ingestion.etec.actions`，CLI `--source-run` byte-for-byte 复现 `metrics.partial.json`（content_hash `sha256:0d3c5a2f…` 保留），测试 `test_compute_metrics_from_online_*` 覆盖 schema ✓；**产物清单口径（中等已修复）**：§产物清单改"入库（untracked，待用户确认后 commit；本周期不擅自提交）"，与 `git status` 一致 ✓。C0 门全绿。非阻塞观察：`--mechanism40-selection` 旗标需在复现命令中显式传入（已文档化）。

### 验收 C（基准方法论审计）— 通过（第三轮再复审）

逐条：公平性（4 finalized run manifest 同 dataset_hash/reader/prompt/budget=4096/seed 机制，mechanism-40.selection seed=42+sha256）✓；无方法混杂（对照臂清晰，L2/L3 降级声明式）✓；统计口径（Wilson 95% CI 数学亲自复算 7 行全一致，n=96+1986 足以确认 100% 不漂移，≤40 题未做显著性，recheck MERGE 5→335 仅点估计）✓；功效论证成立（500 MDE ±0.018–0.039 > 观测 0.005–0.014，L3 fallback 满足一致性职责）✓；LLM judge 合规（未产出，未混用）✓；预注册一致（E1 SUPERSEDE≈0/E2 MERGE>0/E6 interval 近零/E7 500 无显著性全成立，R3 后停止追屏障合规）✓；降级阶梯合规（500 run L0 阻断 → 末段兜底声明式，6m ETEC actions NA 如实）✓；traceability（8 个数字抽查全可溯）✓。**两项残留已修复**：(1) `runs/mechanism/router_screen/router-screen.json` 嵌入 `content_hash` 字段 `sha256:74d18a1d…`（canonical_json_hash 重算匹配，文档引用同步，无 `9698f30e` 拋留）；(2) L3 术语传播到 `consistency.py` 源码（"no-quota fallback"/"末段兜底"）+ 重生成 `consistency.md`（无 L3，与 9of10 §b.3 一致），content_hash 稳定 `sha256:5764711a…`。C0 门全绿。非阻塞观察：README:43 引用 router-screen 文件路径但未显式写 hash（hash 嵌入文件内，无残留错误 hash）。

### 三轮验收总判定：**全部通过**

修复轮次：第一轮发现 A/B 严重 + C minor → 修复 → 第二轮 A/B 通过、C 严重 → 修复 → 第三轮 C 通过。三轮全过，O09 任务验收完成。三份验收意见原文存于子代理 task 输出（本节为编排者据其整理的结论摘要，关键证据路径与重算命令均保留）。
