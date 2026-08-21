# Router Fix Benchmark Validation — New Window Execution Prompt

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`）已完成 S0-S5 整改闭环（分支 C 中间路线）。上一个窗口完成了路由器修复（commit `0ebbea1` + `21899e4`），验证了50q 切片路由准确率从 4% 提升到 60%。但 Phase 2（benchmark 重跑验证 EM 指标）被 **run.py 基础设施限制**阻塞：

**核心问题**：`benchmarks/longmemeval/run.py` 的 `_process_sample` 在发现 sample 结果文件已存在时直接跳过（line 443-445），不检查 extraction snapshot 是否匹配当前代码。当上一个窗口尝试"删除 result 文件 + symlink v2 embedding cache + resume"时，run 找到了旧的 result 文件（基于错误的重新提取 chunk）直接 finalize，导致 `vector_rag` EM 从 0.56 降到 0.52（无效对比）。

**你需要做的事**：修改 run.py 支持 `--retrieval-only` 模式（跳过 extraction，复用已有 snapshot，只跑 retrieval+reader），然后用这个模式跑一个干净的 benchmark 对比。

## 项目结构关键文件

```
src/evoeventmem/router.py          # 路由器（已修改：_STRONG_FACT_RE + _KNOWLEDGE_UPDATE_RE）
benchmarks/longmemeval/run.py      # benchmark runner（需要修改：加 --retrieval-only）
benchmarks/common/providers.py     # model provider resolution
configs/longmemeval/test50-mimo-v2-routerfix.toml  # 路由器修复 benchmark 配置
runs/publication/m13-longmemeval-test50-mimo-v2-factslot/  # v2 baseline（extraction snapshot 源）
runs/publication/m13-longmemeval-test50-mimo-v2-routerfix/  # 路由器修复 run dir（需要重建）
```

## 方法论

### 1. 修改 run.py 加 `--retrieval-only` 模式

**设计**：
- 新增 CLI 参数 `--retrieval-only`（类似已有的 `--extraction-only`）
- 当 `--retrieval-only` 时：检查 extraction snapshot 是否存在（从指定的 `--source-run` 复制），如果存在则跳过 extraction，只跑 materialization + retrieval + reader
- 需要一个 `--source-run` 参数指定 extraction snapshot 来源目录
- 核心修改点：`_process_sample` 函数（line 433-506），当 `retrieval_only=True` 时：
  1. 从 `source_run` 复制 extraction snapshot（如果 run_dir 中不存在）
  2. 跳过 `extract_event_snapshot` 调用
  3. 直接进入 materialization + retrieval + reader

**修改位置**：
- `benchmarks/longmemeval/run.py`：`main()` 函数（加 CLI 参数）、`run_experiment()`（加 `retrieval_only` 参数）、`_process_sample()`（加 `retrieval_only` 分支）

### 2. 用 `--retrieval-only` 跑 benchmark

**步骤**：
1. 清理 `runs/publication/m13-longmemeval-test50-mimo-v2-routerfix/`（删除旧 result 文件 + manifest）
2. 从 v2 baseline 复制 extraction snapshots
3. 用 `--retrieval-only --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot` 跑
4. 对比结果：v2 baseline EM vs 路由器修复 EM

### 3. 验收标准

**必须达到的指标**：
- `vector_rag` EM = 0.56（与 v2 baseline 完全一致——路由器修复不影响 FIXED_VECTOR 检索）
- `full` EM ≥ 0.48（与 v2 baseline 持平或更好——路由器修复应该帮助 QEMR 方法）
- `event_no_etec` EM ≥ 0.48（与 v2 baseline 持平或更好）
- ETEC gap（`full` - `event_no_etec`）≤ 0.00（ETEC 中性或更好）

**如果 `vector_rag` ≠ 0.56**：说明 extraction snapshot 不一致或 embedding cache 有问题，对比无效。必须排查根因。

## 执行步骤

### Step 1: 探索现有代码

1. 读 `benchmarks/longmemeval/run.py` 完整代码，理解 `_process_sample` 的流程
2. 读 `benchmarks/common/providers.py`，理解 model bundle 构建
3. 读 `configs/longmemeval/test50-mimo-v2-factslot/summary.json`，确认 v2 baseline 数字
4. 读 `src/evoeventmem/router.py`，确认路由器修复内容

### Step 2: 修改 run.py 加 `--retrieval-only`

1. 在 `main()` 函数加 `--retrieval-only` 和 `--source-run` CLI 参数
2. 在 `run_experiment()` 加 `retrieval_only` 和 `source_run` 参数
3. 在 `_process_sample()` 加 `retrieval_only` 分支：
   - 从 `source_run` 复制 extraction snapshot（如果 run_dir 中不存在）
   - 跳过 `extract_event_snapshot`
   - 直接进入 materialization + retrieval + reader
4. 确保 manifest 正确创建（不触发 "publication run requires clean Git tree" 错误）

### Step 3: 跑 benchmark 对比

1. 清理旧 run dir：`rm -rf runs/publication/m13-longmemeval-test50-mimo-v2-routerfix/`
2. 跑：`uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/test50-mimo-v2-routerfix.toml --run-dir runs/publication/m13-longmemeval-test50-mimo-v2-routerfix --retrieval-only --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot`
3. 检查结果：对比 summary.json 中的 EM 数字

### Step 4: 验收

1. 检查 `vector_rag` EM = 0.56（必须与 v2 baseline 一致）
2. 检查 `full` EM ≥ 0.48（路由器修复应帮助或持平）
3. 检查 extraction snapshot 一致性（diff routerfix vs v2）
4. 运行全套回归测试（pytest/ruff/mypy/smoke）
5. 提交修改

## 关键约束

- **不改 src/evoeventmem/ 代码**——只改 benchmarks/ 和 configs/
- **不改路由器规则**——router.py 的修改已经在 commit `0ebbea1` + `21899e4` 中
- **extraction snapshot 必须与 v2 baseline 一致**——这是对比有效的前提
- **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；不 commit secrets

## 上下文引用

- S3 根因诊断：`docs/QEMR_FAILURE_DIAGNOSIS.md` §1（router 38%）+ §2（weights sound）+ §4（M2 74% tie）
- S4a+S5 定稿：`docs/REMEDIATION_FINAL_REPORT.md`（分支 C 中间路线）
- 路由器修复 commit：`0ebbea1`（router.py）+ `21899e4`（router.py + config + fixture）
- v2 baseline：`runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json`
