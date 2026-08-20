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

O09 机制诊断增量（2026-08-18，详见 [`docs/8of10_ACCEPTANCE.md`](8of10_ACCEPTANCE.md) §a/§b）：
- **SUPERSEDE 诊断**：真实数据 0/8 实测 + 0/24 结构外推（同管线 v1，mechanism40 未 finalize）= "0/32"（**非全实测，headline 需标注外推**），根因 = R1 提取不写 fact_slot → R1b 不写 valid_from（point-interval 不重叠）→ R3 LLM 过度标 multi_valued + event_time 粒度粗 + 缺 event_time 三层级联屏障；同代码在受控夹具 `runs/mechanism/etec_stress/…/summary.json`（fixture_sha256 `3e2f022e…`）上 4/12 SUPERSEDE、invariant_pass_rate 1.0 → **证明 consolidation 逻辑有效，不证明 ETEC 在真实数据有价值**（真实 0/8 说明 ETEC 无操作面）；v2.1 单测证明 R1b 闭合后机制层可达。续作后：**M1=1/8**（`runs/mechanism/evala/m1.json`，sha256:2afcea42…，22d2cb42 ADD coincidental match flagged — ETEC did ADD due to R1 default, not a genuine correct decision），M5 under_edit=1.0（**SUPERSEDE=0 的直接推论，非独立测量**）/version_chain_recall=0.0（**同上**），M2 未产出（配额阻断，`runs/mechanism/evala/m2.json`）。
- **interval 算子预筛**：router 全量 500 题确定性预筛（零 LLM，预注册方法 `reference_time=question_date` 解析）`none 382 / earliest 42 / latest 23 / duration 26 / between 15 / sequence 11 / at 1`、BEFORE/AFTER = 0（`runs/mechanism/router_screen/router-screen.json`，content_hash 字段 sha256:74d18a1d…）→ 真实数据 interval 过滤近零触发（16/500=3.2%），与既有三个切片一致。**M4 探针（8 探针 × 4 臂，零 LLM）**：ExclusionHit=0 全臂（router "before {year}" 解析包含该年 → upper={year}-12-31 → 0 排除，如实记录非凑数字）；Contamination≈0.11-0.24（旧证据泄漏，SUPERSEDE=0 → 旧值可检索）；ValidRetention=1.0（`runs/mechanism/evalb/m4.json`，sha256:07ba78c3…）。
- **M3 joint recall**：ms 8 KU 四方法 old_recall_mean = 1.0、new_recall_mean = 1.0、JERecall@8 = 1.0（full / event_no_etec / etec / vector_rag）→ 检索侧无瓶颈（**joint recall：old+new 侧，7 对 gold pair；结构性 null——SUPERSEDE=0 → 旧值 ACTIVE → 总可检索 → full vs event_no_etec delta=0.0，非 ETEC 优势**；`runs/mechanism/evala/m3_joint.json`，sha256:1f16ef77…）。
- **大样本一致性**：4 个 finalized 24 样本 run（n=96）provenance 100%（Wilson 95% CI 两两重叠于 1.0，**可从 consistency.json 复算**）+ 记忆方法预算饱和 1.0（**非判别性指标：全方法 4096 满装是 budget 设计预期，不区分方法质量**）+ SUPERSEDE=0 全 run（`runs/mechanism/consistency/consistency.json`，sha256:85e6b73a…，**4-run 结构化产出非多源手动编译**）；失败归因分布结构同构（extr+budget 主导，baselines 加 absent=48），r2 33/33 复核口径下 26/33 = reader_wrong（auto-vs-reviewer 一致率 7/33=21.2%）；1986-LoCoMo 失败分布（answer_not_recoverable 4423 / adversarial_no_gold 2664 / recoverable_wrong 2454 / no_memory 1940 / budget_truncation 1409）方向一致。
- **500 run 状态**：网关 429/403 配额阻断，`configs/longmemeval/main500.toml` 已入库待续；预注册 §6.3 末段兜底路径 + 功效论证已交付（n=500 最小可检测效应 ±0.018–0.039 > 观测 0.005–0.014，500 run 无显著性是预期内，作稳定性检查不作决策信号）。

