# Phase 1 阻塞项执行提示词

## 背景

S8 分层验证完成后，项目定位为分支 C（中间路线）。3 路独立调查识别了 Phase 1 阻塞项：

1. **T1 选择性 SUPERSEDE**：ETEC 在 temporal-reasoning 上有害（etec=0.111 vs vector_rag=0.222），因为 SUPERSEDE 替换旧值丢失排序所需历史上下文。修复方案：router 判定 temporal-reasoning 时跳过 SUPERSEDE。
2. **T2 API 认证**：所有端点无认证，任何人可读写删数据。修复方案：Bearer token 验证 middleware。
3. **T3 CI/CD**：无自动化质量门禁。修复方案：GitHub Actions workflow。

**3 个任务互相独立，可并行执行**。每个任务完成后需通过 3 轮独立验收，验收发现问题必须修复。

## 执行约束

- **并行度**：最多 3 个 subagent 并行（T1/T2/T3 各一个）
- **验收轮次**：每轮验收由独立 subagent 执行，发现问题必须在当前轮修复，不可跳到下一轮
- **验收通过标准**：3 轮全部 PASS 才算完成
- **不允许跨任务修改**：T1 不改 API 层，T2 不改 ETEC 层，T3 不改业务逻辑
- **测试必须全绿**：每轮验收前运行 `uv run ruff check . && uv run mypy src && uv run pytest -q`

## T1: 选择性 SUPERSEDE

### 问题

S8 分层 100q 结果：

| question_type | n | vector_rag | full | etec | etec vs vr |
|---|---|---|---|---|---|
| knowledge-update | 15 | 0.133 | 0.267 | **0.467** | +0.334 |
| temporal-reasoning | 27 | **0.222** | 0.148 | 0.111 | **-0.111** |
| ETEC home-court (TR+KU) | 42 | 0.190 | 0.190 | 0.238 | +0.000 |

ETEC SUPERSEDE 在 KU 上有效（替换旧值 → reader 看到正确新值），但在 TR 上有害（替换旧值 → reader 丢失排序所需历史上下文）。

### 方案

修改 `src/evoeventmem/consolidation.py` 的 `_apply_supersede()`，添加 router intent 检查：

```python
# 当 router 判定 temporal-reasoning 时，跳过 SUPERSEDE
if routing_intent == QueryIntent.TEMPORAL:
    # 保留旧值供排序，降级为 MERGE
    return ConsolidationAction.MERGE, ...
```

需要修改的文件：
- `src/evoeventmem/consolidation.py`：`_apply_supersede()` 添加 intent 检查
- `src/evoeventmem/etec.py`：传递 router intent 到 consolidator
- `benchmarks/longmemeval/run.py`：确保 router intent 在 ETEC 路径中可用

需要新增的测试：
- `tests/consolidation/test_selective_supersede.py`：
  - temporal intent → SUPERSEDE 降级为 MERGE
  - non-temporal intent → SUPERSEDE 正常执行
  - 边界：no-memory intent 的行为

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T1-A1 | 现有 consolidation 测试全绿 | `uv run pytest tests/consolidation -q` |
| T1-A2 | 新增 selective supersede 测试全绿 | 同上 |
| T1-A3 | router intent 正确传递到 consolidator | 代码审查 + 测试 |
| T1-A4 | temporal intent 时 SUPERSEDE 不触发 | 测试验证 |
| T1-A5 | non-temporal intent 时 SUPERSEDE 正常触发 | 测试验证 |
| T1-A6 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |

## T2: API 认证

### 问题

所有 FastAPI 端点（写入、搜索、删除）无认证，任何人可读写删数据。`X-Tenant-Id`/`X-User-Id` 由客户端自行提供，无验证。

### 方案

添加 Bearer token 验证 middleware：

1. 新增 `src/evoeventmem/api/auth.py`：
   - `AuthMiddleware`：检查 `Authorization: Bearer <token>` header
   - 从环境变量 `EEM_API_KEYS` 加载有效 token 列表（逗号分隔）
   - 无 token → 401；token 无效 → 403
   - `GET /health` 和 `GET /readiness` 不需要认证

2. 修改 `src/evoeventmem/api/app.py`：
   - 添加 `AuthMiddleware` 到 middleware stack
   - `/health`、`/readiness`、`/metrics` 路径排除认证

