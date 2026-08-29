# Phase 3 代码质量执行提示词

## 背景

Phase 1 阻塞项（T1 选择性 SUPERSEDE、T2 API 认证、T3 CI/CD）和 Phase 2 重要改进（T4 MDE 显式化、T5 unlimited baseline、T6 router 死代码修复、T7 多重比较校正）已全部完成并提交。

Phase 3 包含 5 个任务（T8–T12），来源：`docs/S8_POST_PLAN.md` Phase 3 节。

| 任务 | 类型 | 依赖 | 并行组 |
|------|------|------|--------|
| T8: 提取共享工具函数 | 重构 | 无 | A（必须先做） |
| T9: 拆分 300 行 prompt 函数 | 重构 | T8 完成 | B（T8 后置） |
| T10: 修复 Any 类型滥用 | 类型修复 | T8 完成 | B（与 T9 并行） |
| T11: 清理死 regex 和 dead code | 清理 | 无 | A（可与 T8 并行） |
| T12: 补充 facade 测试 | 测试 | 无 | A（可与 T8 并行） |

**并行策略**：T8、T11、T12 可并行启动；T9 和 T10 等 T8 完成后并行执行。

## 执行约束

- **并行度**：最多 2 个 subagent 并行（T8+T11 并行，然后 T9+T10 并行，T12 可与任一组并行）
- **验收轮次**：每轮验收由独立 subagent 执行，发现问题必须在当前轮修复
- **验收通过标准**：3 轮全部 PASS 才算完成
- **不允许跨任务修改**：每个任务只改指定文件
- **测试必须全绿**：每轮验收前运行 `uv run ruff check . && uv run mypy src && uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py`

---

## T8: 提取共享工具函数

### 问题

`_jaccard()`、`_cosine_similarity()`、`_unique_evidence()` 在 3 个模块重复定义，且实现有细微差异：

| 函数 | 定义位置 | 差异 |
|------|----------|------|
| `_jaccard` | `consolidation.py:939`、`retrieval.py:1473`、`scripts/extraction_variance.py:61` | consolidation 版有空集 guard；scripts 版返回 `None` 而非 `0.0`；参数名不同（`a,b` vs `left,right`） |
| `_cosine_similarity` | `retrieval.py:1480`、`benchmarks/vector_baseline.py:463` | 逻辑等价但变量命名和计算顺序不同 |
| `_unique_evidence` | `consolidation.py:1024`、`retrieval.py:1497` | consolidation 版用 `_evidence_key()` helper；retrieval 版内联元组构造 |

### 方案

1. **新建 `src/evoeventmem/core/math_utils.py`**，包含：
   - `jaccard(left: set[str], right: set[str]) -> float`：合并最健壮版本（含空集 guard，返回 `0.0`）
   - `cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float`：标准化实现
   - `unique_evidence(refs: Sequence[EvidenceRef]) -> list[EvidenceRef]`：去重逻辑

2. **修改 import**：
   - `src/evoeventmem/consolidation.py`：删除本地 `_jaccard`、`_unique_evidence`、`_evidence_key`，改为 `from evoeventmem.core.math_utils import jaccard, unique_evidence`
   - `src/evoeventmem/retrieval.py`：删除本地 `_jaccard`、`_cosine_similarity`、`_unique_evidence`，改为 `from evoeventmem.core.math_utils import jaccard, cosine_similarity, unique_evidence`
   - `benchmarks/vector_baseline.py`：删除本地 `_cosine_similarity`，改为 `from evoeventmem.core.math_utils import cosine_similarity`

3. **不修改 `scripts/extraction_variance.py`**：该脚本是独立分析工具，保留自己的 `_jaccard` 实现（返回 `None` 是有意设计）。

### 需要修改的文件

