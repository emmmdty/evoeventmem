# 方法论变更复盘（2026-08-13）

> 变更：评测节奏从"先跑 500 样本再分析"改为"**小样本强结果先行，大样本一致性验证**"。
> 本文件记录变更理由、已落地证据与下一阶段约束。所有引用数字均来自 runs/ 产物或上文
> `docs/STRONG_RESULTS_SMALL_SAMPLE.md`，不在本文件重新发明数字。

## 1. 变更背景与动机

- 原计划：直接跑 500 样本 LongMemEval，然后做 M15 分析。
- 问题：功效分析表明（不再重跑）500 样本在 α=0.05 下的最小可检测效应为 **±0.018–0.039**，
  而观测到的配对效应仅 **0.005–0.014**。即：大样本跑完，方法间差异不显著是**预期内的统计事实**，
  用它做决策会得到"无结论"，且分析闭环要等到 500 样本全部跑完才启动，修复周期长。
- 变更后：先在 24 样本 r2 run（`runs/publication/longmemeval-test20-r2`，finalized）上把
  **机制级强结果**闭环：配对修复验证、机制指标、效率、失败归因；500 样本降级为一致性验证。

## 2. 已落地的小样本闭环（2026-08-13）

1. **M15 启动阻塞修复**：
   - `benchmarks/analysis/loaders.py` 的 `resolve_dataset_path` 支持"仓库根相对路径"回退
     （r2 manifest 的 `dataset_path` 是仓库相对路径 `data/raw/longmemeval/longmemeval_s_cleaned.json`）。
   - `tasks/mainline/M15_analysis.md` 验证命令同步到当前 C8 CLI
     （`--config/--source-run/--artifact-root`；不提供 `runs/main` 兼容入口，理由见该任务文件）。
   - 顺带修复单数据集配置下 `headline_claim` 的 IndexError（此前只支持双数据集）。
2. **第一份 content-addressed M15 报告**：`runs/analysis/sha256:6260181f…/`（report.md/report.json/tables/plots/review sheet，
   FINALIZED 封存 8 个输出文件；`validate_report` 校验 valid=true）。analysis config 用收窄版
   `configs/analysis/r2-pilot.toml`（仅 `full vs vector_rag`，因为 r2 方法集只有 3 个方法，
   完整版 main.toml 引用的 `event_no_etec` 缺失 → 如实记录为"需完整 6 方法 run"，未篡改 config）。
3. **强结果页**：`docs/STRONG_RESULTS_SMALL_SAMPLE.md`（5 项清单，全部数字可溯源到 artifacts）。
4. **未完成项（如实记录）**：6 方法完整 run（补齐 no_memory/full_context/event_no_etec）后才能生成
   完整版 M15 报告与 ETEC 消融（evidence/temporal/graph/router/weights/budget 因子）；当前 r2 只有 3 方法，
   ablation 因子为 0，category 只有 information-extraction（r2 未记录 native ability 类别），这两点明确限制
   报告粒度，不伪装成已完成。

## 3. 大样本（500）在本阶段的角色：一致性验证

- 500 样本不再承担"显著性发现"角色：功效分析已证明其最小可检测效应 > 观测效应，无显著性为预期结果。
- 它的职责是验证小样本机制结论的**稳定性**：
  1. 证据溯源覆盖率（r2 三方法 100% raw_turn_id）在大样本不回落；
  2. 0 分格修复与失败归因分布（extraction_provenance_rejection / budget_truncation 为主）在大样本同构；
  3. 预算满装与 tokens/query 不变形；
  4. 数值上报告点估计与 CI，不做显著性宣称。
- 运行约束（已核对的配置，勿改动）：
  - `EEM_LLM_BASE_URL=https://opencode.ai/zen/go/v1`（.env 已配）；
  - embedding 服务 `127.0.0.1:11436`；
  - **勿删 `runs/`**（不可变产物与快照），`runs/` 不入库（.gitignore 已覆盖）。

## 4. 竞品情报与定位（2026-08 联网）

- Mem0：LoCoMo 92.5 / LongMemEval 94.4（其 single-session-user 98.6）。
- LongMemEval-V2 已发布（agentic 场景；O03 候选任务）。
- BEAM 基准新出现，待后续评估是否引入。
- 定位不变：不参与绝对分数竞争；以"机制证据链 + 可复现产物"作为交付物。

## 5. 风险与未决

