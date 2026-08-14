# 小样本强结果报告页（24-sample r2）

> 方法论定位：小样本（24 题）的价值是**机制级强结果**（修复验证、机制指标、效率、失败归因），不是显著性声明。
> 本文所有数字均来自 `runs/publication/longmemeval-test20-r2/`（finalized，config_hash `sha256:533d4a57…`，git `996346b` 干净）、
> 基线记录 `runs/publication/longmemeval-test20/SUMMARY_24SAMPLE.md`、content-addressed M15 报告
> `runs/analysis/sha256:6260181f…/report.md` 与本地 `runs/main/report/`（LoCoMo 主 run）。禁止手写数字。

## 0. 样本与公平性约束

- 24 题 × 3 方法（`vector_rag` / `etec` / `full`），全部 `single-session-user` 类别。
- r2 与基线 test20 共享同一提取快照（仅 retrieval 改变），同一 reader `deepseek-v4-flash`（opencode.ai/zen/go/v1），同一 config_hash。
- 三方法均打到 4096 token 预算上限（packing_bound 24/24 题），预算内可比。
- M15 报告配对比较结论（不是显著性声明）：`full` vs `vector_rag` EM Δ −0.0417，95% CI [−0.2083, +0.1250]，raw p=0.4836（n=24），**descriptive，不显著**。小样本不做方法胜负判断。

## 1. 配对修复验证（10 → 4 归零修复）

来源：基线侧 `runs/publication/longmemeval-test20/SUMMARY_24SAMPLE.md`；r2 侧由 `r2/samples.jsonl`（FINALIZED 校验通过）重算。

| 指标（per-method） | 基线 old → r2 new |
|---|---|
| vector_rag token_f1 | 0.786 → 0.772 |
| etec token_f1 | 0.724 → 0.767 |
| full token_f1 | 0.678 → 0.758 |
| etec / full exact_match | 0.458 → 0.500 / 0.542 |
| evidence recall（三方法） | → 1.000（r2 重算确认 evR=1.0000） |

- token_f1 = 0 的（题目×方法）格：**10 → 4**（r2 重算确认恰好 4 格：`6b168ec8/vector_rag`、`6ade9755/etec`、`c960da58/etec`、`c960da58/full`）。
- 修复归因（基线 SUMMARY 记录）：ETEC merge gate（`af8d2e46`，不再把 "packed 7 shirts" 合并进 "wore 3 shirts"）；budget-fill packing（`5d3d2817`、`51a45a95`，排名靠后但正确的证据被装入）。
- 诚实记录的新回归：`6b168ec8/vector_rag` 0.667 → 0.000（见第 4 节归因）。

## 2. 机制指标（记忆中间层）

来源：`r2/*/retrieval.jsonl`、`r2/evidence.jsonl`、`r2/extraction_snapshot.json`、`r2/consolidation.jsonl`（重算）与 M15 报告。

- 证据溯源覆盖：packed evidence refs 携带 `raw_turn_id` 的比例三方法均 **100%**（933/933、1918/1918、1850/1850）；`evidence.jsonl` 4701 行全部 exact=True。对照 LoCoMo 主 run（runs/main/report §6）历史缺陷 23434 refs 中 0 个带 `raw_turn_id` —— 确定性 span 定位修复在机制层闭环。
- M15 报告 evidence_f1：vector_rag 0.1334 / etec 0.0781 / full 0.0812；evidence precision 0.0735/0.0406/0.0424 —— recall 100% 后 precision 是 budget-fill 多装入的代价（基线 SUMMARY 同样注明）。
- 提取快照：24 个 snapshot，172–292 事件/样本（均值 226.4），累计 82 条 rejection。
- 合并活动：24/24 样本 consolidation action = keep（该切片全是 single-session-user，ETEC merge/supersede 无触发机会 —— 不作为 ETEC 无效的证据，也不作提升声明）。

## 3. 效率

- r2：三方法均满 4096 token 预算（mean 4074–4082），prompt overhead 仅 8 token/查询；等预算可比性成立。
- LoCoMo 主 run（runs/main/report，1986 题）：vector_rag 142.2 tokens/query vs full_context 4102.3（配对 Δ −3959.9，95% CI [−3961.0, −3958.8]，p<0.001）—— 记忆方法在相近效果下节省约 96.5% 输入 token，机制级效率证据。

## 4. 失败归因

来源：M15 报告 taxonomy + `review_longmemeval.jsonl`（33 条失败全部入样）与人工复核产物
`runs/review/longmemeval-r2.reviewed.jsonl`（33/33 复核完成，2026-08-14）。

