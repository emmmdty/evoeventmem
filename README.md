# EvoEventMem

面向长程 Agent 的**证据感知时态事件记忆系统**项目脚手架。项目目标不是重新实现一个完整 Coding Agent，而是构建一个框架无关的 Memory Service，并用公开基准证明其算法收益，再通过 OpenCode MCP 适配器展示工程落地。

## 项目主线

1. 跑通 LongMemEval 与 LoCoMo 的数据、评测和强基线。
2. 实现带原始证据、有效时间和版本关系的事件记忆写入链路。
3. 实现 ETEC：证据约束的时态事件整合与冲突更新。
4. 实现 QEMR：查询自适应的向量—事件图混合检索。
5. 完成公开基准、消融、效率和错误分析。
6. 将 Memory Service 以 MCP 接入 OpenCode，形成 Coding/Debug Agent 演示。

附加优化项目必须在主线结果稳定后启动，见 [`docs/archive/OPTIONAL_EXTENSIONS.md`](docs/archive/OPTIONAL_EXTENSIONS.md)。

## 为什么采用独立 Memory Service

```text
LongMemEval / LoCoMo ─┐
Reference Agent ──────┼──> EvoEventMem API ──> PostgreSQL/pgvector
OpenCode MCP ─────────┤          │
其他 Agent Runtime ───┘          └──> API 模型 / 本地 OpenAI-compatible 模型
```

Agent Runtime 负责规划、工具调用和执行；本项目负责记忆写入、整合、检索、证据追踪与生命周期管理。

## 当前状态

已实现 M01–M16 完整主线（M15 分析已完成，见 [`docs/EVALUATION.md`](docs/EVALUATION.md) 与 [`docs/STRONG_RESULTS_SMALL_SAMPLE.md`](docs/STRONG_RESULTS_SMALL_SAMPLE.md)）：

- 事件记忆写入链路：候选提取、实体/事件链接、ETEC 时序整合（ADD/MERGE/SUPERSEDE/REJECT）、证据溯源持久化；
- 查询链路：规则路由器、QEMR 查询自适应混合检索（向量+时序+图）、token 预算打包；
- 提取方法论：分块提取 + 确定性 span 定位（模型无关，LLM 只做语义判断）；
- 双基准评测工程：LongMemEval / LoCoMo 运行器、统一预算、无 oracle 泄漏、不可变产物（FINALIZED.json）、内容寻址分析；
  注意：LongMemEval 已有 finalized 内容寻址产物（`runs/publication/` + `runs/analysis/`）；LoCoMo 目前只有 legacy
  `runs/main` 产物（1986 题主 run，尚未升级到 finalized 管线）；
- 生产服务：FastAPI + PostgreSQL/pgvector（asyncpg 池）、多租户隔离（tenant/user/session）、fail-closed 降级、可观测性、Docker Compose。

**整改闭环（S0→S5，分支 C 中间路线，2026-08-20）**：S0 诚信止血 → S1a/S1b/S1c 修 ETEC 第一道闸门（schema + v3 prompt + required fact_slot）→ S4b 修 vector_rag 延迟 → S2 50 题 v2 run → S3 QEMR 失效根因诊断 + M2 stale-judge → **S4a 可复现性 config + S5 定稿**。详见 [`docs/REMEDIATION_FINAL_REPORT.md`](docs/REMEDIATION_FINAL_REPORT.md)（定稿）+ [`docs/QEMR_FAILURE_DIAGNOSIS.md`](docs/QEMR_FAILURE_DIAGNOSIS.md)（S3 根因）。

评测结论（v2 S2/S3，全部数字可溯源到 `runs/` 产物）：