- r2 切片全部为 single-session-user：ETEC 合并/冲突更新机制在该切片无触发机会
  （consolidation 24/24 keep）。
- 完整 6 方法 run 前，`etec vs event_no_etec`、`full vs event_no_etec` 等关键消融无法出报告。
- 失败归因的自动标签仍是假设（review 0/33），需要人工复核至少代表性样本后才能引用为结论。

## 6. 小样本闭环进展（2026-08-14）

前文第 2 节的三项未决在 2026-08-14 全部落地，具体数字见
`docs/STRONG_RESULTS_SMALL_SAMPLE.md` 与 content-addressed 报告：

1. **完整 6 方法 run**（`runs/publication/longmemeval-test20-6m`，finalized，git clean）：
   补齐 no_memory/full_context/vector_rag/event_no_etec/etec/full 六方法，
   提取快照与 r2 逐事件一致（仅 UUID/时间戳不同）；完整 M15 报告
   `runs/analysis/sha256:a0907e94…`（validate valid=true）。
   - 注意：`full` 方法在 6m run 与 r2 之间的 12/24 预测存在差异，根因是 retrieval 候选
     cap/链接索引按随机 `memory_id` UUID 打破平局（`retrieval._cap_candidates` 与
     `linking._build_request_indexes`）导致的 run-to-run 非确定性，已在报告中如实记录；
     报告内所有配对比较均在同一 run 内，不受此影响。
2. **消融六因子**：controlled fixture（Gate D 六因子全部 active）+ longmemeval-test20
   家族（`runs/ablation/`，离线检索重放，无 reader 调用，全部 finalize）。
   六因子在 publication 切片均产生决策变化（delta 22–24/24）。报告同时如实渲染了
   analysis 侧对 budget/evidence 等 arm 的 factor_leak 诊断（受限于 payload 字段契约，
   controlled fixture 同样出现，属既有分析端行为，不改变结论）。
3. **multi-session 切片**（`runs/publication/longmemeval-test20-ms`，finalized，git clean；
   24 题 = 8 knowledge-update + 8 multi-session + 8 temporal-reasoning，seed 42 确定性选取，
   清单见 `configs/longmemeval/test20-ms.selection.json`）：
   - **ETEC MERGE 首次触发**：2 个样本（`4dfccbf8`、`f0853d11`）各 1 次 merge，
     `f0853d11` 的合并记忆（"Coastal Cleanup event on March 7th"）携带 4 条 raw-turn 证据；
     该切片仍未出现 SUPERSEDE（无冲突更新事实）。
   - temporal 过滤：router 在该切片仅产生 1 个 earliest 约束、23 个无约束，
     `temporal_interval_excluded` 排除未实际触发——如实记录为"该切片未触发"，
     不作为 temporal 过滤机制无效的证据。
   - 切片报告 `runs/analysis/sha256:ba0ce41e…`（ms-pilot.toml，validate valid=true）；
     机制结论：合并机制在 multi-session 数据上有真实触发机会，EM 层面该切片
     `full`/`etec` 与 `vector_rag` 无观测差异（descriptive，n=24）。
4. **失败归因人工复核**：33/33 复核完成（`runs/review/longmemeval-r2.reviewed.jsonl`），
   自动/人工一致率 21.2%；复核后主因为 answer_present_reader_wrong 26、
   extraction_provenance_rejection 6、budget_truncation 1，详见强结果页第 4 节。
5. **未决（如实记录）**：two-dataset headline 因 locomo 缺失仍 blocked；
   SUPERSEDE/冲突更新机制与 temporal interval 排除仍未在任何切片触发；
   **6m run 的 run-to-run 非确定性（UUID 平局）已修复**（2026-08-14，
   `memory_order_key` 使 tie-break 基于内容+证据而非随机 memory_id，
   覆盖 `retrieval._cap_candidates` 与 `linking._build_request_indexes`，
   新增回归测试 `test_retrieval_decisions_are_run_id_independent` 与
   `test_event_index_order_is_run_id_independent`；**6m 重跑因 embedding/LLM
   服务不可达而阻塞**——修复后的复跑验证仍是待办，修复前跨 run 数字不可逐位比对）。
   另注：`a0907e94`（6m）报告生成所用的 analysis config（`sha256:f796e6fd…`）
   与仓库内 `configs/analysis/main.toml`（`ef98168f…`）不一致——该 config 未入库，
   报告已封存不可变，重生成需还原该 config 变体。
