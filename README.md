# EvoEventMem

面向长程 Agent 的**证据感知时态事件记忆系统**。

项目目标不是重新实现一个完整 Coding Agent，而是构建一个框架无关的 Memory Service，用公开基准证明算法收益，再通过 MCP 适配器展示工程落地。

## 架构

```text
LongMemEval / LoCoMo ─┐
Reference Agent ──────┼──> EvoEventMem API ──> PostgreSQL/pgvector
OpenCode MCP ─────────┤          │
其他 Agent Runtime ───┘          └──> API 模型 / 本地 OpenAI-compatible 模型
```

Agent Runtime 负责规划、工具调用和执行；本项目负责记忆写入、整合、检索、证据追踪与生命周期管理。

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 核心贡献

- **ETEC**（Evidence-constrained Temporal Consolidation）：证据约束的时态事件整合，输出 ADD / MERGE / SUPERSEDE / REJECT 四类决策，每条记忆携带精确到字符 span 的原始证据。
- **QEMR**（Query-adaptive Hybrid Retrieval）：查询自适应的向量—事件图混合检索，按查询意图动态加权。

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

API 端点默认无需认证（开发模式）。生产环境设置 `EEM_API_KEYS` 启用 Bearer token 认证：

```bash
# .env 中设置（逗号分隔多个 key）
EEM_API_KEYS=your-api-key-here

# 请求时携带
curl -H "Authorization: Bearer your-api-key-here" http://localhost:8000/v1/memories/search?q=test
```

不使用 `uv`：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## 数据集

```bash
# 最小烟雾测试
python scripts/data/download_longmemeval.py --variant oracle

# LongMemEval 主实验
python scripts/data/download_longmemeval.py --variant s

# LoCoMo
python scripts/data/download_locomo.py

python scripts/data/verify_datasets.py
```

## 项目结构

```text
src/evoeventmem/        核心 Python 包（domain, services, infra, api）
benchmarks/             公开基准适配、运行器和分析
adapters/               Agent Runtime 适配器（OpenCode MCP, Pi）
configs/                基准运行和部署的 TOML 配置
tasks/mainline/         按顺序完成的主线任务（M00–M18）
tasks/optional/         主线完成后的附加优化（O01–O09）
docs/                   架构、评测和研究文档
docs/archive/           已归档的历史文档（勿编辑）
.agents/skills/         仓库级 Agent 技能
```

## 当前状态

M01–M17 主线已完成，M18（复现、Demo 和发布）进行中。

### 已实现

- 事件记忆写入链路：候选提取、实体/事件链接、ETEC 时序整合、证据溯源持久化
- 查询链路：规则路由器、QEMR 查询自适应混合检索、token 预算打包
- 提取方法论：分块提取 + 确定性 span 定位（模型无关，LLM 只做语义判断）
- 双基准评测工程：LongMemEval / LoCoMo 运行器、统一预算、不可变产物
- 生产服务：FastAPI + PostgreSQL/pgvector（asyncpg 池）、多租户隔离、Docker Compose
- MCP 适配器：OpenCode 6 工具接入（implemented, not deployed）

### 评测结论

LongMemEval 50 题（mimo-v2.5，同 4096 预算）：

| method | EM |
|---|---|
| vector_rag | 0.56 |
| full (ETEC+QEMR) | 0.48 |
| event_no_etec | 0.48 |

- `full` (ETEC+QEMR flagship) EM=0.48，仍低于 `vector_rag` (0.56)
- ETEC 从 v1 有害（Δ −0.08）收敛到 v2 中性（Δ 0.00），SUPERSEDE 首次在真实数据触发（109/40 samples）
- 整改闭环 S0→S5 完成（分支 C 中间路线）

详见 [`docs/EVALUATION.md`](docs/EVALUATION.md)、[`docs/REMEDIATION_FINAL_REPORT.md`](docs/REMEDIATION_FINAL_REPORT.md)。

### 完成定义

| 条目 | 状态 |
|---|---|
| 双基准 | ✅ LongMemEval（finalized）；⚠️ LoCoMo 仅有 legacy 产物 |
| 方法公平对比 | ✅ 50 题 6 方法 v1+v2 同模型 |
| ETEC/QEMR 消融 | ✅ 六因子消融 + S3 权重消融 |
| 指标报告 | ✅ EM/token_f1/evidence_f1/tokens |
| 整改闭环 | ✅ S0→S5 全链路完成 |
| 复现 | ✅ 离线 config + smoke config + 不可变产物 |
| MCP 集成 | ⚠️ implemented, not deployed |

## 开发工作流

```bash
# 查看任务列表
python scripts/taskctl.py list
python scripts/taskctl.py show M01
python scripts/taskctl.py prompt M01
```

每个任务单独开一个会话，只执行一个任务。详见 `AGENTS.md`。

## 技术栈

- Python 3.11+ / FastAPI / PostgreSQL + pgvector / asyncpg
- 多租户隔离（tenant/user/session）
- Docker Compose 部署
- MCP 协议接入 OpenCode