- `src/evoeventmem/core/math_utils.py`：新建
- `src/evoeventmem/consolidation.py`：删除本地函数，改 import
- `src/evoeventmem/retrieval.py`：删除本地函数，改 import
- `benchmarks/vector_baseline.py`：删除本地函数，改 import

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T8-A1 | `math_utils.py` 包含 jaccard、cosine_similarity、unique_evidence | 文件审查 |
| T8-A2 | consolidation.py 不再定义 `_jaccard`/`_unique_evidence`/`_evidence_key` | `rg "_jaccard\|_unique_evidence\|_evidence_key" src/evoeventmem/consolidation.py` 无结果 |
| T8-A3 | retrieval.py 不再定义 `_jaccard`/`_cosine_similarity`/`_unique_evidence` | `rg "_jaccard\|_cosine_similarity\|_unique_evidence" src/evoeventmem/retrieval.py` 无结果 |
| T8-A4 | vector_baseline.py 不再定义 `_cosine_similarity` | `rg "_cosine_similarity" benchmarks/vector_baseline.py` 无结果 |
| T8-A5 | 所有现有测试全绿 | `uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py` |
| T8-A6 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |

---

## T9: 拆分 300 行 prompt 函数

### 问题

`src/evoeventmem/extraction.py` 的 `_build_llm_prompt()`（lines 959-1256，297 行）不可测试不可维护。包含 5 个逻辑段：

1. JSON schema 定义（lines 961-1013，52 行）
2. fact_slot_rules（lines 1014-1044，30 行）
3. fact_slot_examples（lines 1046-1202，156 行）
4. constraints（lines 1203-1240，37 行）
5. turns/observations 序列化 + 组装（lines 1242-1256，14 行）

### 方案

拆分为独立的内部函数，每个函数负责一个逻辑段：

```python
def _build_schema_section() -> dict[str, Any]: ...
def _build_fact_slot_rules() -> list[str]: ...
def _build_fact_slot_examples() -> list[dict[str, Any]]: ...
def _build_constraints(require_turn_evidence: bool) -> list[str]: ...
def _build_turns_payload(turns: Sequence[...]) -> list[dict[str, str]]: ...
def _build_observations_payload(observations: Sequence[...]) -> list[dict[str, str]]: ...
```

`_build_llm_prompt()` 变为组装函数，调用上述子函数。函数签名和返回值不变。

### 需要修改的文件

- `src/evoeventmem/extraction.py`：拆分 `_build_llm_prompt()`
- `tests/extraction/`：新增 section 级别测试（可选但推荐）

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T9-A1 | `_build_llm_prompt()` 行数 < 50 | `wc -l src/evoeventmem/extraction.py` 中函数体行数 |
| T9-A2 | 每个子函数可独立测试 | 新增测试或手动验证 |
| T9-A3 | 函数签名和返回值不变 | `rg "def _build_llm_prompt" src/evoeventmem/extraction.py` 签名一致 |
| T9-A4 | 所有现有提取测试全绿 | `uv run pytest tests/extraction -q` |
| T9-A5 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |

---

## T10: 修复 Any 类型滥用

### 问题

3 处关键边界使用 `Any` 类型，丧失类型安全：

| 位置 | 当前签名 | 问题 |
|------|----------|------|
| `extraction.py:152` | `from_normalized_record(cls, record: Any, *, user_id: str)` | `record` 实际是 `NormalizedRecord`（或其子类），应使用具体类型或 Protocol |
| `extraction.py:461` | `candidate: Any = None` | `json.loads` 返回值，应为 `dict[str, Any] \| list[Any] \| str \| ...` |
| `service_factory.py:68` | `repository: Any` | 应为 `AsyncInMemoryRepository \| AsyncPostgresMemoryRepository` |

### 方案

1. **`extraction.py:152`**：查看 `NormalizedRecord` 的实际结构，将 `record: Any` 改为 `record: NormalizedRecord`（从 `benchmarks.common.normalization` 导入）
2. **`extraction.py:461`**：将 `candidate: Any = None` 改为 `candidate: dict[str, Any] | list[Any] | None = None`（或使用 `json.JSONDecoder` 返回类型）
3. **`service_factory.py:68`**：查看 `build_async_repository` 的返回类型，使用精确的联合类型

### 需要修改的文件

