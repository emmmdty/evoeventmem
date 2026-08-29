# Stage 4a + 5 执行提示词：可复现性 + 定稿（分支 C 中间路线）

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `8b28a5e`）刚完成 Stage 3：QEMR 失效根因诊断 + M2 stale-judge + 独立审查 **CONDITIONAL PASS**（`docs/STAGE3_REVIEW.md`，15/15 验收标准通过）。

S3 的根因结论（`docs/QEMR_FAILURE_DIAGNOSIS.md` §5）触发了 S5 分支路由：

- ✅ **SUPERSEDE > 0 (109)** → S5 不走分支 A（negative-result）
- ❌ **`full` EM 没翻盘 (+0.02 only，0.48 vs vector_rag 0.56)** → S5 不走分支 B（positive thesis）
- ✅ **M2 跑完（74% tie, 0% full-stale）** → S5 分支 C 的必要证据已就位
- → **S5 走分支 C（中间路线）**："ETEC 的 SUPERSEDE 在真实数据上可达但不足以提升整体准确率——证据约束的 operating surface 在 LongMemEval 的 single-session-user 类上太窄。"

S3 的两个根因诊断（`docs/QEMR_FAILURE_DIAGNOSIS.md` §5）：

1. **Router 误路由（primary, 可修复）**：全 500 题准确率 38% < 80% N9 阈值；50 题切片 4%（全为 `single-session-user`，40/50 被路由到 HYBRID，8/50 到 TEMPORAL，仅 2/50 到 SEMANTIC）。`_FACT_RE` 不匹配 LongMemEval 的 "what + noun + did + subject + verb" 句式。SEMANTIC==HYBRID 权重相同（权重中性），但 16% SEMANTIC→TEMPORAL 误路由把 dense 权重从 1.0 降到 0.3。
2. **Operating-surface 窄（structural, 不可在 S5 修复）**：50 题全为 `single-session-user`（单会话事实查询）。SUPERSEDE 在 74% 的差异预测样本上是 reader-level no-op（`full` 和 `event_no_etec` 给同一答案）。无时间显著性答案让 consolidation 改变 reader 可见值。

整改 spec v1.1（`docs/REMEDIATION_SPEC.md` Stage 4a + Stage 5，lines 348-496）明确两阶段：
- **Stage 4a**（1 天，无代码）：可复现性 config + docs——把"私有网关 + SSH tunnel + 未追踪 .env"的可复现性风险清零。
- **Stage 5**（2 天，分支 C）：定稿——写 `docs/REMEDIATION_FINAL_REPORT.md` + 更新 README / INTERVIEW_KIT / RESUME_NARRATIVE。

**为什么合并成一份提示词**：S4a 是纯文档 + config（1 天，无代码依赖），S5 是最终交付（2 天）。两阶段天然顺序（S4a 先可复现性，S5 后定稿），且 S5 的最终报告需要引用 S4a 的可复现性配置。合并执行避免两次独立审查，但**每个阶段单独 commit**（commit message 模板见下）。

**scope 边界（明确声明，不藏着）**：

- S4a **不改 src/ 代码**——只加 `.env.example` 字段、`configs/longmemeval/offline10.toml`、文档 note。
- S5 **不跑新 benchmark**——只写文档，引用 S2/S3 已生成的 `runs/` 产物。所有数字必须可溯源到 `runs/.../summary.json` 或 `docs/QEMR_FAILURE_DIAGNOSIS.md`。
- S5 **不跑 post-S3 router 规则修复**——S3 §5 把 router 修复列为"post-S3 独立小任务（需独立审查批准）"；S5 把它写进 future-work，不交付修复。**N9 scope 延续**。
- S5 **不跑 embedding 换型**——S3 §3 已声明"跳过，留 S5 决定"；S5 根据根因结论决定是否补跑，但**不在本阶段执行**（写进 future-work）。
- S5 **不声称 thesis 翻盘 / ETEC 有效 / QEMR 有效**——分支 C 是中间路线：SUPERSEDE 可达但不足以提升准确率。**禁止 overclaim**（AGENTS.md 代码评审规则 + `METHODOLOGY_CHANGE.md` 预注册框架）。
- S5 **不跨模型对比 EM**——v1 vs v2 都用 mimo-v2.5（同模型同预算 4096，可对比）；24 题 deepseek-v4-flash run 已停服，**禁止与 mimo-v2.5 跨模型对比**（N8）。
- S5 **不动 v3 prompt**——sentinel 率 33.2% 是 known weakness，写进 limitations。
- S5 **不修 R3**（`multi_valued` 过打）——不在 scope。

### 已完成的前置工作

