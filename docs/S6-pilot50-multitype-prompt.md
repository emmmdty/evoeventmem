# S6: Router Fix Benchmark Validation — 多 question_type Pilot Run

## 背景

EvoEventMem 项目已完成 S0-S5 整改闭环（分支 C 中间路线）。S3 诊断发现 router 误路由是 QEMR 失效的 primary 原因（`docs/QEMR_FAILURE_DIAGNOSIS.md` §1），上一个窗口完成了路由器修复（commit `0ebbea1` + `21899e4`，50q 切片路由准确率 4%→60%）。

**核心问题**：之前的 50 题 benchmark 100% 是 `single-session-user` 类型——这恰恰是 ETEC 没有 surface 的子集。要验证 ETEC/QEMR 的价值，需要在包含 `temporal-reasoning` 和 `knowledge-update` 的多类型切片上跑 benchmark。

**本次任务**：从 500 题 LongMemEval 中抽样 50 题（覆盖 5 种 question_type），跑一个 pilot benchmark，用 per-category EM 验证 ETEC 在正确子集上的效果。

## 数据分布

LongMemEval 500 题分布：
```
temporal-reasoning: 133 (27%)  ← ETEC 应该有 surface
knowledge-update:    78 (16%)  ← ETEC 应该有 surface
multi-session:      133 (27%)  ← 部分 ETEC surface
single-session-user:  70 (14%)  ← ETEC 无 surface（之前 50q 全是这个）
single-session-assistant: 56 (11%)
single-session-preference: 30 (6%)
```

## 50 题分层抽样方案

按 question_type 分层抽样（加强 temporal/knowledge-update 权重）：

| question_type | 目标数 | 可用总数 | ETEC surface |
|---|---|---|---|
| temporal-reasoning | 15 | 133 | ✅ 有（时间排序/比较） |
| knowledge-update | 10 | 78 | ✅ 有（旧值 vs 新值） |
| multi-session | 10 | 133 | ⚠️ 部分有 |
| single-session-user | 10 | 70 | ❌ 无（对照组） |
| single-session-assistant | 5 | 56 | ❌ 无 |

**验证逻辑**：
- 如果 ETEC 在 temporal-reasoning + knowledge-update 子集上 `full` > `event_no_etec`（ETEC 有正向效果），说明 ETEC 在正确的子集上有效
- 如果 ETEC 在 single-session-user 子集上 `full` ≤ `event_no_etec`（ETEC 无效果或有害），符合预期
- 总体 `full` EM ≥ `vector_rag` EM 是理想目标

## 前置条件

- ✅ 路由器修复已提交（`0ebbea1` + `21899e4`）
- ✅ mimo-v2.5 API 可用（已测试，状态 200）
- ✅ 嵌入服务器可用（`127.0.0.1:11436`，SSH tunnel 到 gpu-5090）
- ✅ run.py 支持 `--sample-ids` 参数（line 306）
- ✅ 50 题分层抽样已确定（seed=42 可复现）

## 执行步骤

### Step 1: 确认 API 和嵌入服务器可用

```bash
# 确认 mimo-v2.5 API
set -a; source .env 2>/dev/null; set +a
uv run python -c "
import os, urllib.request, json
url = os.environ.get('OPENAI_BASE_URL', 'https://opencode.ai/zen/go/v1')
key = os.environ['OPENAI_API_KEY']
data = json.dumps({'model': 'mimo-v2.5', 'messages': [{'role': 'user', 'content': 'Say hi'}], 'max_tokens': 10}).encode()
req = urllib.request.Request(f'{url}/chat/completions', data=data, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'User-Agent': 'opencode/1.0'})
resp = urllib.request.urlopen(req, timeout=30)
print('API OK:', resp.status)
"

# 确认嵌入服务器（如果 SSH tunnel 断了，重建）
curl -s --connect-timeout 5 http://127.0.0.1:11436/v1/embeddings -X POST -H "Content-Type: application/json" -d '{"model":"qwen3-embedding-0.6b","input":"test"}' | python3 -c "import sys,json; r=json.load(sys.stdin); print('Embedding OK, dim:', len(r['data'][0]['embedding']))"
```

