# Phase 2 重要改进执行提示词

## 背景

Phase 1 阻塞项已全部完成（T1 选择性 SUPERSEDE、T2 API 认证、T3 CI/CD），3 轮验收全部 PASS。

Phase 2 包含 4 个任务（T4–T7），来源：`docs/S8_POST_PLAN.md` Phase 2 节。

| 任务 | 类型 | 依赖 | 并行组 |
|------|------|------|--------|
| T4: MDE 计算显式化 | 仅文档 | Phase 1 完成 | A（可与 B 并行） |
| T5: full_context baseline 验证 | 代码 | Phase 1 完成 | B（T6 前置） |
| T6: router 死代码修复 | 代码 | T5 完成 | B（T5 后置） |
| T7: 多重比较校正 | 仅文档 | T4 完成 | A（T4 后置） |

**并行策略**：T4 和 T5 可并行启动；T7 等 T4 完成后执行；T6 等 T5 完成后执行。

## 执行约束

- **并行度**：最多 2 个 subagent 并行（T4+T5 并行，然后 T7+T6 并行）
- **验收轮次**：每轮验收由独立 subagent 执行，发现问题必须在当前轮修复
- **验收通过标准**：3 轮全部 PASS 才算完成
- **不允许跨任务修改**：T4/T7 不改代码，T5/T6 不改文档
- **测试必须全绿**：每轮验收前运行 `uv run ruff check . && uv run mypy src && uv run pytest -q`

---

## T4: MDE 计算显式化

### 问题

`docs/S8-PREREGISTRATION.md` §3 已声明 MDE 数值（n=42 ~±0.21, n=100 ~±0.14），但缺少：
1. MDE 公式（paired-proportion test）
2. 计算假设（baseline proportion, paired correlation, alpha=0.05 two-sided, power=0.8）
3. 对 C+/C/D 决策稳定性的 caveats

### 方案

修改 `docs/S8-PREREGISTRATION.md` §3，补充：

1. **MDE 公式**：
   ```
   MDE ≈ (z_{α/2} + z_β) × √(2 × p̄ × (1-p̄) / n)
   其中 p̄ = (p1 + p2) / 2, paired correlation ρ considered via VIF
   ```

2. **假设列表**：
   - α = 0.05 two-sided
   - Power = 0.80
   - Expected baseline proportion (vector_rag EM ≈ 0.56)
   - Paired design correlation ρ ≈ 0.3 (same questions, different methods)
   - Minimum n for target MDE

3. **Caveats**：
   - n=100 MDE=0.14 远大于 observed delta (0.00–0.08)，C+/C/D 判断不稳定
   - n=42 home-court MDE=0.21 更大，主场结论仅为方向性
   - 500q 可缩窄 MDE 到 ±0.06–0.10，但仍需 CI 支撑

### 需要修改的文件

- `docs/S8-PREREGISTRATION.md`：§3 补充公式、假设、caveats

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T4-A1 | MDE 公式正确且可复现 | 公式推导与数值一致 |
| T4-A2 | 假设列表完整 | 覆盖 α, power, baseline, correlation, n |
| T4-A3 | Caveats 明确 | 指出 n=100/n=42 的决策不稳定性 |
| T4-A4 | 不修改任何 Python 代码 | `git diff --name-only` 仅含 docs/ |
| T4-A5 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |

---

## T5: full_context baseline 验证

### 问题

`full_context` baseline 在所有 6 个 category 上 EM=0.000，token_f1=0.000。两种可能：
1. **Reader 能力问题**：信息在 context 中但 reader 找不到
2. **信息缺失**：context 被截断，信息不在其中

当前 `FullContextBuilder`（`benchmarks/context_baselines.py:86-128`）使用固定 `max_input_tokens` 预算截断，且使用硬编码 stub model（`DeterministicFixtureChatModel`）而非真实 LLM。

### 方案

1. **新增 unlimited-budget 变体**：在 `FullContextBuilder` 中添加 `unlimited: bool = False` 参数，当 `unlimited=True` 时跳过 token 预算截断
2. **新增配置**：在 benchmark configs 中添加 `full_context_unlimited` 方法
3. **对比分析**：unlimited vs limited 的 EM 和 token_f1 差异可区分"信息缺失"vs"reader 能力"