### 4.1 自动 vs 人工标签（33 条全复核）

- 自动标签（假设）：extraction_provenance_rejection 20、budget_truncation 13。
- 人工复核后：**answer_present_reader_wrong 26、extraction_provenance_rejection 6、budget_truncation 1**。
- **自动/人工一致率 7/33（21.2%）**。主要系统性偏差：`extraction_provenance_rejection` 自动标签只看
  "存在 extraction rejection 记录"，但其中 14/20 条的答案其实已在打包上下文中（answer_recall=1.0），
  真正失败原因是 reader 输出带多余词导致严格 EM 不命中（如 gold "Target" vs pred "At Target, using the
  Cartwheel app."）；`budget_truncation` 13 条中 12 条同样在 recall=1.0 下由 reader 冗余输出失分。

### 4.2 复核后归因分布与主因

| 复核标签 | 条数 | 证据要点 |
|---|---|---|
| answer_present_reader_wrong | 26 | 答案在打包上下文（recall 1.0 或 ≥0.5），reader 输出冗余/不精确（多数仅多一个修饰词） |
| extraction_provenance_rejection | 6 | `c960da58`(etec/full)、`3b6f954b`(etec/full)、`58ef2f1c`(etec/full)：答案事件未入快照/被 invalid_span 拒绝，recall 0.0–0.8 |
| budget_truncation | 1 | `5d3d2817/vector_rag`：答案 turn 未装入（recall 0.6），reader 从其他 turn 作答 |

- 结论：r2 剩余失败主因是 **reader 精确输出**（严格 EM 下的冗余措辞），而非检索/预算失效；
  真正由 extraction 拒绝或预算截断造成答案缺失的只有 7/33。
- 可修复项（不阻塞，记录待办）：reader 指令加"仅输出事实短语、不添加修饰"（对应
  `51a45a95`/`66f24dbb`/`af8d2e46`/`726462e0` 等 ~20 条）；`6b168ec8/vector_rag` 为数字-单词
  归一化（pred "3" vs gold "three"，答案在上下文 recall 1.0，**不是**证据缺失）。

### 4.3 4 个 0 分格最终归因（人工复核）

- `c960da58/etec` 与 `c960da58/full`：**extraction_provenance_rejection（确认）**。快照中无
  "20 playlists" 事件（5 条 invalid_span 拒绝，含 answer turn），answer_recall 0.0，答案事实未入库。
- `6ade9755/etec`：**answer_present_reader_wrong**。answer_recall 1.0（"Serenity Yoga" 在上下文中），
  reader 误读为 "At home using the Down Dog app."（原自动标签 budget_truncation 不成立）。
- `6b168ec8/vector_rag`：**answer_present_reader_wrong**。answer_recall 1.0（"I've got three of them"
  已在上下文中），reader 输出 "3" 未命中 gold "three"；此前文档记作"抽取 rejection 导致证据缺失"
  有误——vector_rag 不使用抽取快照，本格是 reader 输出归一化问题。

### 4.4 仍存疑样本清单

无（33/33 全部复核，无未决）。复核判据与每条证据见 `runs/review/longmemeval-r2.reviewed.jsonl`；
sealed 报告 `runs/analysis/sha256:6260181f…` 未被修改（复核产物写在新位置）。

- LoCoMo 主 run（runs/main/report/error_review.jsonl，12890 行）：answer_not_recoverable 4423、
  adversarial_no_gold 2664、recoverable_wrong 2454、no_memory 1940、context_budget_truncation 1409
  —— 与 r2 复核后"reader 输出不精确"为一大主因的方向一致（LoCoMo 侧为历史诊断，不参与本页数字）。

## 5. 大样本一致性验证的定位与运行约束

- 功效分析（2026-08-13，详见 docs/METHODOLOGY_CHANGE.md）：500 样本最小可检测效应 ±0.018–0.039，大于观测效应 0.005–0.014 → **大样本无显著性是预期内结果**，不构成方法失效。
- 500 样本在本阶段只承担**一致性验证**：确认小样本机制结果（溯源覆盖率、0 分修复、预算满装、失败归因分布）在大样本下不漂移。
- 运行约束：`EEM_LLM_BASE_URL=https://opencode.ai/zen/go/v1`（.env 已配）、embedding `127.0.0.1:11436`、勿删 `runs/`、`runs/` 不入库。

## 6. 完整 6 方法 run 与完整 M15 报告（2026-08-14）