未达成的门槛（如实记录，不伪装）：
- 无端到端 QA 增益声明：24 样本 `full` vs `vector_rag` 无正向显著差异（6m 报告 Δ −0.1667 为负，且受 run-to-run UUID 平局非确定性影响，见 `docs/STRONG_RESULTS_SMALL_SAMPLE.md` §6）；
- `etec` vs `event_no_etec` 在 single-session 切片 EM 逐题一致（Δ 0，CI [0,0]），且该对照混入检索策略因素（§7）；
- SUPERSEDE 与 temporal interval 排除在真实数据上结构性不可达（机制诊断完成，见 `docs/8of10_ACCEPTANCE.md` §a）；
- stale-memory error 机制诊断完成（SUPERSEDE 结构性不可达，R1+R1b+R3 级联屏障）；M2 stale judge 因网关配额阻断未跑，由诊断隐含无 with/without-ETEC 差异（SUPERSEDE 不触发则旧值不会被标记失效），见 `docs/8of10_ACCEPTANCE.md` §b 风险 2。

结论口径：本项目的交付物是"机制证据链 + 可复现产物"，不是绝对分数竞争（竞品见 `docs/COMPETITIVE_ANALYSIS.md`）。

## test50-mimo (n=50, mimo-v2.5, 2026-08-18)

> 本节为 S0 整改（诚信止血）补披露——`test50-mimo` 是项目最大的 finalized LongMemEval run（50 题，FINALIZED 在 `runs/publication/m13-longmemeval-test50-mimo/`，git `e585d7e` 干净），与 8of10 验收文档同一天生成，但在所有叙事文档中缺席。

完整指标表（数字源自 `runs/publication/m13-longmemeval-test50-mimo/summary.json`）：

| method         | EM    | token_f1 | evidence_recall | tokens/query | p50 search ms | p50 write ms |
|----------------|-------|----------|------------------|--------------|---------------|--------------|
| no_memory      | 0.00  | 0.0050   | 0.0000           | 10.56        | 0.0           | -            |
| full_context   | 0.00  | 0.0107   | 0.0000           | 4094.86      | 3.4           | -            |
| vector_rag     | 0.56  | 0.8105   | 1.0000           | 4072.50      | 437,556.8     | 45.1         |
| event_no_etec  | 0.54  | 0.7264   | 0.9800           | 4082.66      | 2,386.8       | 36.2         |
| etec           | 0.52  | 0.7060   | 0.9800           | 4083.00      | 2,340.3       | 130,185.2    |
| full (flagship)| 0.46  | 0.6869   | 0.9800           | 4080.92      | 2,339.1       | 130,185.2    |

**诚实解读**：

- `full` (ETEC+QEMR flagship) 是所有记忆方法里最差（EM=0.46），比 `vector_rag` (0.56) 低 10 个点；
- 拆掉 ETEC（`full` → `event_no_etec`）反而 +8 EM（0.46→0.54），说明 ETEC 在真实数据上有害；
- 拆掉 QEMR（`full` → `etec`）反而 +6 EM（0.46→0.52），说明 QEMR 在真实数据上有害；
- ETEC write p50=130,185ms（约 130s）是 consolidation 开销；
- `vector_rag` p50 search=437,556ms（约 7.3 分钟）是 SSH tunnel + 串行 embedding 的病态延迟（整改 spec S4b 修复，必须先于 S2 重跑）；
- **跨模型对比禁止**：50 题 run 用 mimo-v2.5 reader；既有 24 题 finalized run 用 deepseek-v4-flash（已停服、不可复现、**禁止跨模型对比**——AGENTS.md 禁止不等模型下 benchmark 对比）；
- 整改方案见 `docs/REMEDIATION_SPEC.md`（S1a/S1b 修 ETEC 第一道闸门 → S2 重跑诊断 → S3 QEMR 失效根因）。

### 6m run ETEC NA 声明（S0 / spec B4 Gap 3）