- **分支 C thesis（中间路线）**：ETEC 的证据约束 SUPERSEDE 在真实数据上**可达但不足以提升整体准确率**——`full`（ETEC+QEMR flagship）EM=0.48 仍低于 `vector_rag`=0.56（Δ −0.08）；SUPERSEDE=109 在 40/50 样本上触发（v1 为 0，v3 required-fact-slot prompt 闭合 R1+R1b 屏障后首次在真实数据触发），四重 gate 可达性 PASS（非 XFAIL）。两个已识别根因（均非权重 profile / SUPERSEDE 消费）：(a) router 误路由（500 题准确率 38% < 80% 阈值，**可修复**，留 future-work，N9 scope）；(b) operating-surface 太窄（50 题全为 `single-session-user`，M2 stale-judge 74% tie，consolidation 无时间显著性答案可改 reader 可见值，**结构性**）。
- **v1 vs v2 诚实 nuance**：`full` vs `event_no_etec` gap 从 v1 的 −0.08（ETEC 有害）收敛到 v2 的 0.00（ETEC 中性），但收敛是通过 `event_no_etec` 降 0.06（0.54→0.48）实现的，不是 `full` 升 0.02（0.46→0.48）实现的。v3 prompt 让 ETEC **中性化**，不是**有效化**。
- **正面贡献（基础设施，非准确率声明）**：(1) 100% provenance 覆盖率基础设施；(2) 33/33 失败人工复核归因；(3) 不可篡改 `FINALIZED.json`；(4) S3 三层机制级根因诊断（router 38% / weights qemr≥ablations / M2 74% tie → 定位到 router 规则 + operating surface，排除权重 + SUPERSEDE 消费）。
- **不声称翻盘 / ETEC 有效 / QEMR 有效**（分支 C 是"可达但不足以提升"；AGENTS.md 代码评审规则 + `METHODOLOGY_CHANGE.md` 预注册框架）。任何强 claim 需 p-value + CI 支撑，本阶段无新实验不产生新 p-value。
- **跨模型对比禁止**：v1 vs v2 都用 mimo-v2.5（同 4096 预算，可对比）；24 题 deepseek-v4-flash run 已停服、不可复现、**禁止与 mimo-v2.5 对比**（AGENTS.md N8）。

## test50-mimo-v2-factslot (n=50, mimo-v2.5, v3 prompt, 2026-08-19, 整改 S2)

> S2 v2 run（FINALIZED 在 `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`，git `17b1014` 干净）。S3 权重消融产物在同目录的 `m13-longmemeval-test50-mimo-v2-ablation/` 子目录。整改 spec `docs/REMEDIATION_SPEC.md` S2/S3/S5。

v1 vs v2 EM 对比表（同模型 mimo-v2.5，同 4096 预算，可对比；24 题 deepseek-v4-flash run 不对比——N8）：

| method         | v1 EM | v2 EM | Δ     |
|----------------|-------|-------|------|
| no_memory      | 0.00  | 0.00  | +0.00 |
| full_context   | 0.00  | 0.00  | +0.00 |
| vector_rag     | 0.56  | 0.56  | +0.00 |
| event_no_etec  | 0.54  | 0.48  | -0.06 |
| etec           | 0.52  | 0.46  | -0.06 |
| full (flagship)| 0.46  | 0.48  | +0.02 |

**诚实解读**：