- S0 完成（commit `b60b38d`，诚信止血）。
- S1a 完成（commit `162183c`，schema + prompt v2 落地）。
- S1b 完成（commit `00b3dc6`，5 题 smoke + reachability test）。
- S1c 完成（commit `ab5ba1a`，required fact_slot + retry + salvage + v3 prompt + contrast pair）。
- S4b 完成（commit `46b7b38`，vector_rag 延迟修复：`CachedEmbeddingModel` 批量化 + `OpenAICompatibleEmbeddingClient` 渐进收缩 + `run.py` 写时 pre-warm）。
- S2 完成（commit `17b1014`，50 题 v2 run + 诊断 + 独立审查 CONDITIONAL PASS）。
- S3 完成（commits `a428e8d`/`c215ff8`/`87009a7`/`8b28a5e`，QEMR 失效诊断 + M2 + 独立审查 CONDITIONAL PASS）。
- v2 run 已 finalized 在 `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`（50 题，9419 events，66.8% fact_slot 有效率，SUPERSEDE=109）。
- v1 baseline run 在 `runs/publication/m13-longmemeval-test50-mimo/`（finalize 于 `e585d7e`，同模型 mimo-v2.5，同预算 4096）。
- S3 产物（gitignored，在 `runs/`）：
  - `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/router_diagnosis_report.md`（Step 1 router 混淆矩阵）
  - `runs/publication/m13-longmemeval-test50-mimo-v2-ablation/`（Step 2 权重消融：`ablation_summary.json` + `ablation_<arm>.json` × 3 + `ablation_report.md` + `model_cache/`）
  - `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/m2_judge_report.md` + `.json` + `m2_judge_cache/`（31 缓存 judge 调用）
- S3 诊断报告：`docs/QEMR_FAILURE_DIAGNOSIS.md`（§1 router §2 weights §3 embedding skip §4 M2 §5 根因 + S5 路由分支 C）。

### 关键数据（S5 报告必须引用，可溯源到 runs/）

**v1 vs v2 EM 对比表（同模型 mimo-v2.5，同预算 4096，可对比）：**

| method | v1 EM | v2 EM | Δ |
|---|---|---|---|
| no_memory | 0.00 | 0.00 | 0.00 |
| full_context | 0.00 | 0.00 | 0.00 |
| vector_rag | 0.56 | 0.56 | 0.00 |
| event_no_etec | 0.54 | 0.48 | -0.06 |
| etec | 0.52 | 0.46 | -0.06 |
| full | 0.46 | 0.48 | +0.02 |

**关键 nuance**：v3 prompt（required fact_slot）让 `full` +0.02（ETEC 方法略升），
但 `event_no_etec` / `etec` 各 -0.06（非 ETEC 方法降）。ETEC 中性化（`full` vs
`event_no_etec` gap 从 v1 的 -0.08 收敛到 v2 的 0.00）是**通过非 ETEC 方法变差实现的**，
不是通过 ETEC 方法变好实现的。S5 报告必须诚实记录这个 nuance。

**ETEC actions（v2）**：`ADD=7188, MERGE=1770, REJECT=352, SUPERSEDE=109`（across 40/50 samples）。

**ETEC gap nuance**：`full` vs `event_no_etec` gap 从 v1 的 -0.08（ETEC 有害，full 0.46 < event_no_etec 0.54）收敛到 v2 的 0.00（ETEC 中性，full 0.48 = event_no_etec 0.48）。但收敛是通过 `event_no_etec` -0.06 实现的，不是 `full` 提升。S5 报告必须诚实记录。

**fact_slot / sentinel（v2）**：有效 fact_slot = 66.8% (6295/9419) ✅ ≥ 50% floor；valid_from = 66.8% ✅；sentinel = 33.2% (3124/9419) ⚠️ ≥ 20% ceiling（known weakness）。

**S3 router 诊断**：全 500 题准确率 38%（190/500）< 80% N9 阈值；50 题切片 4%（2/50，全为 single-session-user）。

**S3 权重消融**（同模型同预算，只差 retrieval weight）：

| arm | strategy | EM | Δ vs qemr |
|---|---|---|---|
| v2 full (baseline) | qemr | 0.48 | — |
| no_temporal | qemr_no_temporal | 0.46 | -0.02 |
| no_graph | qemr_no_graph | 0.48 | 0.00 |
| uniform | qemr_uniform | 0.42 | -0.06 |

**S3 M2 stale-judge**（minimax-m3 ≠ mimo-v2.5，31 差异预测样本）：tie 74.2%（23/31）、event_no_etec less-stale 19.4%（6/31，correctness 混淆）、full less-stale 3.2%（1/31）、parse-error 3.2%（1/31）。