3. 新增 `.env.example` 条目：
   ```
   EEM_API_KEYS=your-api-key-here
   ```

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T2-A1 | 无 token 返回 401 | curl 测试 |
| T2-A2 | 无效 token 返回 403 | curl 测试 |
| T2-A3 | 有效 token 正常访问 | curl 测试 |
| T2-A4 | /health 不需要认证 | curl 测试 |
| T2-A5 | /readiness 不需要认证 | curl 测试 |
| T2-A6 | 现有 API 测试全绿（mock auth） | `uv run pytest tests/api -q` |
| T2-A7 | ruff/mypy 全绿 | 同上 |

## T3: CI/CD 配置

### 问题

无 GitHub Actions workflow，代码变更无法自动验证质量。

### 方案

新增 `.github/workflows/ci.yml`：

```yaml
name: CI
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --extra dev --extra postgres
      - run: uv run ruff check .
      - run: uv run mypy src
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --extra dev --extra postgres
      - run: uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t evoeventmem:test .
```

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T3-A1 | workflow 文件语法正确 | `act -l` 或 GitHub Actions 验证 |
| T3-A2 | lint job 包含 ruff + mypy | 代码审查 |
| T3-A3 | test job 包含 pytest | 代码审查 |
| T3-A4 | docker job 包含 build | 代码审查 |
| T3-A5 | 本地 ruff/mypy/pytest 全绿 | 命令验证 |
| T3-A6 | Docker build 成功 | `docker build -t evoeventmem:test .` |

## 并行执行策略

```
时间线：
  T0: 启动 3 个 subagent 并行执行 T1/T2/T3
  T1: 各 subagent 完成实现 → 各自运行验收
  T2: 3 个 subagent 各自提交验收报告
  T3: 主 agent 汇总 3 轮验收结果，修复遗留问题
```

### Subagent 1（T1: 选择性 SUPERSEDE）

任务：
1. 读 `src/evoeventmem/consolidation.py`、`src/evoeventmem/etec.py`、`benchmarks/longmemeval/run.py`
2. 实现选择性 SUPERSEDE 逻辑
3. 新增 `tests/consolidation/test_selective_supersede.py`
4. 运行 3 轮验收（每轮：ruff + mypy + pytest + 代码审查）
5. 输出验收报告

### Subagent 2（T2: API 认证）

任务：
1. 读 `src/evoeventmem/api/app.py`、`tests/api/`
2. 实现 `src/evoeventmem/api/auth.py` + middleware
3. 修改 `app.py` 添加认证
4. 新增 `tests/api/test_auth.py`
5. 运行 3 轮验收（每轮：ruff + mypy + pytest + curl 测试）
6. 输出验收报告

### Subagent 3（T3: CI/CD）

任务：
1. 读现有 CI 相关文件（如有）
2. 新增 `.github/workflows/ci.yml`
3. 验证 workflow 语法
4. 本地运行 lint + test + docker build
5. 运行 3 轮验收
6. 输出验收报告

## 验收流程

每轮验收由主 agent 派出独立 subagent 执行：

### 第 1 轮验收（功能正确性）

对每个任务：
1. 运行 `uv run ruff check . && uv run mypy src && uv run pytest -q`
2. 代码审查：变更是否符合任务要求
3. 边界测试：是否有遗漏的边界情况
4. 输出：PASS / FAIL + 问题清单

### 第 2 轮验收（回归 + 安全性）

对每个任务：
1. 运行全量测试 `uv run pytest -q`（含 benchmarks）
2. 检查是否有意外的副作用（跨任务影响）
3. 安全性审查（认证绕过、注入、信息泄露）
4. 输出：PASS / FAIL + 问题清单

### 第 3 轮验收（集成 + 文档）

对每个任务：
1. 集成测试：T1 的 ETEC 修改是否影响 benchmark 路径
2. 文档更新：是否需要更新 README、API 文档
3. 最终 ruff + mypy + pytest
4. 输出：PASS / FAIL + 问题清单

## 最终交付物

每个任务完成后，主 agent 汇总：
1. 3 轮验收报告
2. 变更文件清单
3. 测试结果
4. 遗留风险

全部 3 个任务 PASS 后，Phase 1 完成，进入 Phase 2。