### 需要修改的文件

- `benchmarks/context_baselines.py`：`FullContextBuilder` 添加 `unlimited` 参数
- `benchmarks/common/strategies.py`（或等价注册点）：注册 `full_context_unlimited` 策略
- `tests/benchmarks/`：新增测试验证 unlimited 模式行为

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T5-A1 | unlimited 变体不截断 context | 测试验证 output token count > limited |
| T5-A2 | limited 变体行为不变 | 现有测试全绿 |
| T5-A3 | token_f1 在 unlimited 下 > 0（若信息在完整 context 中） | 运行 smoke 测试 |
| T5-A4 | 新增策略可配置 | 配置文件可指定 `full_context_unlimited` |
| T5-A5 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |
| T5-A6 | 新增测试全绿 | `uv run pytest tests/benchmarks -q` |

---

## T6: router 死代码修复

### 问题

`src/evoeventmem/router.py` 的 `_detect_temporal_constraint()`（lines 770-895）存在语义排序 bug：

**当前顺序**（简化）：
```
1. BEFORE/AFTER + year → return（lines 802-825）
2. DURATION check（line 840）
3. SEQUENCE check（line 848）
4. RELATIVE check（line 856）
5. FIRST/LAST check（lines 871-885）
6. temporal_relation_without_date → return NONE（line 887）
```

**Bug**：当查询匹配 `_BEFORE_RE`（如 "What happened before the concert?"）但不含 year 时：
- 步骤 1 进入 `if _BEFORE_RE.search()` 块，内部 `if year is not None` 失败，fall through
- 步骤 2-5 可能被查询中的 stray words（"first", "last", "how long" 等）误匹配
- 步骤 6 才返回 `NONE`，但此时可能已被错误分类为 EARLIEST/LATEST/SEQUENCE/DURATION

### 方案

重排检查顺序：
```
1. BEFORE/AFTER + year → return（不变）
2. BEFORE/AFTER without year → return NONE（从 line 887 移到这里）
3. DURATION check（line 840）
4. SEQUENCE check（line 848）
5. RELATIVE check（line 856）
6. FIRST/LAST check（lines 871-885）
```

### 需要修改的文件

- `src/evoeventmem/router.py`：`_detect_temporal_constraint()` 重排 lines 887-894 到 lines 826 之后
- `tests/retrieval/test_query_router.py`：新增边界测试

### 新增测试

```python
def test_before_without_year_returns_none():
    """'What happened before the concert?' → TemporalOperator.NONE"""

def test_after_without_year_returns_none():
    """'What happened after the meeting?' → TemporalOperator.NONE"""

def test_before_with_year_returns_before():
    """'What happened before 2023?' → TemporalOperator.BEFORE"""

def test_before_with_stray_first_not_misclassified():
    """'What was the first thing before the concert?' → NONE, not EARLIEST"""
```

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T6-A1 | 现有 router 测试全绿 | `uv run pytest tests/retrieval/test_query_router.py -q` |
| T6-A2 | 新增边界测试全绿 | 同上 |
| T6-A3 | "before X" without year → NONE | 测试验证 |
| T6-A4 | "after X" without year → NONE | 测试验证 |
| T6-A5 | stray "first"/"last" 不误分类 | 测试验证 |
| T6-A6 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |
| T6-A7 | 不影响其他 temporal constraint 类型 | 现有测试覆盖 |

---

## T7: 多重比较校正

### 问题

`docs/S8-STRATIFIED_VALIDATION_REPORT.md` 的 per-category EM 表（6 categories × N methods）未做多重比较校正。6 个 category 的单独比较增加了假阳性风险。

### 方案

在报告中添加 caveat，标注 per-category 比较为探索性（exploratory）。不修改数据或重跑实验。

具体修改：
1. 在 per-category EM 表下方添加脚注：
   > **Multiple-comparison caveat**: Per-category EM values are exploratory.
   > With 6 categories × N method pairs, uncorrected comparisons inflate
   > false-positive risk. Category-level results should be interpreted as
   > hypothesis-generating, not confirmatory. A Holm-corrected analysis
   > across all category-method pairs is recommended before drawing
   > category-specific conclusions.

