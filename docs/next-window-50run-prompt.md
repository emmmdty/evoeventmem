# 下一个窗口执行提示词：50-run 验收（MiMo V2.5）

## 背景

EvoEventMem O09 任务的 8→9 推进已完成（**注**：8→9 自续属 self-awarded 升分；S0 整改保留独立审计的 8/10 结论，见 `docs/8of10_AUDIT.md` Part 6 disclaimer 与 `docs/REMEDIATION_SPEC.md` S0）。本窗口目标：**跑 50-run 验证 MiMo V2.5 端到端管道可行性**。500-run 暂不执行。

### 已完成的前置工作

- `.env` 已切换到 MiMo V2.5（`OPENAI_API_KEY=sk-2Kki…`，`OPENAI_MODEL=mimo-v2.5`，网关 `https://opencode.ai/zen/go/v1`）
- `configs/longmemeval/test50-mimo.toml` 已创建（50 题、6 方法、mimo-v2.5、timeout 提取 180s/reader 120s）
- `scripts/run50-parallel.sh` 已创建（10 批 × 5 题并行脚本）
- MiMo V2.5 可行性已验证：
  - 端点可用（cost=0，无 429 配额阻断）
  - 123K 提取上下文可行（43s/次，113K prompt tokens）
  - 10x 并行可行（10/10 成功，6.5s 墙钟）
  - 推理无法关闭（网关不转发 `thinking` 参数，每次 +70-100 reasoning tokens，功能正常）
  - **Cloudflare 要求 `User-Agent: opencode/1.0`**（代码已设，Python urllib 默认 UA 会被 403 拦截）

### 注意事项

- 前 50 题无 KU（knowledge-update）题目（数据集按类型排序）→ M1/M3/M5 机制指标不适用，本 run 只验证管道可行性和一致性指标（provenance/budget/failure-attribution）
- 不修改 `src/` 代码，不跑 500-run
- embedding 隧道需保持 UP（`ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090`）

## 执行步骤

### Step 1: 环境检查

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
set -a; source .env; set +a

# 检查 embedding 隧道
nc -z 127.0.0.1 11436 && echo "tunnel UP" || { echo "tunnel DOWN"; ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090; sleep 2; }

# 检查 MiMo 可用性
curl -s -m 10 https://opencode.ai/zen/go/v1/chat/completions \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "Content-Type: application/json" \
    -H "User-Agent: opencode/1.0" \
    -d '{"model":"mimo-v2.5","messages":[{"role":"user","content":"OK"}],"max_tokens":50}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('Model:', 'OK' if 'choices' in d else d.get('error',{}).get('message','UNKNOWN'))"

# 确认配置
cat configs/longmemeval/test50-mimo.toml | head -5
```

### Step 2: 并行跑 50-run

```bash
# 方式 A：用并行脚本（10 批 × 5 题，预计 ~12-15 min）
bash scripts/run50-parallel.sh

