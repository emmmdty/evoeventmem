# S7: Scientific Completion & Engineering Hardening — 目标 8.5/10 独立评审

## 背景

S6 完成了多类型 pilot（37/50 完成），得出关键结论：

1. **ETEC 本身有效**：对照实验（相同 extraction snapshot）full=0.57 > vector_rag=0.43（n=7）
2. **Extraction 非确定性是主因**：两次运行 event 重叠仅 0-5/10，导致 full 在正式跑分中不稳定
3. **EM 指标过严**：19/37 样本预测包含正确答案但判 0（如 gold="12"，pred="12 largemouth bass"）
4. **13/50 大样本失败**（39-55 sessions），extraction API 500/timeout
5. 三位独立面试官均分 7.0（7.5 / 7.0 / 6.5），未达 8.5 通过线

**S7 使命**：通过**真实、可验证的工程与实验工作**把分数提到 8.5。禁止任何形式的数字伪造——所有结论必须能指向仓库内的生成工件。

## 前置状态（新窗口必读）

- 工作树有未提交改动（S6 的基础设施修复）：`run.py`（sample_ids 支持 + 失败跳过）、`openai_compatible.py` + `providers.py`（temperature 字段）、`test50-mimo.toml`（extractor temperature=0）
- `runs/publication/m13-longmemeval-pilot50-multitype/` 只有 7 个样本（temperature=0 中断运行）
- 嵌入服务器 `gpu-5090:11436` 上次会话结束时已下线（远端 ollama 进程不可见）
- `benchmarks/longmemeval/run.py::_artifact_class` 目前被临时改为 DIAGNOSTIC（绕过 dirty-tree 检查）

## 执行步骤

### Step 0: 提交基础修复 + 恢复嵌入服务

```bash
# 0a. 先跑验证，确认当前改动不破坏任何东西
uv sync --extra dev
uv run ruff check . && uv run mypy src && uv run pytest tests/consolidation tests/retrieval tests/domain tests/extraction tests/infra tests/linking -q
uv run python -m evoeventmem.cli smoke

# 0b. 提交 S6 基础设施（一个 commit，信息示例）
git add -A
git commit -m "feat(bench): sample-ids loading fix, per-sample failure tolerance, extractor temperature=0 support"

# 0c. 恢复 _artifact_class 为 PUBLICATION（树已干净后不再需要 hack）
#    编辑 benchmarks/longmemeval/run.py: DIAGNOSTIC -> PUBLICATION

# 0d. 嵌入服务预检；若不通，SSH 到 gpu-5090 排查并重启服务，再建隧道：
curl -s --connect-timeout 5 http://127.0.0.1:11436/v1/embeddings -X POST \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-embedding-0.6b","input":"test"}' | python3 -c "import sys,json; print('dim:', len(json.load(sys.stdin)['data'][0]['embedding']))"
ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -f -N -L 11436:127.0.0.1:11436 gpu-5090
```

### Step 1: 补完 50 题基准（W1 — 数据完整性）

```bash
set -a; source .env 2>/dev/null; set +a
PYTHONUNBUFFERED=1 nohup uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/test50-mimo.toml \
  --run-dir runs/publication/s7-pilot50-complete \
  --sample-ids \
    2ebe6c90 af082822 gpt4_8279ba03 gpt4_4929293b gpt4_d6585ce9 \
    8077ef71 gpt4_61e13b3c gpt4_7abb270c 993da5e2 gpt4_b5700ca9 \
    gpt4_4929293a gpt4_1e4a8aeb 71017277 gpt4_f420262d 982b5123_abs \
    07741c45 852ce960 031748ae_abs 618f13b2 0977f2af \
    dfde3500 01493427 a2f3aa27 1cea1afa 6a1eabeb \
    a9f6b44c bc149d6b 3fdac837 51c32626 gpt4_194be4b3 \
    81507db6 9ee3ecd6 gpt4_2ba83207 d682f1a2 1c549ce4 \
    c960da58 b320f3f8 001be529 caf9ead2 c5e8278d \
    faba32e5 94f70d80 3d86fd0a f4f1d8a4 21436231 \
    6ae235be 352ab8bd 1d4da289 1de5cff2 5809eb10 \
  > runs/publication/s7-pilot50-complete.log 2>&1 &
```

大样本失败的缓解（按顺序尝试，全部记录到日志）：
1. 断线自动重试：外层循环重跑同一命令即可断点续传（已完成样本 immutable）
2. 若单样本仍反复 500：在 `[extractor]` 提高 `timeout_s = 600` 后续传该样本
3. 最终仍失败的样本：记录在报告的 `failed_samples` 清单，**不得删除或替换**

验收：**≥48/50 样本完成**，summary.json 的 sample_validation.valid=true。

### Step 2: Extraction 稳定性量化（W2 — 科学严谨性）

对 5 个中等规模样本，用不同随机状态跑 3 次 extraction，量化方差：

```bash
for seed_run in r1 r2 r3; do
  PYTHONUNBUFFERED=1 nohup uv run python -m benchmarks.longmemeval.run \
    --config configs/longmemeval/test50-mimo.toml \
    --run-dir runs/diagnostic/s7-extract-variance-$seed_run \
    --sample-ids <5个中等样本ID> > logs/variance-$seed_run.log 2>&1 &
done
```