**可达性**：v2 snapshot 四重 gate 可达性测试 PASS（非 XFAIL）。

**replay/online 一致性**：109 SUPERSEDE 在 replay 和 online 完全一致；2/50 样本 minor ADD↔MERGE 重分类（known limitation）。

### 关键约束（违反即 spec 失败）

- **S4a 不改 src/ 代码**——只加 config + `.env.example` 字段 + 文档 note。
- **S5 不跑新 benchmark**——只写文档；所有数字可溯源到 `runs/` 产物。
- **S5 不声称翻盘 / ETEC 有效 / QEMR 有效**——分支 C 是"可达但不足以提升"。**禁止** "显著提升"/"significant improvement"/"outperform" 等 overclaim（AGENTS.md 代码评审规则）。任何强 claim 必须有 p-value + CI 支撑（本阶段无新实验，不产生新 p-value）。
- **不跨模型对比 EM**——v1 vs v2 都用 mimo-v2.5（可对比）；24 题 deepseek-v4-flash run 已停服，禁止与 mimo-v2.5 对比（N8）。
- **不跑 500 题**——S5 在 50 题数据上写定稿；500 题是 future-work（分支 C 不要求补跑，分支 B 才要求）。
- **不修 router.py 规则**——S3 §5 把 router 修复列为 post-S3 独立小任务；S5 写进 future-work，不交付修复。**N9 scope 延续**。
- **不动 prompt**——v3 prompt 已落地；sentinel 率 33.2% 写进 limitations。
- **可以 git commit**——每个步骤单独 commit（commit message 模板见下）。
- **不破坏既有契约**：`uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q` + `uv run ruff check .` + `uv run mypy src` + `uv run python -m evoeventmem.cli smoke` 必须全绿。S4a 加 config + .env.example 字段时不能破坏 production 路径。
- **不 commit secrets**——`.env` 不追踪（`git ls-files .env` 必须空）；`.env.example` 只留字段名 + 注释，不留值。
- **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；UTC-aware datetime；小纯函数 + ports；不 commit datasets / secrets / model weights / benchmark caches。

## 执行步骤

### Part A: Stage 4a — 可复现性 config + docs（1 天，无代码）

#### Step A0: 前置检查

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -3   # 确认 HEAD 是 8b28a5e (S3 审查 commit) 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）

# 确认 .env.example 现状
cat .env.example
# 期望：缺 EEM_EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EEM_EMBEDDING_MODEL / EEM_EMBEDDING_DIMENSION

# 确认 offline10.toml 不存在（需新建）
test -f configs/longmemeval/offline10.toml && echo "EXISTS" || echo "MISSING (need to create)"

# 确认 deterministic_fake 分支可用
grep -n "deterministic_fake" benchmarks/longmemeval/run.py | head -5

# 确认 6m run 不存在或已声明 NA
ls runs/publication/ | grep -i "6m\|test20-6m\|test20-ms" 2>/dev/null || echo "no 6m run dir"