- `src/evoeventmem/extraction.py`：2 处类型修复
- `src/evoeventmem/infra/service_factory.py`：1 处类型修复

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T10-A1 | extraction.py:152 不再使用 `Any` | `rg "record: Any" src/evoeventmem/extraction.py` 无结果 |
| T10-A2 | extraction.py:461 不再使用 `Any` | 同上 |
| T10-A3 | service_factory.py:68 不再使用 `Any` | `rg "repository: Any" src/evoeventmem/infra/service_factory.py` 无结果 |
| T10-A4 | mypy 通过 | `uv run mypy src` |
| T10-A5 | 所有现有测试全绿 | `uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py` |
| T10-A6 | ruff 全绿 | `uv run ruff check .` |

---

## T11: 清理死 regex 和 dead code

### 问题

`src/evoeventmem/router.py` 中：
- `_MONTH_RE`（line 745）：定义但未使用
- `_TO_YEAR_RE`（line 750）：定义但未使用，且与 `_YEAR_RE`（line 743）pattern 完全相同

`src/evoeventmem/retrieval.py` 中：
- `RetrievalRequest`（line 270）：定义、导出、测试，但 **生产代码零引用**
- `RetrievalResult`（line 281）：定义、导出、测试，但 **生产代码零引用**
- 实际使用的是 `QEMRRetrievalResult`

### 方案

1. **删除 `_MONTH_RE` 和 `_TO_YEAR_RE`**：从 `router.py` 中移除这两个未使用的正则
2. **保留 `RetrievalRequest` 和 `RetrievalResult`**：虽然生产代码未使用，但它们有测试覆盖（`test_retrieval_contract.py`），且属于公开 API 的一部分（`__all__` 导出）。删除会破坏 API 契约。标记为 `# noqa: F811` 或在文档中注明即可。

### 需要修改的文件

- `src/evoeventmem/router.py`：删除 `_MONTH_RE`、`_TO_YEAR_RE`

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T11-A1 | `_MONTH_RE` 不存在于 router.py | `rg "_MONTH_RE" src/evoeventmem/router.py` 无结果 |
| T11-A2 | `_TO_YEAR_RE` 不存在于 router.py | `rg "_TO_YEAR_RE" src/evoeventmem/router.py` 无结果 |
| T11-A3 | 所有现有 router 测试全绿 | `uv run pytest tests/retrieval/test_query_router.py -q` |
| T11-A4 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |
| T11-A5 | 不影响其他 temporal constraint 类型 | 现有测试覆盖 |

---

## T12: 补充 facade 测试

### 问题

`RetrievalService` 无独立 facade 测试。虽然 `test_qemr.py` 间接测试了它，但缺少：
- 服务级接口契约测试
- 错误处理路径测试
- 端到端 happy path 测试

`QueryRouterService` 已有基础测试（`test_query_router.py:220-239`），但可以补充边界场景。

### 方案

新建 `tests/retrieval/test_retrieval_service.py`，包含：

1. **happy path 测试**：构造 QEMRRetrievalResult mock，验证 `RetrievalService.search()` 返回正确结果
2. **空结果测试**：验证无匹配时返回空列表
3. **错误传播测试**：验证底层异常正确传播
4. **多 query 测试**：验证服务可以处理多个独立查询

### 需要修改的文件

- `tests/retrieval/test_retrieval_service.py`：新建

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T12-A1 | `test_retrieval_service.py` 存在 | 文件检查 |
| T12-A2 | 至少 4 个测试用例 | `uv run pytest tests/retrieval/test_retrieval_service.py -v` |
| T12-A3 | 覆盖 happy path + error path | 测试审查 |
| T12-A4 | 所有新测试全绿 | `uv run pytest tests/retrieval/test_retrieval_service.py -q` |
| T12-A5 | 不影响现有测试 | `uv run pytest tests/retrieval -q` |
| T12-A6 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |

---

## 并行执行策略