### Step 2: 跑 50 题 pilot benchmark

```bash
set -a; source .env 2>/dev/null; set +a

uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/test50-mimo.toml \
  --run-dir runs/publication/m13-longmemeval-pilot50-multitype \
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
    6ae235be 352ab8bd 1d4da289 1de5cff2 5809eb10
```

### Step 3: 分析 per-category EM

```bash
uv run python -c "
import json
from collections import defaultdict

summary = json.loads(open('runs/publication/m13-longmemeval-pilot50-multitype/summary.json').read())

# 按 question_type 分组
# 需要从 sample 结果中读取 question_type
import os
results = {}
for f in os.listdir('runs/publication/m13-longmemeval-pilot50-multitype/samples'):
    if f.endswith('.json') and 'extraction_snapshot' not in f:
        data = json.loads(open(f'runs/publication/m13-longmemeval-pilot50-multitype/samples/{f}').read())
        qtype = data.get('question_type', 'unknown')
        if qtype not in results:
            results[qtype] = {'full': [], 'event_no_etec': [], 'etec': [], 'vector_rag': []}
        for method in ['full', 'event_no_etec', 'etec', 'vector_rag']:
            if method in data.get('methods', {}):
                results[qtype][method].append(data['methods'][method]['exact_match'])

print('=== Per-category EM ===')
print(f'{\"question_type\":<25} {\"vector_rag\":>10} {\"full\":>10} {\"event_no_etec\":>10} {\"etec\":>10} {\"ETEC gap\":>10} {\"n\":>5}')
for qtype in ['temporal-reasoning', 'knowledge-update', 'multi-session', 'single-session-user', 'single-session-assistant']:
    if qtype in results:
        r = results[qtype]
        vr = sum(r['vector_rag'])/len(r['vector_rag']) if r['vector_rag'] else 0
        full = sum(r['full'])/len(r['full']) if r['full'] else 0
        ene = sum(r['event_no_etec'])/len(r['event_no_etec']) if r['event_no_etec'] else 0
        etec = sum(r['etec'])/len(r['etec']) if r['etec'] else 0
        gap = full - ene
        n = len(r['full'])
        print(f'{qtype:<25} {vr:>10.2f} {full:>10.2f} {ene:>10.2f} {etec:>10.2f} {gap:>+10.2f} {n:>5}')
"
```

## 验收标准

### 关键指标（per-category）
- **temporal-reasoning**: `full` > `event_no_etec`（ETEC 有正向效果）或至少 `full` ≥ `event_no_etec`（ETEC 中性）
- **knowledge-update**: `full` > `event_no_etec`（ETEC 有正向效果）或至少 `full` ≥ `event_no_etec`（ETEC 中性）
- **single-session-user**: `full` ≤ `event_no_etec`（ETEC 无效果或有害，符合预期）
- **总体**: `full` EM ≥ `vector_rag` EM 是理想目标

### 过拟合风险控制
- 分层抽样是预注册的（seed=42）
- per-category EM 是主要报告指标，不是 cherry-picked
- 所有 5 种 question_type 都报告，包括 ETEC 预期无效的类型

## 关键约束

- **不改 src/evoeventmem/ 代码**——路由器修复已在 commit `0ebbea1` + `21899e4` 中
- **不改路由器规则**——只跑 benchmark 验证
- **extraction snapshot 由 v2 prompt 生成**——与 50q v2 run 使用相同的提取管线
- **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；不 commit secrets
- **嵌入服务器 SSH tunnel**：如果断了，运行 `ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090`

## 上下文引用

- S3 根因诊断：`docs/QEMR_FAILURE_DIAGNOSIS.md` §1（router 38%）+ §2（weights sound）+ §4（M2 74% tie）
- 路由器修复：commit `0ebbea1`（router.py）+ `21899e4`（router.py + config）
- v2 baseline：`runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json`
- 配置文件：`configs/longmemeval/test50-mimo.toml`（mimo-v2.5，同模型同预算）