# 确认 .env 不追踪
git ls-files .env | wc -l   # 期望 0
```

预期发现：
- HEAD = `8b28a5e` 或后继；工作区 clean。
- `.env.example` 缺 embedding 相关字段。
- `configs/longmemeval/offline10.toml` 不存在。
- `deterministic_fake` 分支在 `run.py` 中可用（`_artifact_class` 或 provider 解析）。
- `.env` 不追踪。

#### Step A1: `.env.example` 补全

```bash
# 编辑 .env.example，加：
# - EEM_EMBEDDING_BASE_URL（embedding 服务地址，注释：生产用本地 GPU tunnel，离线用 deterministic_fake 不需要）
# - EMBEDDING_API_KEY（embedding 服务 key，注释：本地服务用任意非空值）
# - EEM_EMBEDDING_MODEL（模型 id，如 qwen3-embedding-0.6b）
# - EEM_EMBEDDING_DIMENSION（向量维度，如 1024）
# - EEM_LLM_MODEL / EEM_LLM_API_KEY_ENV（reader/extractor 模型 + key env 名）
# - ARK_API_KEY / ARK_BASE_URL / ARK_MODEL（judge 模型 minimax-m3，注释：S3 M2 用，judge ≠ reader）
# 每个字段：值留空 + 注释说明用途 + "生产/离线"两种配置说明
# 不留真实 secret 值
```

**验收**：`grep -E "EEM_EMBEDDING_BASE_URL|EMBEDDING_API_KEY|ARK_API_KEY" .env.example` 命中。

**commit**：`feat(s4a): complete .env.example with embedding + judge model fields`

#### Step A2: `configs/longmemeval/offline10.toml` + 离线复现

```bash
# 新建 configs/longmemeval/offline10.toml：
# - provider = "deterministic_fake"
# - sample_limit = 10
# - methods = ["no_memory", "full_context", "vector_rag", "event_no_etec", "etec", "full"]
# - [reader] / [extractor] / [embedding] 用 deterministic_fake provider（不需要 base_url / api_key_env）
# - max_input_tokens = 4096（与 mimo run 同预算）
# 跑通：uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml
# 期望：10 题跑完，产 summary.json（无网络调用）
```

**验收**：`configs/longmemeval/offline10.toml` 存在且 `uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml` 跑通（无网络）。

**commit**：`feat(s4a): offline deterministic_fake config for no-network reproducibility`

#### Step A3: 模型 pinning 文档 + 6m NA 声明

```bash
# 在 docs/EVALUATION.md 加 note（找合适位置，如 §config 或 §reproducibility）：
# "50 题 run 用 mimo-v2.5（configs/longmemeval/test50-mimo.toml，model_id pinned）。
#  24 题 finalized runs 用 deepseek-v4-flash（已停服，无法复跑，禁止与 mimo-v2.5 跨模型对比——AGENTS.md N8）。
#  离线复现：uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml（deterministic_fake，无网络）。
#  生产复现：需要 OPENAI_API_KEY (mimo-v2.5) + 本地 embedding tunnel (qwen3-embedding-0.6b on GPU)。"
#
# 6m run ETEC NA 声明（B4 / Gap 3 修复）：
# "6m run (test20-6m/test20-ms) 的 ingestion.etec.actions 为 NA（legacy field contract，未持久化 samples dir；
#  deepseek-v4-flash 已停服，run 不可复现）。"
```

**验收**：`grep -n "mimo-v2.5\|deepseek-v4-flash\|offline10\|6m run" docs/EVALUATION.md` 命中且表述诚实。

**commit**：`docs(s4a): model pinning + 6m ETEC NA note in EVALUATION.md`

#### Step A4: S4a 回归

```bash
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
git ls-files .env | wc -l   # 0
```

要求全绿 + `.env` 不追踪。

### Part B: Stage 5 — 定稿分支 C（2 天）

#### Step B0: 分支确认 + 文献补充

```bash
# 确认 S3 分支路由：读 docs/QEMR_FAILURE_DIAGNOSIS.md §5 的 "S5 routing" 段
grep -A5 "S5 routing" docs/QEMR_FAILURE_DIAGNOSIS.md
# 期望：明确写 "S5 branch C (intermediate route)"

# 确认分支 C 的 spec 描述
grep -A3 "分支 C" docs/REMEDIATION_SPEC.md
# 期望："SUPERSEDE > 0 但 full 仍输（中间路线）...M2 必须跑（已跑）"

