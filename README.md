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

评测结论（当前口径，全部数字可溯源到 `runs/` 产物）：

- **机制级强结果（24 样本小样本闭环）**：证据溯源覆盖率 100%、0 分格修复 10→4（merge gate + 预算满装）、LoCoMo 记忆方法 vs `full_context`（trivial 基线，把全部历史塞进 prompt）省约 96.5% 输入 token（**注意：vs `vector_rag`（公平 RAG 基线）`full` 反而贵 41% 且 EM 更低，见下表**）；33/33 失败人工复核（主因是 reader 精确输出，真正检索/提取/预算失效仅 7/33）。
- **O09 机制诊断增量（2026-08-18，见 [`docs/8of10_ACCEPTANCE.md`](docs/8of10_ACCEPTANCE.md)）**：
  - SUPERSEDE 诊断：真实数据 0/32 触发（R1 提取不写 fact_slot → R1b 不写 valid_from point-interval 不重叠 → R3 LLM 输出形态三层级联屏障），同代码在受控夹具上 4/12 SUPERSEDE（`runs/mechanism/etec_stress/…/summary.json`）→ 真实不可达根因是提取管线 metadata 缺口，非 consolidation 逻辑 bug；interval 算子 500 题确定性预筛 16 例 interval（15 BETWEEN + 1 AT，3.2%），BEFORE/AFTER=0（`runs/mechanism/router_screen/router-screen.json`）。
  - 大样本一致性：4 个 finalized 24 样本 run（n=96）provenance 100%（Wilson 95% CI 两两重叠于 1.0）+ 记忆方法预算饱和 1.0 + SUPERSEDE=0 全 run + 失败归因分布结构同构（`runs/mechanism/consistency/consistency.json`）；1986-LoCoMo 效率 96.5% vs `full_context`（trivial 基线；vs `vector_rag` `full` 反而贵 41%）与 r2 复核"reader 主导错误"方向一致。500 run 配额阻断待续（网关 429/403），预注册 §6.3 末段兜底路径 + 功效论证已交付（n=500 最小可检测效应 ±0.018–0.039 > 观测 0.005–0.014，500 run 无显著性是预期内）。
  - 对照臂口径修正：`etec`=FIXED_VECTOR、`event_no_etec`=QEMR（`benchmarks/longmemeval/run.py:153-158`，8of10 报告原文记作 `run.py:153-158`）→ 既有"etec vs event_no_etec"混入检索策略因素；**ETEC 隔离主对照 = `full` vs `event_no_etec`（同 QEMR，只差 ETEC 存储/整合）**，已写入 spec §3.3/§13 决议 1 与 `docs/EVALUATION.md` §7。
- **无端到端 QA 增益声明**：`full` vs `vector_rag` 在 24 样本上无正向显著差异；方法论定位是"机制证据链 + 可复现产物"，不是绝对分数竞争。详见 `docs/METHODOLOGY_CHANGE.md`（含大样本一致性验证的定位）。

## test50-mimo (n=50, mimo-v2.5, 2026-08-18)

> 本节为 S0 整改（诚信止血）补披露——`test50-mimo` 是项目最大的 finalized LongMemEval run（50 题，FINALIZED 在 `runs/publication/m13-longmemeval-test50-mimo/`，git `e585d7e` 干净），与 8of10 验收文档同一天生成，但在所有叙事文档中缺席。整改 spec `docs/REMEDIATION_SPEC.md` S0 步骤 2 要求主动披露。

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
| 双基准 | ✅ LongMemEval（finalized 内容寻址）；⚠️ LoCoMo 仅有 legacy `runs/main` 产物（M14 run，未升级 finalized 管线） |
| 方法公平对比 | ✅ 24 样本 6 方法（`runs/publication/longmemeval-test20-6m`）+ LoCoMo 1986 题 |
| ETEC/QEMR 消融 | ✅ 六因子 `runs/ablation/`（全部 finalized）；⚠️ ETEC SUPERSEDE 真实数据 0/32（R1+R1b+R3 级联屏障）vs 受控夹具 4/12（`runs/mechanism/etec_stress/`、`runs/mechanism/evala/metrics.partial.json`），机制诊断见 `docs/8of10_ACCEPTANCE.md` §a |
| 指标报告 | ✅ M15 内容寻址报告（EM/token_f1/evidence_f1/tokens）；⚠️ stale-memory error 机制诊断完成（SUPERSEDE 在真实数据结构性不可达，M2 stale judge 因配额未跑、由诊断隐含无 with/without 差异），见 `docs/8of10_ACCEPTANCE.md` §a/§b 风险 |
| 复现 | ✅ smoke config + 不可变产物 + 内容寻址分析（`--config`/`--resume-dir` 支持断点续跑）；✅ 4 finalized 24 样本 run（n=96）一致性重算（`runs/mechanism/consistency/consistency.json`，sha256:5764711a…）；⚠️ 500 run `configs/longmemeval/main500.toml` 已入库、网关配额阻断待续 |
| MCP 集成 | ⚠️ implemented, not deployed（`adapters/opencode/` 代码存在，未上线） |