- `full` (ETEC+QEMR flagship) EM=0.48，仍低于 `vector_rag` (0.56) 0.08 个点；S2 v3 prompt 让 `full` +0.02（微升，非翻盘）。
- 拆掉 ETEC（`full` → `event_no_etec`）v2 上 Δ 0.00（0.48 vs 0.48，ETEC 中性；v1 是 Δ −0.08 ETEC 有害）；gap 收敛由 `event_no_etec` 降 0.06 驱动，非 `full` 升 0.02 驱动。
- **ETEC actions（v2）**：`ADD=7188, MERGE=1770, REJECT=352, SUPERSEDE=109`（across 40/50 samples）。SUPERSEDE=109 是**第一次在真实数据触发**（v1 为 0），四重 gate 可达性 PASS（非 XFAIL）；replay/online 一致性：109 SUPERSEDE 完全一致，2/50 样本 minor ADD↔MERGE 重分类（known limitation）。
- **fact_slot / sentinel（v2，9419 events）**：有效 fact_slot=66.8% (6295/9419) ✅ ≥ 50% floor；valid_from=66.8% ✅；sentinel=33.2% (3124/9419) ⚠️ ≥ 20% ceiling（known weakness，写进 limitations）。
- **S3 router 诊断**：全 500 题 router 准确率 38% (190/500) < 80% N9 阈值；50 题切片 4% (2/50，全为 `single-session-user`，40/50 误路由到 HYBRID 权重中性，8/50 到 TEMPORAL 把 dense 权重从 1.0 降到 0.3)。`_FACT_RE` 不匹配 LongMemEval 的 "what + noun + did + subject + verb" 句式。
- **S3 权重消融**（同模型同预算，只差 retrieval weight）：`qemr`=0.48 ≥ `no_temporal`=0.46 / `no_graph`=0.48 / `uniform`=0.42（权重 profile 不是根因，不修）。
- **S3 M2 stale-judge**（judge=minimax-m3 ≠ reader mimo-v2.5，31 差异预测样本）：tie 74.2% (23/31)、event_no_etec less-stale 19.4% (6/31，correctness 混淆)、full less-stale 3.2% (1/31)、parse-error 3.2% (1/31)。**retrieval 未忽略 SUPERSEDE；operating surface 窄**。
- **跨模型对比禁止**：v1 vs v2 都用 mimo-v2.5 reader+extractor（同 4096 预算，可对比）；既有 24 题 finalized run 用 deepseek-v4-flash（已停服、不可复现、**禁止跨模型对比**——AGENTS.md N8）。
- 整改定稿见 [`docs/REMEDIATION_FINAL_REPORT.md`](docs/REMEDIATION_FINAL_REPORT.md)（分支 C）；S3 根因见 [`docs/QEMR_FAILURE_DIAGNOSIS.md`](docs/QEMR_FAILURE_DIAGNOSIS.md)；可复现性见 [`docs/EVALUATION.md`](docs/EVALUATION.md) §6.5。

## LoCoMo (n=1986, legacy run)

> 来源：`runs/main/report/report.md`（M14 legacy run，未升级 finalized 管线）。当前 README 之前只提 token 节省，不提 `full` 的准确率——这是 S0 整改必须补披露的"系统性隐瞒"。

| method         | exact_match | token_f1 | tokens/query |
|----------------|--------------|----------|--------------|
| full_context   | 0.0670       | 0.1507   | 4102.3       |
| vector_rag     | 0.0861       | 0.1873   | 142.2        |
| full (flagship)| 0.0634       | 0.1508   | 200.3        |

**统计结论**（`runs/main/report/report.md` C01）：`vector_rag` vs `full` Δ +0.0227，95% CI [+0.0141, +0.0312]，p=0.000 —— flagship `full` 在 LoCoMo 1986 题上**显著劣于**简单 `vector_rag` baseline（同 reader、同 budget、同 prompt）。整改方案见 `docs/REMEDIATION_SPEC.md` S1a/S2/S3。

项目结构、评测协议、部署决策与竞品定位分别见
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)、[`docs/EVALUATION.md`](docs/EVALUATION.md)、
[`docs/DEPLOYMENT_DECISIONS.md`](docs/DEPLOYMENT_DECISIONS.md)、[`docs/COMPETITIVE_ANALYSIS.md`](docs/COMPETITIVE_ANALYSIS.md)。

## 快速开始

要求：Python 3.11–3.13、Git、推荐使用 `uv`。

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run python -m evoeventmem.cli smoke
uv run uvicorn evoeventmem.api.app:app --reload
```

不使用 `uv`：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## 使用 Codex 开发

先查看任务列表：

```bash
python scripts/taskctl.py list
python scripts/taskctl.py show M01
python scripts/taskctl.py prompt M01
```

交互式 Codex：

```bash
codex
```

然后粘贴 `taskctl.py prompt M01` 的输出。每个任务单独开一个会话，只执行一个任务。

非交互式运行：

```bash
python scripts/taskctl.py prompt M01 > /tmp/M01.prompt.txt
codex exec --sandbox workspace-write "$(cat /tmp/M01.prompt.txt)"
```

不要使用“完成整个项目”一类提示词。详见 [`docs/archive/CODEX_WORKFLOW.md`](docs/archive/CODEX_WORKFLOW.md)。

## 数据集

主线数据：

```bash
# 最小烟雾测试，体积小、可先验证评测链路
python scripts/data/download_longmemeval.py --variant oracle