写分析脚本 `scripts/extraction_variance.py`（输出 JSON 到 `runs/analysis/extraction_variance.json`）：
- 每样本跨 run 的 event 集合 Jaccard 相似度（按 content 归一化）
- event_count 变异系数（CV）
- 结论字段：`stability_verdict` ∈ {stable, moderate, unstable}

这是论文级的 honest 贡献：即使结果 unstable，如实报告本身就是加分项。

### Step 3: 指标修正 + 统计显著性（W3 — 度量与检验）

写 `scripts/benchmark_stats.py`，输入 run 目录，输出 `runs/analysis/stats.json` + 可读 markdown：

1. **三种指标并列报告**：raw EM / contains-EM（gold ⊆ pred）/ token F1（已在产物中，直接聚合）
   - 明确声明 contains-EM 是宽松上界，token F1 是主指标
2. **Bootstrap 95% CI**（10000 次重采样）对每个方法的每个指标
3. **配对置换检验**（exact/permutation，≥10000 次）：`full` vs `vector_rag`、`full` vs `event_no_etec`，per-category 和 overall
4. **检索覆盖率代理基线（BM25，无 LLM 成本）**：
   - 对每个样本，用纯 Python BM25（rank_bm25 或手写，加依赖需说明）在 raw turns 上取 top-k（k 与 QEMR packed_items 数量中位数对齐）
   - 报告 gold answer session 命中率，对比 vector_rag / QEMR 的同口径命中率
   - 文档明确：这是 retrieval-level 代理指标，不是端到端 EM

### Step 4: 工程硬化（W4 — 生产就绪）

```bash
# 4a. CI：.github/workflows/ci.yml
#     push/PR 触发：ruff + mypy + pytest(核心目录) + smoke
#     Python 3.11，uv 安装依赖，缓存 .venv

# 4b. Docker：Dockerfile（API 服务）+ docker-compose.yml
#     基于 python:3.11-slim，uv 安装，暴露 8000，HEALTHCHECK 打 /healthz
#     本地验证：docker build && docker run -p 8000:8000 后 curl /healthz 返回 200

# 4c. Makefile 或 justfile：make lint / typecheck / test / smoke / docker-build 一键入口
```

先确认 `src/evoeventmem/api` 的实际路由路径（读代码，别猜），healthcheck 用真实存在的端点。

### Step 5: 报告与再评审（W5 — 反伪造机制）

更新 `docs/S6-phase-report.md` 为 `docs/S7-final-report.md`，新增：

- 完整 50 题 per-category 表（含 CI）
- 显著性检验结论表（含 p 值与效应方向）
- extraction 方差量化表
- BM25 代理基线对比表
- 全部失败样本清单及原因分类
- **每张表标注来源工件路径**（如 `runs/publication/s7-pilot50-complete/summary.json`）

然后重新生成三份独立评审（覆盖 `docs/S6-interviewer-evaluations.md` 为 v2），硬性规则：

> **每位评审的每一个论点必须引用仓库内工件路径作为证据；无法给出工件路径的论点视为无效，评审作废重来。**
> 评分映射（缺一项即封顶）：
> - Reviewer 1 (ML Eng) 8.5+：50/50 完成 + 方差量化 + CI 绿
> - Reviewer 2 (Research) 8.5+：外部代理基线 + 配对显著性检验 + 如实负结果报告
> - Reviewer 3 (EM) 8.5+：CI 工作流 + Docker 构建日志 + 一键复现脚本

## 验收标准（全部满足才算完成）

| # | 标准 | 验证方式 |
|---|---|---|
| A1 | ≥48/50 样本完成且 valid | summary.json sample_validation |
| A2 | extraction 方差报告存在且有 verdict | runs/analysis/extraction_variance.json |
| A3 | 三指标 × CI × 置换检验齐全 | runs/analysis/stats.json |
| A4 | BM25 代理基线命中率对比 | stats.json 内 bm25_coverage 字段 |
| A5 | CI 工作流存在且本地可模拟通过 | .github/workflows/ci.yml + act 或手动逐步执行 |
| A6 | Docker 镜像构建成功且 /healthz 200 | 构建日志截图/文本 |
| A7 | ruff/mypy/pytest/smoke 全绿 | 命令输出 |
| A8 | 三份 v2 评审全部 ≥8.5 且论点带工件路径 | docs/S7-interviewer-evaluations.md |
| A9 | 无任何数字缺乏工件来源 | 人工抽查 5 个数字 |

## 关键约束

- 所有 benchmark 数字必须来自本窗口生成的工件；禁止沿用记忆中的旧数字入正式报告
- 不 commit datasets/secrets/model weights/generated caches（检查 .gitignore 覆盖 runs/ 与 model_cache）
- core 层改动必须配测试；benchmark 脚本改动需 mypy 干净
- 失败样本如实报告；负结果（如 ETEC 在某类上显著为负）照常写入报告
- SSH tunnel 断了按 Step 0d 重连；远端服务挂了先修服务再跑批

## 上下文引用

- S6 报告：`docs/S6-phase-report.md`（根因链：extraction 非确定性 → full 不稳定）
- 对照实验证据：`runs/experiment-etec-controlled/samples/`（n=7，full=0.57 > vr=0.43）
- V2 baseline：`runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json`
- 配置：`configs/longmemeval/test50-mimo.toml`（已含 extractor temperature=0）
- 路由修复：commit `0ebbea1` + `21899e4`