**注**：6m run（`runs/publication/longmemeval-test20-6m/`）的 `ingestion.etec.actions` 字段为 NA（legacy 字段契约，未持久化 samples dir 的 `ingestion.etec.actions` 路径；deepseek-v4-flash 已停服，run 不可复现）。整改 spec `docs/REMEDIATION_SPEC.md` S0 步骤 5（B4 / Gap 3）要求显式声明，避免读者误读为"已测量但缺失"。

## 6.5 模型 pinning + 可复现性（S4a）

S4a 把"私有网关 + SSH tunnel + 未追踪 .env"的可复现性风险清零。所有 reader/extractor 模型在 TOML 配置里以 `model_id` 显式 pin；`.env.example` 列出全部字段名（值留空 + 注释说明用途），`.env` 不被 git 追踪（`git ls-files .env` 输出空）。

**模型 pinning**：

- 50 题 v1 run（`test50-mimo`）+ 50 题 v2 run（`test50-mimo-v2-factslot`）+ S3 权重消融（`test50-mimo-v2-ablation`）均用 `mimo-v2.5` 作 reader/extractor（`configs/longmemeval/test50-mimo.toml`，`model_id = "mimo-v2.5"` pinned）。v1 vs v2 EM 可对比（同模型同 4096 预算）。
- S3 M2 stale-judge 用 `minimax-m3`（`ARK_*` env），**显式 ≠** reader `mimo-v2.5`（AGENTS.md "LLM judges require cached inputs/outputs and a documented judge model"；spec N8/B4）。judge 输入输出缓存到 `<source-run>/m2_judge_cache/`（31 个内容寻址缓存文件）。
- Embedding：50 题 v1/v2 run + 消融均用 `qwen3-embedding-0.6b`（本地 GPU tunnel，`http://127.0.0.1:11436/v1`）。
- 24 题 finalized run（`longmemeval-test20-6m` / `longmemeval-test20-ms` 等）用 `deepseek-v4-flash`（已停服、不可复跑、**禁止与 mimo-v2.5 跨模型对比**——AGENTS.md 禁止不等模型下 benchmark 对比，见上文 §test50-mimo "跨模型对比禁止" 与 §test50-mimo-v2-factslot "Same-model comparison"）。

**离线复现（无网络调用）**：

```bash
uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml
```

`configs/longmemeval/offline10.toml` 用 `provider = "deterministic_fake"`：`benchmarks/common/providers.py` 的 `build_model_bundle` 在该 provider 下构造内存态 chat + embedding 模型，benchmark 运行期间**零网络调用**。`deterministic_fake` 是占位模型（预测为固定字符串，EM=0.0 by design），目标是**管线可达性 + 无网络复现**，不是准确率基准。配置使用已追踪的 `tests/fixtures/longmemeval/oracle_tiny.json`（n=1，fresh clone 无需下载数据集即可跑通；`sample_limit = 10` 为上限，因 fixture 仅含 1 题故实际处理 1 题，<1 秒完成）。若要在真实 LongMemEval-S 数据上做 10 题离线跑，把 `dataset_path` 改为 `data/raw/longmemeval/longmemeval_s_cleaned.json`（先一次性 `python scripts/data/download_longmemeval.py --variant s` 拉取；benchmark 运行本身仍零网络调用，但真实 LongMemEval haystack 较大，10 题约需 20 分钟）。

**生产复现（需凭据）**：

50 题 v2 run 的复现需要：(1) `OPENAI_API_KEY`（opencode.ai 网关 / `mimo-v2.5` reader+extractor）；(2) 本地 embedding tunnel（`qwen3-embedding-0.6b` on GPU，`http://127.0.0.1:11436/v1`，`EMBEDDING_API_KEY` 用任意非空值）。配置见 `configs/longmemeval/test50-mimo.toml`。S3 M2 judge 的复现额外需要 `ARK_API_KEY`（`minimax-m3`）。`.env.example` 列出全部字段名 + 用途注释，不留真实凭据值。

## 7. 方法学：对照臂口径修正（O09）