# LongMemEval 主实验
python scripts/data/download_longmemeval.py --variant s

# LoCoMo
python scripts/data/download_locomo.py

python scripts/data/verify_datasets.py
```

完整下载链接、许可和可选基准见 [`docs/DATASETS.md`](docs/DATASETS.md)。

## 目录

```text
src/evoeventmem/        核心 Python 包
benchmarks/             公开基准适配与实验输出约定
adapters/               OpenCode/Pi 等 Agent Runtime 适配器
tasks/mainline/         必须按顺序完成的主线任务
tasks/optional/         主线完成后的附加优化
.agents/skills/         Codex 仓库级 Skills
.codex/agents/          窄职责 Codex subagents
docs/                   架构、评测、数据和求职材料
```

## 完成定义

主线完成不是“Demo 能运行”，而是同时满足：

- 至少 LongMemEval 与 LoCoMo 两个公开基准；
- No Memory、Full Context、Vector RAG 和完整方法公平对比；
- ETEC、QEMR 分别有消融；
- 报告 Accuracy/F1、Evidence F1、token、延迟；
- 实验子集可复现（smoke 配置 + finalized 内容寻址产物，见 `docs/EVALUATION.md`）；
- README 中只填写真实测得的简历指标。

当前状态对照：

| 条目 | 状态 |
|---|---|
| 双基准 | ✅ LongMemEval（finalized 内容寻址：v1 `m13-longmemeval-test50-mimo/` + v2 `m13-longmemeval-test50-mimo-v2-factslot/` + S3 消融 `m13-longmemeval-test50-mimo-v2-ablation/`）；⚠️ LoCoMo 仅有 legacy `runs/main` 产物（M14 run，未升级 finalized 管线） |
| 方法公平对比 | ✅ 50 题 6 方法 v1+v2 同模型（mimo-v2.5，同 4096 预算）；LoCoMo 1986 题；24 题 deepseek-v4-flash run 已停服、禁止跨模型对比（N8） |
| ETEC/QEMR 消融 | ✅ 六因子 `runs/ablation/`（全部 finalized）；✅ S3 QEMR 权重消融三臂（`no_temporal`/`no_graph`/`uniform`，同模型同预算，`qemr` 0.48 ≥ 全部）；✅ ETEC SUPERSEDE 真实数据 v2=109 across 40/50 samples（v1=0，v3 required-fact_slot 闭合 R1+R1b 屏障后首次触发），四重 gate 可达性 PASS（非 XFAIL） |
| 指标报告 | ✅ M15 内容寻址报告（EM/token_f1/evidence_f1/tokens）；✅ S3 M2 stale-judge 已跑（judge=minimax-m3 ≠ reader mimo-v2.5，31 缓存 judge 调用，74% tie / 0% full-stale → retrieval 未忽略 SUPERSEDE，operating surface 窄） |
| 整改闭环 | ✅ S0→S5 全链路完成（分支 C 中间路线）；定稿 [`docs/REMEDIATION_FINAL_REPORT.md`](docs/REMEDIATION_FINAL_REPORT.md)，S3 根因 [`docs/QEMR_FAILURE_DIAGNOSIS.md`](docs/QEMR_FAILURE_DIAGNOSIS.md)，独立审查 [`docs/STAGE4a5_REVIEW.md`](docs/STAGE4a5_REVIEW.md) |
| 复现 | ✅ offline `configs/longmemeval/offline10.toml`（`deterministic_fake`，benchmark 运行零网络调用）；✅ smoke config + 不可变产物 + 内容寻址分析（`--config`/`--resume-dir` 断点续跑）；✅ `.env.example` 全字段 + `.env` 不追踪；⚠️ 500 run `configs/longmemeval/main500.toml` 已入库、网关配额阻断待续（future-work，分支 C 不要求补跑） |
| MCP 集成 | ⚠️ implemented, not deployed（`adapters/opencode/` 代码存在，未上线） |