# 可选：webfetch 文献补充
# - LongMemEval (arXiv:2410.10813) §4 question categories（single-session-user 定义）
# - MemTrace (arXiv:2606.17328) "evidence 10x retrievable than missing"（provenance 贡献 framing）
# - Mem0 (arXiv:2504.19413) graph memory +2%（结构化收益有限，与 S2 +0.02 一致）
```

**关键判断**：S3 §5 明确路由分支 C。若 `docs/QEMR_FAILURE_DIAGNOSIS.md` §5 未写明分支 C，**回到 S3 修复**（不在本阶段修）。

#### Step B1: 写 `docs/REMEDIATION_FINAL_REPORT.md`

```bash
# 报告结构（分支 C）：
# 1. Executive summary（1 段）：
#    - thesis 定位：ETEC 的 SUPERSEDE 在真实数据上可达（109 fires）但不足以提升整体准确率（full EM 0.48 vs vector_rag 0.56）。
#    - 证据约束的 operating surface 在 LongMemEval single-session-user 类上太窄（M2: 74% tie）。
# 2. v1 vs v2 EM 对比表（同模型 mimo-v2.5，同预算 4096）：
#    - 引用上面"关键数据"段的表
#    - 注明：v1 vs v2 同模型同预算可对比；24 题 deepseek run 不对比（N8）
# 3. ETEC 可达性诊断：
#    - SUPERSEDE=109 across 40/50 samples（第一次在真实数据触发）
#    - fact_slot 有效率 66.8%，sentinel 33.2%（known weakness）
#    - 四重 gate 可达性 PASS（非 XFAIL）
#    - replay/online 一致性：109 SUPERSEDE 一致；2/50 minor 重分类（known limitation）
# 4. QEMR 根因诊断（引用 docs/QEMR_FAILURE_DIAGNOSIS.md §1-§4）：
#    - §1 Router：38% 准确率（primary，可修复，留 future-work）
#    - §2 Weights：qemr ≥ 所有消融（weights 不是根因）
#    - §3 Embedding：跳过，留 S5 决定（写进 future-work）
#    - §4 M2：74% tie，0% full-stale（retrieval 未忽略 SUPERSEDE；surface 窄）
# 5. 最终 thesis 定位（分支 C）：
#    - "ETEC 的 SUPERSEDE 在真实数据上可达但不足以提升整体准确率——
#       证据约束的 operating surface 在 LongMemEval single-session-user 类上太窄。"
#    - 正面贡献：(1) 100% provenance coverage 基础设施；(2) 33/33 failure attribution；
#       (3) 不可篡改 FINALIZED.json；(4) 机制级诊断（router/weights/M2 三层定位根因）。
# 6. Limitations：
#    - 50 题全为 single-session-user（operating surface 窄，不可外推到 temporal-reasoning）
#    - sentinel 率 33.2%（extraction prompt 已知弱点）
#    - embedding 未对照（S3 §3 跳过）
#    - router 准确率 38%（未在本阶段修复，N9 scope）
#    - M2 judge 在非时间显著问题上混淆 correctness 与 staleness
# 7. Future work：
#    - router 规则修复（_FACT_RE + knowledge-update regex）——最高杠杆
#    - embedding 换型（bge-large-en-v1.5 / e5-large-v2）——若 router 修复不够
#    - M2 在 temporal-reasoning / knowledge-update 子集上重跑
#    - 500 题一致性验证（METHODOLOGY_CHANGE.md 角色：稳定性验证，不承担显著性）
#    - sentinel 率优化（prompt 调优，独立任务）
```

**验收**：`test -f docs/REMEDIATION_FINAL_REPORT.md` + `grep -c "test50-mimo-v2" docs/REMEDIATION_FINAL_REPORT.md` ≥ 1 + 含 v1 vs v2 对比表 + 含分支 C thesis + 含 limitations + 含 future work。

**commit**：`docs(s5): REMEDIATION_FINAL_REPORT — branch C intermediate-route thesis`

#### Step B2: 更新 `README.md`

```bash
# 读现有 README.md，找到 v1 标题/数据引用
# 替换为 v2 数据：
# - 标题：保留框架描述，但把"validated end-to-end"改成"evaluated end-to-end with intermediate-route finding on LongMemEval"
# - 关键数字：用 v2 的 full EM 0.48 / vector_rag 0.56 / SUPERSEDE 109
# - 不声称翻盘；诚实表述"ETEC SUPERSEDE reachable but insufficient on single-session-user slice"
# - 加链接到 docs/REMEDIATION_FINAL_REPORT.md + docs/QEMR_FAILURE_DIAGNOSIS.md
```

**验收**：`grep -c "0.48\|SUPERSEDE\|intermediate" README.md` ≥ 1；无 v1-only overclaim 残留。

**commit**：`docs(s5): README updated to v2 branch-C framing`

#### Step B3: 更新 `docs/INTERVIEW_KIT.md`

```bash
# 读现有 INTERVIEW_KIT.md
# 把"validated end-to-end"改成符合分支 C 的诚实表述：
# - "We built ETEC (evidence-constrained temporal consolidation) + QEMR (query-adaptive hybrid retrieval).
#    On LongMemEval 50-question single-session-user slice, ETEC's SUPERSEDE is reachable (109 fires across 40/50 samples)
#    but does not lift full EM above vector_rag (0.48 vs 0.56). Diagnosis: router mis-routes 84% of factual lookups
#    (fixable, future work) + operating surface too narrow for consolidation to change reader-visible answers (74% tie)."
# - 准备 2-3 个面试官可能的追问 + 诚实回答（不藏着 null 结果）
```

**验收**：`grep -c "intermediate\|reachable\|0.48\|SUPERSEDE" docs/INTERVIEW_KIT.md` ≥ 1；无"validated end-to-end"残留（除非上下文限定）。

**commit**：`docs(s5): INTERVIEW_KIT honest branch-C framing`

#### Step B4: 更新 `docs/RESUME_NARRATIVE.md`

```bash
# 读现有 RESUME_NARRATIVE.md
# 30 秒电梯陈述改成分支 C：
# - "Built a framework-independent temporal event-memory service (EvoEventMem) with two research contributions:
#    ETEC (evidence-constrained consolidation) and QEMR (query-adaptive retrieval). Evaluated on LongMemEval:
#    ETEC's SUPERSEDE is reachable on real data (109 fires) but insufficient to lift accuracy on single-session-user
#    queries — diagnosed to router mis-routing (38% accuracy, fixable) + narrow operating surface (74% reader-level tie).
#    Infrastructure contributions: 100% provenance coverage, 33/33 failure attribution, reproducible FINALIZED runs."
```

**验收**：`grep -c "reachable\|insufficient\|109\|0.48" docs/RESUME_NARRATIVE.md` ≥ 1。

**commit**：`docs(s5): RESUME_NARRATIVE 30-sec pitch — branch C`

#### Step B5: S5 回归 + 一致性扫描

```bash
# 全套回归
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke

# 一致性扫描
# 1. src/ 代码未动（S4a + S5 都是文档/config）
git diff 8b28a5e..HEAD -- src/ | head -5
# 期望：空（S4a 的 offline10.toml 是 config 不是 src；S5 是纯文档）

# 2. 无新增 overclaim
rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效|QEMR 有效' docs/REMEDIATION_FINAL_REPORT.md docs/INTERVIEW_KIT.md docs/RESUME_NARRATIVE.md README.md 2>&1
# 期望：无新增 overclaim（REMEDIATION_FINAL_REPORT.md 自身的 disclaimer "does not claim" 允许）

# 3. 数字一致性：抽样 3 条核对
uv run python -c "
import json
v2 = json.loads(open('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json').read())
print('full EM:', v2['methods']['full']['exact_match'])
print('vector_rag EM:', v2['methods']['vector_rag']['exact_match'])
print('SUPERSEDE check: see docs/QEMR_FAILURE_DIAGNOSIS.md §1')
"
# 核对 docs/REMEDIATION_FINAL_REPORT.md 里的数字与 runs/ 一致

# 4. .env 不追踪
git ls-files .env | wc -l   # 0

# 5. offline10 跑通
test -f configs/longmemeval/offline10.toml && uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml 2>&1 | tail -3
```

## 验收标准（全部勾选才算 S4a+S5 完成）

### S4a 部分

- [ ] `.env.example` 含 `EEM_EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `ARK_API_KEY` 字段（值空 + 注释）
- [ ] `configs/longmemeval/offline10.toml` 存在且 `uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml` 跑通（无网络）
- [ ] `docs/EVALUATION.md` 含模型 pinning note + 6m run ETEC NA 声明
- [ ] `git ls-files .env` 输出空

### S5 部分

- [ ] `docs/REMEDIATION_FINAL_REPORT.md` 存在且含 v1 vs v2 对比表
- [ ] `grep -c "test50-mimo-v2" docs/REMEDIATION_FINAL_REPORT.md` ≥ 1
- [ ] 报告含分支 C thesis（"reachable but insufficient" 或等价表述）
- [ ] 报告含 limitations 段（sentinel 33.2% / 50 题全 single-session-user / embedding 未对照 / router 38% 未修 / M2 correctness 混淆）
- [ ] 报告含 future work 段（router 修复 / embedding 换型 / M2 temporal 子集 / 500 题一致性）
- [ ] `README.md` 用 v2 数据替换 v1 标题；无 v1-only overclaim 残留
- [ ] `docs/INTERVIEW_KIT.md` 把"validated end-to-end"改成符合分支 C 的诚实表述
- [ ] `docs/RESUME_NARRATIVE.md` 30 秒陈述改成分支 C
- [ ] 报告数字与 `runs/.../summary.json` 一致（抽样 3 条核对）

### 共同部分

- [ ] `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q` 全绿
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run mypy src` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"
- [ ] `git diff 8b28a5e..HEAD -- src/` 为空（S4a+S5 不动 src/ 代码）
- [ ] 无新增 overclaim（"显著提升" / "thesis 翻盘" / "ETEC 有效" / "QEMR 有效"）
- [ ] 不跨模型对比 EM（v1 vs v2 同 mimo-v2.5；deepseek-v4-flash 不对比）
- [ ] 独立审查 PASS 或 CONDITIONAL PASS（`docs/STAGE4a5_REVIEW.md`）

## 验证命令（spec 复制）

```bash
# S4a
test -f configs/longmemeval/offline10.toml
uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml
git ls-files .env | wc -l   # 0
grep -E "EEM_EMBEDDING_BASE_URL|EMBEDDING_API_KEY|ARK_API_KEY" .env.example

# S5
test -f docs/REMEDIATION_FINAL_REPORT.md
grep -c "test50-mimo-v2" docs/REMEDIATION_FINAL_REPORT.md   # >=1

# 共同
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## S4a+S5 验收失败的 fallback

**如果 `deterministic_fake` 离线模式跑不通**：
1. 不强行用真实 API 凑数。
2. 在 `docs/EVALUATION.md` 显式声明："离线 deterministic_fake 配置未跑通，原因（provider 解析错误 / config 字段缺失 / 其他）；生产复现需要 OPENAI_API_KEY + embedding tunnel"。
3. S4a 标 PARTIAL；S5 仍可完成（不依赖 offline10）。

