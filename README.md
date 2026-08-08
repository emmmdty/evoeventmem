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

已实现 M01–M16 完整主线：

- 事件记忆写入链路：候选提取、实体/事件链接、ETEC 时序整合（ADD/MERGE/SUPERSEDE/REJECT）、证据溯源持久化；
- 查询链路：规则路由器、QEMR 查询自适应混合检索（向量+时序+图）、token 预算打包；
- 提取方法论：分块提取 + 确定性 span 定位（模型无关，LLM 只做语义判断）；
- 双基准评测工程：LongMemEval / LoCoMo 运行器、统一预算、无 oracle 泄漏、不可变产物（FINALIZED.json）、内容寻址分析；
- 生产服务：FastAPI + PostgreSQL/pgvector（asyncpg 池）、多租户隔离（tenant/user/session）、fail-closed 降级、可观测性、Docker Compose。

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
- 报告 Accuracy/F1、Evidence F1、stale-memory error、token、延迟；
- 一条命令可复现实验子集；
- OpenCode 可通过 MCP 调用记忆检索和证据解释；
- README 中只填写真实测得的简历指标。