```
时间线：
  T0: 启动 2 个 subagent 并行执行 T8 + T11
      同时启动 1 个 subagent 执行 T12（与 T8/T11 无冲突）
  T1: T8 完成 → 启动 T9 + T10 并行
      T11 完成 → 等待 T8
  T2: T9 + T10 各自完成实现 → 各自运行验收
  T3: 主 agent 汇总 3 轮验收结果
```

### Subagent 1（T8: 共享工具 + T9: prompt 拆分）

任务（顺序执行）：
1. 读 `src/evoeventmem/consolidation.py`、`src/evoeventmem/retrieval.py`、`benchmarks/vector_baseline.py`
2. 实现 T8：新建 `math_utils.py`，修改 3 个文件的 import
3. 读 `src/evoeventmem/extraction.py`
4. 实现 T9：拆分 `_build_llm_prompt()` 为子函数
5. 运行 3 轮验收（每轮：ruff + mypy + pytest + 代码审查）
6. 输出验收报告

### Subagent 2（T11: 死代码清理 + T10: Any 类型修复 + T12: facade 测试）

任务（顺序执行）：
1. 读 `src/evoeventmem/router.py`
2. 实现 T11：删除 `_MONTH_RE`、`_TO_YEAR_RE`
3. 读 `src/evoeventmem/extraction.py`、`src/evoeventmem/infra/service_factory.py`
4. 实现 T10：修复 3 处 Any 类型
5. 读 `src/evoeventmem/retrieval.py`（RetrievalService 接口）
6. 实现 T12：新建 facade 测试
7. 运行 3 轮验收（每轮：ruff + mypy + pytest + 代码审查）
8. 输出验收报告

---

## 验收流程

每轮验收由主 agent 派出独立 subagent 执行。

### 第 1 轮验收（功能正确性）

对每个任务：
1. 运行 `uv run ruff check . && uv run mypy src && uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py`
2. 代码审查：变更是否符合任务要求，重构是否保持行为等价
3. T8 重点：`math_utils.py` 的函数签名和返回值是否与原始实现一致
4. T9 重点：`_build_llm_prompt()` 输出是否与拆分前完全相同
5. 输出：PASS / FAIL + 问题清单

### 第 2 轮验收（回归 + 安全性）

对每个任务：
1. 运行全量测试 `uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py`
2. 检查是否有意外的副作用（跨任务影响）
3. T8 重点：import 变更是否导致循环导入
4. T10 重点：类型修复是否引入新的 mypy 错误
5. T11 重点：删除 regex 是否影响其他模块
6. 输出：PASS / FAIL + 问题清单

### 第 3 轮验收（集成 + 文档）

对每个任务：
1. T8：验证 `math_utils.py` 可独立导入，无副作用
2. T9：验证拆分后的子函数可独立测试
3. T12：验证 facade 测试覆盖核心路径
4. 最终 ruff + mypy + pytest
5. 输出：PASS / FAIL + 问题清单

---

## 最终交付物

每个任务完成后，主 agent 汇总：
1. 3 轮验收报告
2. 变更文件清单
3. 测试结果
4. 遗留风险

全部 5 个任务 PASS 后，Phase 3 完成。

---

## 命令参考

```bash
# 环境准备
uv sync --extra dev

# Lint + Typecheck
uv run ruff check .
uv run mypy src

# 全量测试
uv run pytest -q --ignore=tests/benchmarks/test_locomo_run.py

# 特定测试
uv run pytest tests/extraction -q                # T9
uv run pytest tests/retrieval/test_query_router.py -q  # T11
uv run pytest tests/retrieval/test_retrieval_service.py -q  # T12
uv run pytest tests/consolidation -q              # T8 回归

# 死代码检查
rg "_MONTH_RE\|_TO_YEAR_RE" src/evoeventmem/router.py
rg "_jaccard\|_cosine_similarity\|_unique_evidence" src/evoeventmem/consolidation.py src/evoeventmem/retrieval.py benchmarks/vector_baseline.py
rg "record: Any\|repository: Any" src/evoeventmem/extraction.py src/evoeventmem/infra/service_factory.py

# Smoke
uv run python -m evoeventmem.cli smoke
```