2. 在 §4 (paired permutation test) 添加说明：
   > Category-level p-values are not corrected for multiplicity. The
   > primary comparison (full vs vector_rag at the dataset level) is the
   > pre-registered confirmatory test.

### 需要修改的文件

- `docs/S8-STRATIFIED_VALIDATION_REPORT.md`：添加 multiple-comparison caveat

### 验收标准

| # | 标准 | 验证 |
|---|---|---|
| T7-A1 | Caveat 明确标注探索性 | 文档审查 |
| T7-A2 | 不修改任何 Python 代码 | `git diff --name-only` 仅含 docs/ |
| T7-A3 | 不修改任何数据或实验结果 | 无 runs/ 变更 |
| T7-A4 | ruff/mypy 全绿 | `uv run ruff check . && uv run mypy src` |

---

## 并行执行策略

```
时间线：
  T0: 启动 2 个 subagent 并行执行 T4 + T5
  T1: T4 完成 → 启动 T7
      T5 完成 → 启动 T6
  T2: T7 + T6 各自完成实现 → 各自运行验收
  T3: 主 agent 汇总 3 轮验收结果
```

### Subagent 1（T4: MDE 显式化 + T7: 多重比较）

任务（顺序执行）：
1. 读 `docs/S8-PREREGISTRATION.md`
2. 实现 T4：补充 MDE 公式、假设、caveats
3. 读 `docs/S8-STRATIFIED_VALIDATION_REPORT.md`
4. 实现 T7：添加 multiple-comparison caveat
5. 运行 3 轮验收（每轮：ruff + mypy + 文档审查）
6. 输出验收报告

### Subagent 2（T5: full_context baseline + T6: router 死代码）

任务（顺序执行）：
1. 读 `benchmarks/context_baselines.py`
2. 实现 T5：添加 unlimited-budget 变体
3. 读 `src/evoeventmem/router.py`
4. 实现 T6：重排 `_detect_temporal_constraint` 检查顺序
5. 运行 3 轮验收（每轮：ruff + mypy + pytest + 代码审查）
6. 输出验收报告

---

## 验收流程

每轮验收由主 agent 派出独立 subagent 执行。

### 第 1 轮验收（功能正确性）

对每个任务：
1. 运行 `uv run ruff check . && uv run mypy src && uv run pytest -q`
2. 代码/文档审查：变更是否符合任务要求
3. 边界测试：是否有遗漏的边界情况
4. 输出：PASS / FAIL + 问题清单

### 第 2 轮验收（回归 + 安全性）

对每个任务：
1. 运行全量测试 `uv run pytest -q`
2. 检查是否有意外的副作用（跨任务影响）
3. T6 重点：router 修改是否影响其他 temporal constraint 类型
4. T5 重点：unlimited 变体是否影响现有 baseline 行为
5. 输出：PASS / FAIL + 问题清单

### 第 3 轮验收（集成 + 文档）

对每个任务：
1. T4/T7：文档格式、公式正确性、caveat 措辞
2. T5/T6：集成测试 — 现有 benchmark 路径不受影响
3. 最终 ruff + mypy + pytest
4. 输出：PASS / FAIL + 问题清单

---

## 最终交付物

每个任务完成后，主 agent 汇总：
1. 3 轮验收报告
2. 变更文件清单
3. 测试结果
4. 遗留风险

全部 4 个任务 PASS 后，Phase 2 完成。

---

## 命令参考

```bash
# 环境准备
uv sync --extra dev

# Lint + Typecheck
uv run ruff check .
uv run mypy src

# 全量测试
uv run pytest -q

# 特定测试
uv run pytest tests/retrieval/test_query_router.py -q    # T6
uv run pytest tests/benchmarks -q                         # T5
uv run pytest tests/consolidation -q                      # 回归

# Smoke
uv run python -m evoeventmem.cli smoke

# 文档变更检查
git diff --name-only    # 确认 T4/T7 仅修改 docs/
```