来源：`runs/publication/longmemeval-test20-6m/`（finalized，git `9656301` clean，config_hash
`sha256:6f959d5e…`，dataset_hash 与 r2 相同，24 样本与 r2 一致，提取快照逐事件一致），
消融 `runs/ablation/controlled` + `runs/ablation/longmemeval-test20`，完整报告
`runs/analysis/sha256:a0907e94…/report.md`（validate valid=true，8 输出文件）。

- 方法总览（24 题，EM / token_f1 / tokens-per-query）：no_memory 0.0000/0.0111/9.6、
  full_context 0.0000/0.0195/4095.0、vector_rag **0.5833**/0.7720/4074.0、
  event_no_etec 0.4167/0.7489/4084.6、etec 0.5000/0.7670/4081.4、full 0.4167/0.7600/4082.8。
- 配对 bootstrap（n=24，descriptive 为主，不做显著性声明）：
  - `full vs vector_rag`（EM）Δ −0.1667，95% CI [−0.3333, −0.0417]，raw p=0.0002（Holm p=0.0004）；
  - `full vs event_no_etec`（EM）Δ +0.0000，CI [0,0]（两方法在 single-session 切片 EM 逐题一致）。
- 消融六因子（离线检索重放，无 reader 调用，全部 finalize）：evidence/temporal/graph/weights/
  budget 决策变化 24/24 题，routing 22/24 题；controlled fixture 六因子全部 active（Gate D 通过）。
  报告如实渲染了 analysis 端对 budget/evidence 等 arm 的 factor_leak 诊断（既有分析端行为，
  controlled fixture 同样出现；记录为已知字段契约局限，不改变消融结论）。
- **已知限制（如实记录）**：`full` 方法在 6m run 与 r2 的预测 12/24 不同（vector_rag 2/24），
  根因是候选 cap / 链接索引按随机 `memory_id` UUID 打破平局（`retrieval._cap_candidates`、
  `linking._build_request_indexes`）的 run-to-run 非确定性。**该根因已于 2026-08-14 修复**
  （tie-break 改用内容+证据派生的 `memory_order_key`，新增 run-id 无关回归测试）；
  6m 重跑复验因外部服务不可达仍待办，修复前跨 run 数字不可逐位比对。报告内所有
  比较均为同 run 配对，不受影响。

## 7. multi-session 切片（ETEC 合并机制验证，2026-08-14）

来源：`runs/publication/longmemeval-test20-ms/`（finalized，git `3ac8979` clean），24 题 =
8 knowledge-update + 8 multi-session-reasoning + 8 temporal-reasoning，seed 42 确定性选取
（清单与选择方法见 `configs/longmemeval/test20-ms.selection.json`），切片报告
`runs/analysis/sha256:ba0ce41e…/report.md`（validate valid=true）。

- **ETEC MERGE 首次触发**：`4dfccbf8`、`f0853d11`（均为 temporal-reasoning）各 1 次 merge。
  `f0853d11` 合并记忆 "User volunteered at the Coastal Cleanup event on March 7th." 携带
  4 条 raw-turn 证据（离线重放确认，embedding 全部 cache hit）。consolidation.jsonl 只记录
  每样本主 action（keep），merge 细节存于 samples/ingestion 的 actions 计数——记录为报告粒度局限。
- **未触发（如实记录，不作为机制无效证据）**：SUPERSEDE 0 次（切片无冲突更新事实）；
  `temporal_interval_excluded` 0 次（router 在本切片仅产生 1 个 earliest 约束、23 个无约束，
  interval 过滤路径未被调用）；`historical` packed 0 项。
- 检索差异：`full` vs `event_no_etec` 打包内容仅 `4dfccbf8` 1 题不同（恰为 merge 样本），
  但 EM 逐题相同（Δ 0，CI [0,0]）；`etec`（FIXED_VECTOR）与 QEMR 类方法打包全部不同属策略差异。
- 方法总览（EM / token_f1）：vector_rag 0.2500/0.5083、event_no_etec 0.2500/0.5075、
  etec 0.2083/0.4626、full 0.2500/0.4988、no_memory 0.0000、full_context 0.0000。
  配对 bootstrap（descriptive）：full vs vector_rag Δ 0.0000（CI [−0.1667, +0.1667]）、
  full vs event_no_etec Δ 0.0000（CI [0,0]）——小样本不做显著性宣称。

## 竞品情报（2026-08 联网，仅定位说明，不参与绝对分数竞争）

- Mem0：LoCoMo 92.5 / LongMemEval 94.4（single-session-user 98.6）。
- LongMemEval-V2 已发布（agentic 场景，O03 候选）；BEAM 基准新出现。
- 本项目的叙事是机制级证据链（修复闭环 + 溯源 + 效率 + 归因），而非绝对分数追赶。