# 方式 B：如脚本失败或 run 目录已存在，手动 resume
# uv run python -m benchmarks.longmemeval.run \
#     --config configs/longmemeval/test50-mimo.toml \
#     --resume-dir runs/publication/m13-longmemeval-test50-mimo
```

**如果 run 目录已存在**（重新执行时）：所有批次都用 `--resume-dir`（不用 `--run-dir`）：
```bash
RUN_DIR=runs/publication/m13-longmemeval-test50-mimo
# 分 10 批并行
for batch in 1 2 3 4 5 6 7 8 9 10; do
    # 每批 5 个 sample-ids（从 50 个中取对应批次）
    IDS=$(uv run python -c "
import json; from pathlib import Path
data = json.loads(Path('data/raw/longmemeval/longmemeval_s_cleaned.json').read_bytes())
ids = [r['question_id'] for r in data[:50]]
start = $((batch-1))*5
print(' '.join(ids[start:start+5]))
")
    uv run python -m benchmarks.longmemeval.run \
        --config configs/longmemeval/test50-mimo.toml \
        --resume-dir $RUN_DIR \
        --sample-ids $IDS > /tmp/batch$batch.log 2>&1 &
done
wait
# 合并
uv run python -m benchmarks.longmemeval.run \
    --config configs/longmemeval/test50-mimo.toml \
    --resume-dir $RUN_DIR
```

### Step 3: 验证 50-run

```bash
set -a; source .env; set +a
RUN_DIR=runs/publication/m13-longmemeval-test50-mimo

# 3a. Finalized 检查
ls $RUN_DIR/finalized/FINALIZED.json && echo "FINALIZED OK" || echo "NOT FINALIZED"
uv run python -c "from benchmarks.common.artifacts import load_finalized; from pathlib import Path; load_finalized(Path('$RUN_DIR')); print('load_finalized: OK')"

# 3b. 样本数检查（应为 50）
ls $RUN_DIR/samples/*.json | grep -v extraction_snapshot | wc -l  # 应该是 50

# 3c. retrieval.jsonl 行数（应为 50 题 × 6 方法 = 300）
wc -l $RUN_DIR/retrieval.jsonl

# 3d. 一致性检查（加入 50-run 到现有 4 个 finalized run）
uv run python -m benchmarks.mechanism.consistency \
    --source-run $RUN_DIR \
    --source-run runs/publication/longmemeval-test20-r2 \
    --source-run runs/publication/longmemeval-test20-6m \
    --source-run runs/publication/longmemeval-test20-ms \
    --source-run runs/recheck/m13-longmemeval-test20-20260814T195333507448Z \
    --review-sheet runs/review/longmemeval-r2.reviewed.jsonl \
    --locomo-report runs/main/report \
    --out runs/mechanism/consistency/consistency-with-test50

# 3e. 验证命令
uv run pytest tests/mechanism -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

### Step 4: 检查结果

```bash
# 检查一致性
uv run python -c "
import json
from pathlib import Path
r = json.loads(Path('runs/mechanism/consistency/consistency-with-test50.json').read_text())
for c in r['runs']:
    pc = c['provenance_coverage']
    ea = c['etec_actions']
    acts = ea.get('actions') if ea.get('status') == 'ok' else 'NA'
    print(f'{c[\"run_id\"]}: prov={pc[\"numerator\"]}/{pc[\"denominator\"]}={pc[\"point_estimate\"]:.3f} etec={acts}')
print()
print('provenance overlap:', r['cross_run']['provenance_coverage']['wilson_ci_pairwise_overlap'])
"

# 检查 ETEC actions（SUPERSEDE 应为 0）
uv run python -c "
import json
from pathlib import Path
r = json.loads(Path('runs/mechanism/consistency/consistency-with-test50.json').read_text())
for c in r['runs']:
    if 'test50' in c['run_id']:
        ea = c['etec_actions']
        if ea.get('status') == 'ok':
            print(f'{c[\"run_id\"]}: ADD={ea[\"actions\"][\"ADD\"]} MERGE={ea[\"actions\"][\"MERGE\"]} SUPERSEDE={ea[\"actions\"][\"SUPERSEDE\"]} REJECT={ea[\"actions\"][\"REJECT\"]}')
        else:
            print(f'{c[\"run_id\"]}: ETEC actions NA ({ea[\"status\"]})')
"
```

## 预期结果

| 指标 | 预期 | 说明 |
|---|---|---|
| FINALIZED | 存在 + load_finalized 通过 | 管道端到端可行 |
| 样本数 | 50 | 全部完成 |
| retrieval.jsonl 行数 | 300 (50×6) | 6 方法全覆盖 |
| provenance | ~100% | 新管线 span 定位不漂移 |
| budget saturation | ~100% | 四方法满装 budget |
| SUPERSEDE | 0 | R1 屏障在 50 题复现（无 KU 题，全是 single-session/non-KU） |
| ETEC actions | ADD only + some MERGE | 同管线，无 fact_slot → SUPERSEDE 不可达 |
| pytest/ruff/mypy/smoke | 全绿 | 代码质量不退化 |

## 预计时间

- 10 批并行：~12-15 min（提取 43s×5/batch + reader 5s×6×5/batch + 并行开销）
- 顺序跑（fallback）：~60 min
- 验证：~5 min

## 不做什么

- 不跑 500-run（下下个窗口）
- 不改 src/ 代码
- 不改 budget/prompt/threshold
- 不跑 M1/M3/M5（50 题无 KU，gold 不适用）
- 不擅自 commit

## 故障排查

| 问题 | 解决 |
|---|---|
| Cloudflare 403 (code 1010) | 检查 User-Agent: opencode/1.0 是否设置（代码已设；如手动 curl 必须加 `-H "User-Agent: opencode/1.0"`）|
| embedding 403/超时 | 检查隧道 `nc -z 127.0.0.1 11436`，重连 `ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090` |
| 批次失败 | 查看 `/tmp/batchN.log`，用 `--resume-dir --sample-ids <失败批次>` 重跑 |
| 合并后样本数 <50 | 检查哪些 sample 文件缺失，用 `--resume-dir --sample-ids <缺失ID>` 补跑 |
| FINALIZED 不存在 | 可能需要手动 finalize：查看 `run.py` 是否自动 finalize，或调 `benchmarks.common.artifacts.finalize_run` |
| reasoning tokens 消耗大 | 正常——MiMo 是推理模型，网关无法关闭，每次 +70-100 reasoning tokens |

## 完成后报告

1. 50-run 是否 FINALIZED（是/否 + 原因）
2. 样本数 + retrieval.jsonl 行数
3. 一致性指标（provenance/budget/SUPERSEDE）是否与预期一致
4. 验证命令结果（pytest/ruff/mypy/smoke）
5. 异常/风险（如有）
6. commit 决策（询问用户）