来源：`docs/8of10_ACCEPTANCE.md` §a.1/§c.2、`benchmarks/longmemeval/run.py:153-158`（8of10 报告原文记作 `run.py:153-158`）、spec §3.3/§13 决议 1。

**问题**：既有 `etec` 与 `event_no_etec` 对照臂在 `benchmarks/longmemeval/run.py:153-158` 上配置为：
- `etec` → 检索策略 `FIXED_VECTOR`、ETEC 存储/整合 off（即纯向量基线，没有事件图/QEMR 路径）；
- `event_no_etec` → 检索策略 `QEMR`、ETEC 存储/整合 off（即 QEMR 检索但不做冲突更新/时序整合）。

因此既有 "etec vs event_no_etec" 对照同时混入两因素：(1) ETEC 开/关、(2) 检索策略 QEMR/FIXED_VECTOR，**不构成"只差 ETEC"的纯净消融**。在此口径下观测到的 Δ 0（CI [0,0]）不能解读为"ETEC 无收益"，因为两臂在检索策略上也不等价。

**修正**：ETEC 隔离主对照改为 `full` vs `event_no_etec`：
- `full` → 检索策略 `QEMR`、ETEC 存储/整合 **on**；
- `event_no_etec` → 检索策略 `QEMR`、ETEC 存储/整合 **off**；
- 两者仅差 ETEC 存储/整合，**同 QEMR 检索** → 满足"只差 ETEC"的消融口径。

`full` vs `etec` 则是检索策略隔离（同 ETEC 存储）：`full` 用 QEMR、`etec` 用 FIXED_VECTOR。

**理由**：(1) 控制变量法要求消融臂只差被消融因子，避免方法混杂（AGENTS.md 代码评审规则："Reject changes that mix benchmark methods under unequal model, context-budget, or retrieval-budget settings"）；(2) 在 ETEC 真实不可达的诊断结论下（`docs/8of10_ACCEPTANCE.md` §a.7），`full` vs `event_no_etec` 是 ETEC 隔离的唯一可用纯净对照，既有 multi-session 切片该对照 EM 逐题一致（Δ 0）与机制诊断（SUPERSEDE 不触发则 ETEC 在检索侧无操作面）相互佐证；(3) `etec` vs `event_no_etec` 对照不丢弃，但定位为"检索策略 + ETEC 两因素联合对照"，不作 ETEC 单因素结论。

## test50-mimo-v2-factslot (n=50, mimo-v2.5, v3 prompt, 2026-08-19)

**Status**: S2 v2-factslot 50-question run complete and finalized. Results below are empirical measurements; no pre-declared expectation.

**Same-model comparison**: v1 and v2 both use mimo-v2.5 as reader and extractor. v1 vs v2 EM comparison is permitted. Cross-model comparison against the 24-question deepseek-v4-flash run is forbidden (N8).

**S4b vector_rag latency fix applied**: the S4b fix (commit 46b7b38) moves the corpus-embedding cost from `search_latency_ms` (per-query) to `vector_index_ms` (per-sample write). v2 `search_latency_ms` is therefore near-zero (2,333 ms p50); v2 `vector_index_ms` is ~68,623 ms p50 (the embedding cost moved to write time). **v1 vs v2 search latency is NOT directly comparable** (v1 lazily embedded at search time; v2 pre-warms at write time). **v1 vs v2 EM IS directly comparable** (latency does not affect EM).

### v1 vs v2 EM comparison (same model: mimo-v2.5)

| method         | v1 EM | v2 EM | Δ     |
|----------------|-------|-------|------|
| no_memory      | 0.00  | 0.00  | +0.00 |
| full_context   | 0.00  | 0.00  | +0.00 |
| vector_rag     | 0.56  | 0.56  | +0.00 |
| event_no_etec  | 0.54  | 0.48  | -0.06 |
| etec           | 0.52  | 0.46  | -0.06 |
| full (flagship)| 0.46  | 0.48  | +0.02 |

_No pre-declared expectation. Same model (mimo-v2.5). Cross-model comparison forbidden (N8)._