**如果 S3 分支路由不明确（`docs/QEMR_FAILURE_DIAGNOSIS.md` §5 未写明分支 C）**：
1. **回到 S3 修复**（不在 S5 修）。
2. S5 标 BLOCKED，等 S3 §5 补全分支路由声明。

**如果 mimo-v2.5 配额再次耗尽（影响 offline10 不大，但若影响 production 复现验证）**：
1. S4a 的 offline10 用 deterministic_fake，不依赖 mimo。
2. S5 是纯文档，不依赖 LLM。
3. 若需要在 S5 验证 production 复现路径，标 BLOCKED，等配额恢复。

**如果发现 S3 数字与 runs/ 不一致**：
1. **不修 S3 产物**（已 finalize + 审查）。
2. 在 `docs/REMEDIATION_FINAL_REPORT.md` 显式声明："S3 报告数字 X，runs/ 实际数字 Y，差异原因（...）"。
3. 独立审查核对。

## 独立审查协议（S4a+S5 完成后必须执行）

S4a+S5 完成后，**派一个独立 subagent**（不审自己写的文档）执行以下检查，输出 `docs/STAGE4a5_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **overclaim 扫描**：`rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效|QEMR 有效' docs/REMEDIATION_FINAL_REPORT.md docs/INTERVIEW_KIT.md docs/RESUME_NARRATIVE.md README.md` —— 无新增 overclaim（disclaimer 除外）。
3. **数字一致性**：从 `docs/REMEDIATION_FINAL_REPORT.md` 抽样 3 条数字，与 `runs/.../summary.json` 或 `docs/QEMR_FAILURE_DIAGNOSIS.md` 核对，必须一致。
4. **分支 C 一致性**：README / INTERVIEW_KIT / RESUME_NARRATIVE / REMEDIATION_FINAL_REPORT 的 thesis 表述一致（"reachable but insufficient"，不翻盘，不 negative）。
5. **不跨模型对比**：报告里没有 v2 vs deepseek-v4-flash EM 对比（N8）。
6. **src/ 未动**：`git diff 8b28a5e..HEAD -- src/` 为空。
7. **offline10 跑通**：独立重跑 `uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml`，确认无网络调用 + 产 summary.json。
8. **.env 不追踪**：`git ls-files .env | wc -l` == 0。
9. **全套回归绿**：pytest / ruff / mypy / smoke 四命令输出。
10. **git 状态**：除 `runs/` 外工作区干净；每个 step 单独 commit。
11. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；不 commit datasets / secrets / model weights / benchmark caches。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S4a+S5 修复；CONDITIONAL PASS 标注未决项（如 offline10 未跑通但已声明）。审查通过后整改 spec 闭环。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建的文件 + 每个文件的变更摘要。
2. **S4a 可复现性状态**：`.env.example` 完整度 / `offline10.toml` 跑通 / 6m NA 声明 / `.env` 不追踪。
3. **S5 最终 thesis**：分支 C 的一句话定位 + 正面贡献 + limitations + future work。
4. **v1 vs v2 对比表**：6 方法 EM 对比（同 mimo-v2.5，同 4096）。
5. **S3 诊断引用**：router 38% / weights qemr≥ablations / M2 74% tie / embedding skipped。
6. **验收标准勾选**：S4a 4 条 + S5 9 条 + 共同 7 条 = 20 条逐条 ✅/❌/⚠️ + 验证命令输出。
7. **独立审查结果**：`docs/STAGE4a5_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现。
8. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出。
9. **异常/风险**（如有）：
   - deterministic_fake 离线模式未跑通 → S4a PARTIAL
   - S3 分支路由不明确 → S5 BLOCKED
   - 数字不一致 → 显式声明
10. **整改闭环状态**：S0-S5 全链路完成声明 + 整改 spec 闭环（或未闭环项清单）。

## 不做什么（防止 scope creep）

- 不跑新 benchmark（S5 只写文档，引用 S2/S3 数据）。
- 不跑 500 题（分支 C 不要求；500 题是 future-work / 分支 B）。
- 不修 router.py 规则（N9 scope 延续；写进 future-work）。
- 不改 production QEMR_WEIGHT_PROFILES（S3 已验证不修）。
- 不修 R3（multi_valued 过打，不在 scope）。
- 不动 prompt（sentinel 33.2% 写进 limitations）。
- 不跑 embedding 换型（S3 §3 跳过，写进 future-work）。
- 不擅自写论文 draft（分支 C 不要求 paper_draft.md；分支 A 才要求）。
- 不声称 thesis 翻盘 / ETEC 有效 / QEMR 有效（分支 C 是"可达但不足以提升"）。
- 不跨模型对比 EM（v1 vs v2 同 mimo-v2.5；deepseek-v4-flash 不对比）。
- 不 commit secrets（`.env` 不追踪；`.env.example` 只留字段名）。

## 故障排查

| 问题 | 解决 |
|---|---|
| `deterministic_fake` config 解析失败 | 检查 `configs/longmemeval/offline10.toml` 的 provider 字段；参考 `benchmarks/common/providers.py` 的 `resolve_provider_config` |
| offline10 跑通但产 no_memory=0.0 全错 | deterministic_fake 是占位模型，EM=0 是预期；验收只看"跑通无网络"，不看 EM |
| README v1 标题找不到 | 用 `rg -n "0\.46\|v1\|validated" README.md` 定位 |
| INTERVIEW_KIT 没有"validated end-to-end" | 可能已被前阶段改过；核对当前表述是否与分支 C 一致即可 |
| 数字与 runs/ 不一致 | 不修 runs/（已 finalize）；在报告显式声明差异 |
| 独立审查发现 overclaim | 回到对应文档删除 overclaim；重新 commit |

## 预计时间

- S4a（Step A0-A4）：1 天。
- S5（Step B0-B5）：2 天。
- 独立审查：1-2 小时。
- 总计：3 天 + 审查。

## 文献依据

- **LongMemEval** (arXiv:2410.10813, ICLR 2025)：§4 question categories 定义 single-session-user；§5.4 time-aware query expansion +6.8%~11.3% temporal（S3 §1 验证 router 把 temporal 类分错）。
- **MemTrace** (arXiv:2606.17328)："evidence 10x retrievable than missing" → 本项目 100% provenance 是真实贡献，准确率 null 是 honest finding（分支 C 的正面 framing）。
- **Filesystem-Based Memory** (arXiv:2607.26637)："no agent converts organization into better answers" → 警示 QEMR query-adaptive 在 LongMemEval 上无 surface（S3 §2 验证：uniform 0.42 < qemr 0.48，组织有微弱收益，但不足以翻盘）。
- **Mem0** (arXiv:2504.19413)：graph memory +2% → 结构化收益有限，与 S2 +0.02 一致。
- **LoCoMo §9**：`no_temporal` (0.3654) > `qemr` (0.3000) → S3 §2 在 LongMemEval 上**不成立**（no_temporal 0.46 < qemr 0.48），跨数据集差异。
- **审计 `9of10_AUDIT.md`**：8/10 自评降级触发整改 spec；S5 闭环后更新审计状态。

## 历史阶段路由回顾

- S0 (止血) ✅ DONE commit `b60b38d`
- S1a (schema + prompt v2) ✅ DONE commit `162183c`
- S1b (5q smoke + reachability) ✅ DONE commit `00b3dc6`
- S1c (required fact_slot + v3 prompt) ✅ DONE commit `ab5ba1a` (CONDITIONAL PASS — sentinel 39.7%)
- S4b (vector_rag 延迟修复) ✅ DONE commit `46b7b38`
- S2 (50q v2 run + 诊断) ✅ DONE commit `17b1014` (CONDITIONAL PASS — SUPERSEDE=109, full EM +0.02)
- S3 (QEMR diagnosis + M2) ✅ DONE commits `a428e8d`→`8b28a5e` (CONDITIONAL PASS — router 38%, weights sound, M2 74% tie → branch C)
- **S4a + S5 (可复现性 + 定稿分支 C) ← 本阶段**
- 整改闭环：S4a+S5 审查通过后，整改 spec 全链路完成。

## 分支决策回顾

S2+S3 结果触发的 S5 路由（spec line 529-535）：
- ✅ **SUPERSEDE > 0 (109)** → 不走分支 A
- ❌ **full EM 没翻盘 (+0.02)** → 不走分支 B
- ✅ **M2 已跑（74% tie, 0% full-stale）** → 分支 C 证据就位
- → **S5 走分支 C（中间路线）**："ETEC 的 SUPERSEDE 在真实数据上可达但不足以提升整体准确率——证据约束的 operating surface 在 LongMemEval 的 single-session-user 类上太窄。"

分支 C 的诚实定位（spec line 458-463）：
- thesis："ETEC 的 SUPERSEDE 在真实数据上可达但**不足以提升整体准确率**——证据约束的 operating surface 在 LongMemEval 的 single-session-user 类上太窄。"
- pivot 到 auditability：用 SUPERSEDE > 0 证明逻辑可达，用准确率 null 证明真实场景 surface 窄。
- "这是最诚实的结果。"