**Read**: `full` improved +0.02 EM (0.46 → 0.48) — a slight improvement but not a翻盘. `event_no_etec` and `etec` both dropped 0.06. The `full` vs `event_no_etec` gap closed from -0.08 (v1) to 0.00 (v2) — ETEC went from harmful to neutral. But the absolute `full` EM (0.48) is still below `vector_rag` (0.56), so the flagship is still not the best method.

### ETEC actions distribution

- v1 baseline (test50-mimo): SUPERSEDE = 0 (ETEC structurally unreachable on v1's v2-prompt extraction output).
- **v2 S2 measurement: SUPERSEDE = 109 across 40/50 samples** (first time SUPERSEDE fires on real data).

| action    | v2 count |
|-----------|----------|
| ADD       | 7,188    |
| MERGE     | 1,770    |
| REJECT    | 352      |
| SUPERSEDE | 109      |

**Routing**: SUPERSEDE > 0 → S3 (QEMR diagnosis + M2 stale-judge). The 109 SUPERSEDE count is a necessary condition for the positive thesis, NOT sufficient — S3 still needs to verify QEMR uses the superseded memories correctly and the reader benefits.

### fact_slot / valid_from / sentinel rates (50 questions, v3 prompt)

| metric                          | v2 S2 (n=50) | S1c baseline (n=5) | spec floor / ceiling |
|---------------------------------|--------------|--------------------|----------------------|
| fact_slot effective rate (excl. sentinel) | 66.8% (6295/9419) | 60.3% (625/1036) | ≥ 50% ✅ |
| valid_from non-empty rate       | 66.8% (6294/9419) | 60.3% (625/1036) | ≥ 50% ✅ |
| valid_until non-empty rate      | 0.7% (63/9419) | 1.6% (17/1036)    | no spec floor (informational) |
| sentinel rate ("none")          | 33.2% (3124/9419) | 39.7% (411/1036) | < 20% ⚠️ (xfail) |

**Routing**: sentinel rate 33.2% ≥ 20% ceiling → **do NOT re-tune the prompt in S2** (AGENTS.md anti-fishing rule). The 33.2% is a documented weakness; S3/S5 will decide whether to redesign the contrast-pair example or pivot the SUPERSEDE basis away from `fact_slot`.

The sentinel rate dropped from S1c's 5-question 39.7% to S2's 50-question 33.2% (-6.5pp), but still well above the 20% ceiling. The 5-question slice over-estimated the rate; the 50-question measurement is more stable but still fails the prompt-health threshold.

### Reachability test

- S1c 5-question baseline: 107 four-gate pairs satisfy all four SUPERSEDE gates.
- **S2 50-question v2: reachability test PASSES** (not XFAIL) — at least one within-sample pair on the v2 snapshot satisfies `not multi_valued` AND `_same_fact_slot` AND `not _same_fact_value` AND `_intervals_overlap`.

### Per-sample breakdown

Run `uv run python -m benchmarks.mechanism.s2_diagnostics` for the full per-sample table (50 rows × events / real / sentinel / valid_from / effective_rate / sentinel_rate / valid_from_rate / valid_until_rate).

### Routing after S2

- ✅ **SUPERSEDE > 0 (109)** → S3 (QEMR diagnosis + M2 stale-judge; positive thesis path).
- ⚠️ **sentinel rate ≥ 20% (33.2%)** → do NOT re-tune prompt in S2; route to S3/S5 (AGENTS.md anti-fishing rule). Documented as known weakness.
- ✅ **fact_slot effective rate ≥ 50% (66.8%)** → S1c v3 prompt effective on 50 questions.
- ⚠️ **`full` EM not翻盘 (+0.02 only)** → S3 must verify QEMR uses the 109 SUPERSEDE memories correctly. The gap closed (`full` vs `event_no_etec` from -0.08 to 0.00) but absolute EM still below `vector_rag`.
- v1 vs v2 latency NOT directly comparable (S4b moved embedding cost from search to write). v1 vs v2 EM IS comparable.
