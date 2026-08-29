# Stage 2 执行提示词：50 题重跑 + ETEC 可达性诊断（v3 prompt 落地后的第一次经验测量）

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `ab5ba1a`）刚完成 Stage 1c：在 S1a schema 落地之上加了 `_EventDraft.fact_slot` required + `"none"` sentinel + `_extract_single` retry on missing fact_slot + salvage fallback，prompt 从 v2 升到 v3（`event-extraction.v3`）。S1c 独立审查 **CONDITIONAL PASS**（`docs/STAGE1c_REVIEW.md`）——12/13 验收绿、唯一未决项是 **"none" sentinel 占比 39.7% (411/1036) > spec 的 20% prompt-health 门槛**。

S1c 的关键诊断证据（写进 `docs/STAGE1c_REVIEW.md`）：

- **5 题真实数据测量（v3 prompt + contrast example）**：1036 events，**有效 fact_slot 率 = 60.3% (625/1036)**（排除 "none" sentinel，spec line 253 "分母里排除"）；per-sample 有效率 = `1e043500=55.9% / 58bf7951=50.0% / 118b2229=63.8% / e47becba=65.9% / 51a45a95=67.7%`（5/5 ≥ 50%；S1b 是 3/5，1e043500=33.3% 单点拖低均值，已消除）。
- **"none" sentinel 率 = 39.7% (411/1036)** —— 5/5 样本全超 20%（`58bf7951` 最差 50%）。LLM 在"User enjoys X" / "User plans to do X" / "User has been doing Y" 这类偏好/活动句上**仍过打 "none" sentinel**，把真实 user fact 误判为非事实。第二次 prompt tweak（contrast pair example）只把 sentinel 从 42.6% 推到 39.7%（+2.9pp），未达 <20% 目标。
- **可达性 PASS（非 xfail）**：107 对 within-sample event 在真实 v3 LLM 输出上满足全部四重 gate（107,619 对枚举中）；`blocked_by_multi_valued=0`——R3 在 5 题切片未阻塞（multi_valued=0%，S1a/S1b/S1c 都没 emit `multi_valued`，所以四重 gate 真正 reachable，不是 R3 bypass）。S1b 是 22 four-gate pairs；S1c 提升到 107。
- **valid_until ≈ 0**：`valid_to=1.6%`（17/1036），多数 fact 是 start-only 单 event 形式（`valid_until=None`）。S1b 是 0.3% (2/666)；S1c 微升到 1.6%——仍偏低，S2 在 50 题上测量真实状态变化分布。
- **multi_valued=0%**：S1a/S1b/S1c 三阶段都没 emit；R3 仍未碰也不在 S2 scope（AGENTS.md 反 fishing 规则）。
- **v3 prompt 真进了 LLM 调用**：1036/1036 events 的 `metadata.extractor_prompt_version == "event-extraction.v3"`。

整改 spec v1.1（`docs/REMEDIATION_SPEC.md` Stage 2，lines 228-283）+ S1c fallback（spec lines 276-283 + `docs/STAGE1c_REVIEW.md` §Dedicated section）明确路由：

> **S1c 触发 spec fallback 替代路径**：重新评估 50% 门槛 + sentinel 门槛本身在 50 题上是否合理（5 题噪声大，1e043500 单点拖低均值已消除，但 5 题仍小；50 题上均值可能自然稳定）。把决策路由到 S2：S2 在 50 题上同时测 v3 prompt 的 fact_slot 有效率 + sentinel 率 + R3 阻塞率 + SUPERSEDE 计数 + EM，若 fact_slot ≥ 50% + sentinel < 20% → 继续 S3 测是否提升准确率；若 sentinel 仍 > 20% → 重新评估 50% / 20% 门槛本身，写进 `docs/STAGE2_REVIEW.md` 作为 S3 / S5 的输入。

S2 是 spec fallback 路由出来的关键证据窗口：**第一次在 50 题 statistically meaningful 的样本上经验性测量 v3 prompt 的真实效果 + ETEC SUPERSEDE 真实计数**。这是决定 thesis 翻盘还是 pivot 的关键证据。

**scope 边界（明确声明，不藏着）**：S2 **只跑 50 题 + 诊断 + 写报告**，不改 extraction/consolidation/retrieval/router；不修 R3；不调阈值；不跑 500 题；不修 prompt（若 sentinel > 20%，**不**在 S2 反复调 prompt 凑数——那是 fishing；记录事实，路由到 S3 / S5 决策）。S2 是 S1c spec fallback 的直接落地。

**为什么是这一阶段**：S1c 在 5 题上证明 v3 prompt 让 fact_slot 有效率 ≥ 50%（60.3%），但 sentinel 率 > 20%。S2 在 50 题上回答四个关键问题：
1. **fact_slot 有效率在 50 题上稳定 ≥ 50% 吗？**（S1c 是 60.3%；50 题均值是否自然稳定 ≥ 50%？）
2. **sentinel 率在 50 题上稳定 < 20% 吗？**（S1c 是 39.7%；50 题均值是否自然稳定 < 20%？若是 → 5 题噪声是问题；若否 → prompt 真有缺陷，路由到 S3/S5 决策）
3. **SUPERSEDE 在 50 题上从 0 变成多少？**（v1 是 0；S1b 5 题切片是 22 four-gate pairs；S1c 5 题切片是 107；50 题真实计数的第一次测量）
4. **v2 `full` EM 是否翻盘 vs v1 0.46？**（预注册的 negative-result 框架，不预先声明期望）

**S2 不论结果都是赢**：若 SUPERSEDE > 0 + EM 翻盘 → thesis 翻盘，进 S3 测 QEMR；若 SUPERSEDE > 0 + EM 不翻盘 → ETEC 有操作面但 QEMR 失效，进 S3 测 QEMR；若 SUPERSEDE = 0 → pivot 到 negative-result 论文（S5 path A），**不修 R3**（AGENTS.md 反 fishing）。

### 已完成的前置工作

- S0 完成（commit `b60b38d`，诚信止血）。
- S1a 完成（commit `162183c`，schema + prompt v2 落地，独立审查 PASS）。
- S1b 完成（commit `00b3dc6`，5 题 smoke + stats + reachability 全套落地，独立审查 CONDITIONAL PASS）。
- S1c 完成（commit `ab5ba1a`，required fact_slot + retry + salvage + v3 prompt + contrast pair example，独立审查 CONDITIONAL PASS）。
- S1c 5 题 snapshot 已生成于 `runs/s1c/smoke5/`（gitignored），作为 S2 的"50 题对照"——S2 跑完后应用相同 5 题、相同 mimo provider 的子集做交叉对比（验证 50 题均值是否被 5 题噪声拖偏）。
- v3 prompt + salvage + retry 已落地 `src/evoeventmem/extraction.py`；`_EventDraft.fact_slot` required；`_FACT_SLOT_NONE_SENTINEL = "none"`；`LLMEventExtractor.PROMPT_VERSION = "event-extraction.v3"`。
- S2 复用基础设施：
  - `configs/longmemeval/test50-mimo.toml`（50 题 mimo provider 配置，`sample_limit = 50`，6 methods）
  - `scripts/run50-parallel.sh`（10 批并行启动器，每批 5 题，含 embedding tunnel check + model availability check）
  - `benchmarks/mechanism/extraction_smoke.py`（stats 脚本，`uv run python -m benchmarks.mechanism.extraction_smoke <run_dir>`——S2 可直接复用，计算 fact_slot / valid_from / valid_until 非空率）
  - `tests/consolidation/test_etec_real_data_reachability.py`（四重 gate 真实数据可达性测试，已通过 `EEM_S1B_SNAPSHOT_PATH` 环境变量参数化——S2 跑完后用 `EEM_S1B_SNAPSHOT_PATH=runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json pytest ...` 测可达性）
- mimo provider 已验证可用（`OPENAI_API_KEY` 在 `.env`；网络可达 `https://opencode.ai/zen/go/v1`；S1c 跑 5 题 ~1 小时）。
- embedding tunnel：本地 `127.0.0.1:11436` 指向 `qwen3-embedding-0.6b`；若 tunnel 断开用 `ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090` 重建。

### 关键约束（违反即 spec 失败）

- **只做 S2，不开始 S3/S4/S5**——S2 完成并 commit + 独立审查通过后才允许进入下一阶段。
- **不修 R3（`multi_valued` 过打）**——S2 的 scope 边界，AGENTS.md 反 fishing 规则约束。`consolidation.py:876` 的 `multi_valued` 短路保留；`_EventDraft` 仍**不**含 `multi_valued` 字段；`extraction.py` 仍不 emit `multi_valued`。若 SUPERSEDE = 0 且 R3 阻塞率高，记录事实，**不修**——pivot 决策在 S5。
- **不调阈值**——`supersede_contradiction_min=0.7` 不动；不动任何 weight profile；不动 retrieval budget；不动 router 规则。
- **不改 prompt**——若 50 题 sentinel 率仍 > 20%，**不**在 S2 反复调 prompt 凑数（spec line 345 + AGENTS.md 反 fishing）；记录事实，路由到 S3 / S5 决策。S2 是测量阶段，不是 prompt 工程阶段。
- **不跑 500 题 / 新 benchmark**——S2 只跑 50 题；不跑 LoCoMo（S5 决定是否补跑）；不跑 reader ablation（S3 的事）。
- **不擅自 commit**——完成后报告变更清单，询问用户是否 commit + push。commit message 模板：`feat(s2): 50-question v3 rerun + ETEC reachability diagnosis + EM comparison`。
- **不预先声明期望**——预注册的 negative-result 框架（`METHODOLOGY_CHANGE.md`）要求不 bias 结果解读。**禁止**在 run 跑完前在 `docs/EVALUATION.md` 或 commit message 写"S2 期望 SUPERSEDE > X"。
- **不声称 SUPERSEDE > 0 经验上**（即使 50 题测出 SUPERSEDE > 0）——SUPERSEDE > 0 是必要条件，**不充分**证明 thesis 翻盘；S3 还要测 QEMR + M2 stale-judge；S5 才决定 paper framing。**S2 只能说"50 题上测到 SUPERSEDE = N"**，不能说"thesis 翻盘"或"ETEC 有效"。
- **禁止跨模型对比**——v2 `full` EM 只能 vs v1 `full` EM (mimo-v2.5)，**禁止** vs 24 题 deepseek-v4-flash run（已停服、不可复现、AGENTS.md 禁止不等模型下 benchmark 对比）。
- **不破坏既有契约**：`uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q` + `uv run ruff check .` + `uv run mypy src` + `uv run python -m evoeventmem.cli smoke` 必须全绿（S2 不改 src/，所以应原样绿；若哪条红了，是 infra 问题不是代码问题）。
- **不删 S1a/S1b/S1c 落地**——`_EventDraft` 的 `fact_slot` required + `fact_value` / `valid_from` / `valid_until` 4 字段保留；v3 prompt + salvage + retry 保留；reachability test 的 `EEM_S1B_SNAPSHOT_PATH` 参数化保留。
- **不调 stats 脚本计算逻辑**——若 fact_slot 有效率 < 50% 或 sentinel 率 > 20%，如实记录，走 fallback 路径。

## 执行步骤

### Step 0: S4b 前置检查（vector_rag 延迟修复）

spec line 232 + line 393-435 明确：**S4b（vector_rag 延迟修复）必须先于 S2**——否则 S2 的 vector_rag 延迟数据与 v1 不可比，S5 的 v1-vs-v2 对比无效。S4b 是 1 天小任务。

**S4b 是否已落地**：

```bash
# 检查 S4b 是否在 git 历史里
git log --oneline --all | grep -i "s4b\|vector_rag.*laten\|embedding.*batch" | head -5
# 期望: 若有 S4b commit, 这里会显示; 若空, S4b 未做

# 检查 vector_rag 延迟代码是否改过
git log --oneline -- benchmarks/vector_baseline.py src/evoeventmem/infra/async_embedding.py | head -5
```

**若 S4b 已落地**：直接进 Step 1，跑 50 题用全 10 批并行（vector_rag 延迟 < 30s，run 总时间 ~1.5 天）。

**若 S4b 未落地**——两条路：

- **路径 A（推荐）**：先做 S4b（1 天），再跑 S2。S4b spec 在 `docs/REMEDIATION_SPEC.md` lines 393-435。S4b 验收：5 题 vector_rag p50 search latency < 30,000 ms；`tests/infra tests/retrieval` 全绿；provenance coverage 仍 100%。S4b 完成后进 Step 1。
- **路径 B（fallback）**：S2 直接跑，但 **并行度降到 5**（`scripts/run50-parallel.sh` 改 `N_BATCHES=5` 或 `BATCH_SIZE=10`），vector_rag 延迟仍病态（~437s/题），run 总时间可能拖到 2-3 天。**显式声明**：在 `docs/STAGE2_REVIEW.md` 标注 "v2 vector_rag 延迟仍病态（~437s/题），与 v1 不可比；S5 v1-vs-v2 latency 对比无效；EM 对比仍有效（latency 不影响 EM）"。S5 决策时此声明是 known limitation。

**关键判断**：若 S4b 未做且时间紧（窗口 < 2 天），选路径 B；若时间充裕（窗口 ≥ 3 天），选路径 A。**任何路径下 EM 对比都有效**（latency 不影响 EM，只影响 efficiency 指标）。

### Step 1: 侦察（read-only，不改文件）

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -5   # 确认 HEAD 是 ab5ba1a (S1c commit) 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）

# 确认 spec + S1c 审查 + 真实数据都存在
test -f docs/REMEDIATION_SPEC.md && echo "spec OK"
test -f docs/S1c-execution-prompt.md && echo "s1c prompt OK"
test -f docs/STAGE1c_REVIEW.md && echo "s1c review OK"
test -f runs/s1c/smoke5/extraction_snapshot.json && echo "s1c baseline snapshot OK"

# 确认 v3 prompt + required fact_slot + salvage 仍落地
grep -n "PROMPT_VERSION = " src/evoeventmem/extraction.py | head -3
# 期望: rule.v1 (RuleExtractor) + event-extraction.v3 (LLMEventExtractor)

grep -n "_FACT_SLOT_NONE_SENTINEL = " src/evoeventmem/extraction.py
# 期望: 定义在 _EventDraft 之前

grep -n "fact_slot: str = Field" src/evoeventmem/extraction.py
# 期望: fact_slot: str = Field(min_length=1, max_length=128) (required)

grep -n "_salvage_missing_fact_slot\|attempt >= 2" src/evoeventmem/extraction.py | head -5
# 期望: salvage 函数定义 + _extract_attempt 内 attempt >= 2 时调用

# 确认 R3 仍未修
grep -n "multi_valued" src/evoeventmem/extraction.py
# 期望: 仅在 code comment (lines ~1438-1439); 无 _EventDraft.multi_valued 字段

# 确认 test50 config 完整
cat configs/longmemeval/test50-mimo.toml | head -15
# 期望: sample_limit = 50, methods = [no_memory, full_context, vector_rag, event_no_etec, etec, full], mimo-v2.5 reader+extractor

# 确认 embedding tunnel 通
nc -z 127.0.0.1 11436 && echo "embedding tunnel UP" || echo "embedding tunnel DOWN - fix: ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090"

# 确认 mimo provider 可用
set -a; source .env; set +a
curl -s -m 10 https://opencode.ai/zen/go/v1/chat/completions \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "Content-Type: application/json" \
    -H "User-Agent: opencode/1.0" \
    -d '{"model":"mimo-v2.5","messages":[{"role":"user","content":"OK"}],"max_tokens":50}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('mimo:', 'OK' if 'choices' in d else d.get('error',{}).get('message','UNKNOWN'))"

# 确认 scripts/run50-parallel.sh 存在且可执行
ls -la scripts/run50-parallel.sh
head -20 scripts/run50-parallel.sh
```

预期发现：
- HEAD = `ab5ba1a` 或后继；工作区 clean。
- v3 prompt + required fact_slot + salvage 全部落地。
- R3 未碰（multi_valued 仅在 code comment）。
- test50 config 完整（50 题、6 methods、mimo-v2.5）。
- embedding tunnel UP；mimo provider OK。

**关键判断**：侦察完后，若 mimo provider 或 embedding tunnel 不可用，S2 标 BLOCKED，不强行用 fake 凑数（违反"真实数据 50 题"目的）。

### Step 2: 跑 50 题 v2 run（复用 S0 的 parallel launcher）

```bash
# 不复用旧 cache（extraction schema 变了，v1 chat cache 全失效；embedding cache 可复用）
# 新 run dir（spec line 236 + 248）
mkdir -p runs/publication/m13-longmemeval-test50-mimo-v2-factslot

# 跑 10 批并行（参照 scripts/run50-parallel.sh，但每批用独立 --run-dir 避免 manifest drift）
# 若 S4b 已做: 全 10 批并行, ~1.5 天
# 若 S4b 未做 (路径 B): 降到 5 批并行, ~2-3 天
set -a; source .env; set +a

# 选项 1（推荐）: 用 scripts/run50-parallel.sh（已封装好 10 批并行 + tunnel check + model check）
# 但需修改 RUN_DIR 指向 v2-factslot 目录; 改法: 复制脚本并改 RUN_DIR 变量
cp scripts/run50-parallel.sh scripts/run50-parallel-v2-factslot.sh
# 编辑 scripts/run50-parallel-v2-factslot.sh: RUN_DIR="runs/publication/m13-longmemeval-test50-mimo-v2-factslot"
# （若 S4b 未做, 改 N_BATCHES=5 或 BATCH_SIZE=10 降并行度）
bash scripts/run50-parallel-v2-factslot.sh 2>&1 | tee runs/publication/m13-longmemeval-test50-mimo-v2-factslot/run.log

# 选项 2（手动批）: 跑 batch 1 建 run dir, 再并行 batch 2-10
# (spec line 236 + 2026-08-18 执行记录的"每批独立 --run-dir 避免 manifest drift"模式)
# 详见 scripts/run50-parallel.sh 内的循环逻辑
```

**预期产物**（均在 `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`，gitignored）：
- `samples/<sample_id>.json` × 50（per-sample 含 extraction_snapshot + ingestion.etec.actions + retrieval + reader predictions）
- `samples/<sample_id>.extraction_snapshot.json` × 50（per-sample extraction only）
- `extraction_snapshot.json`（combined，50 题）
- `retrieval.jsonl`（50 题 × 4 method = 200 行；no_memory + full_context 不产 retrieval）
- `predictions.jsonl` × 6 method-subdir
- `manifest.json`、`summary.json`、`run.log`
- `finalized/FINALIZED.json`（最终化标记）
- 每个 event 的 `metadata.extractor_prompt_version == "event-extraction.v3"`（确认 v3 prompt 真进了 50 题 LLM 调用）

**失败处理**：
- HTTP 429 / 网络超时 / "Remote end closed" → 失败批次用 `--resume-dir <batch_run_dir>` 重跑（spec line 275）；不抛整个 run。
- embedding tunnel 断开 → `ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090` 重建；中断的批次用 `--resume-dir` 续跑。
- reader LLM 配额耗尽 → 已完成批次的 extraction + retrieval 仍有效；reader 配额恢复后续跑未完成批次。
- v3 prompt 让 LLM 完全不产 event（要求过严，S1c 未观察到此风险）→ 记录在 run.log，标 BLOCKED，建议回 S1c 微调（**不在 S2 反复调 prompt 凑数**）。

### Step 3: finalize + 合并

```bash
# finalize run（spec line 238 + 248）
uv run python -m benchmarks.longmemeval.run --resume-dir runs/publication/m13-longmemeval-test50-mimo-v2-factslot --finalize-only 2>&1 | tail -20
# 期望: 写入 finalized/FINALIZED.json
```

### Step 4: 诊断 ETEC 可达性 + EM 对比

```bash
# (a) 50 题 ETEC actions 分布（spec line 240）
uv run python -c "
import json
from pathlib import Path
from collections import Counter
actions = Counter()
run_dir = Path('runs/publication/m13-longmemeval-test50-mimo-v2-factslot')
for f in (run_dir / 'samples').glob('*.json'):
    if 'extraction_snapshot' in f.name: continue
    d = json.loads(f.read_text())
    a = d.get('ingestion', {}).get('etec', {}).get('actions')
    if a: actions.update(a)
print('ETEC actions:', dict(actions))
print('SUPERSEDE count:', actions.get('SUPERSEDE', 0))
"

# (b) fact_slot / valid_from / valid_until / sentinel 非空率（spec line 242 + S1c 复用 stats 脚本）
uv run python -m benchmarks.mechanism.extraction_smoke runs/publication/m13-longmemeval-test50-mimo-v2-factslot

# (c) sentinel 率 + 有效 fact_slot 率（spec line 253 排除 "none" 的有效率）
uv run python -c "
import json
from pathlib import Path
snaps = json.loads(Path('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json').read_bytes())
total_events = 0
total_sentinel = 0
total_real = 0
for s in snaps:
    sid = s.get('conversation_id')
    events = s.get('events', [])
    sentinel = sum(1 for e in events if isinstance(e, dict) and e.get('metadata', {}).get('fact_slot') == 'none')
    real = sum(1 for e in events if isinstance(e, dict) and e.get('metadata', {}).get('fact_slot') not in (None, 'none'))
    total_events += len(events)
    total_sentinel += sentinel
    total_real += real
    eff = real / len(events) * 100 if events else 0
    snt = sentinel / len(events) * 100 if events else 0
    print(f'{sid}: events={len(events)} real={real} ({eff:.1f}%) sentinel={sentinel} ({snt:.1f}%)')
eff_total = total_real / total_events * 100 if total_events else 0
snt_total = total_sentinel / total_events * 100 if total_events else 0
print(f'TOTAL: events={total_events} real={total_real} ({eff_total:.1f}%) sentinel={total_sentinel} ({snt_total:.1f}%)')
"

# (d) 可达性测试（spec line 240 + 复用 S1b/S1c 已参数化的 reachability test）
EEM_S1B_SNAPSHOT_PATH=runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json \
  uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s 2>&1 | tail -30

# (e) v1 vs v2 EM 对比表（spec line 244 + 253）
uv run python -c "
import json
from pathlib import Path
v1_run = Path('runs/publication/m13-longmemeval-test50-mimo')  # v1 baseline (S0 已 finalized)
v2_run = Path('runs/publication/m13-longmemeval-test50-mimo-v2-factslot')
for method in ['no_memory', 'full_context', 'vector_rag', 'event_no_etec', 'etec', 'full']:
    v1_summary = v1_run / 'summary.json'
    v2_summary = v2_run / 'summary.json'
    if v1_summary.exists() and v2_summary.exists():
        v1 = json.loads(v1_summary.read_text())
        v2 = json.loads(v2_summary.read_text())
        v1_em = v1.get('methods', {}).get(method, {}).get('exact_match', None)
        v2_em = v2.get('methods', {}).get(method, {}).get('exact_match', None)
        delta = (v2_em - v1_em) if (v1_em is not None and v2_em is not None) else None
        print(f'{method}: v1={v1_em} v2={v2_em} Δ={delta}')
"

# (f) replay/online 一致性复核（spec line 243, B4 修复）
# 在 v2 run 上重跑 benchmarks/mechanism/replay.py，对比 online ingestion.etec.actions 与 replay 重建值
uv run python -m benchmarks.mechanism.replay --run-dir runs/publication/m13-longmemeval-test50-mimo-v2-factslot 2>&1 | tee runs/publication/m13-longmemeval-test50-mimo-v2-factslot/replay_check.log | tail -30
# 若发散: 记录为已知 limitation, 不静默修复 (auditability 角度的真实证据)
```

**关键决策点**（spec line 241）：

- **SUPERSEDE = 0**：进一步统计 R3 阻塞率（多少对被 `multi_valued=True` 屏蔽——但 S1a/S1b/S1c 都没 emit multi_valued，所以 R3 阻塞率预期 = 0%；若仍 = 0 且 SUPERSEDE = 0 → 是 R1b `valid_until` 缺失或 interval 不重叠导致，写进 `docs/STAGE2_REVIEW.md`）。**若 SUPERSEDE = 0**：pivot 到 negative-result 论文（S5 path A），**不修 R3**（AGENTS.md 反 fishing）。
- **SUPERSEDE > 0**：进 S3 测 QEMR + M2 stale-judge（B2 修复——审计 `9of10_AUDIT.md:47-54` 的"结构性 null"防御在 SUPERSEDE>0 时立即失效）。
- **fact_slot 有效率 < 50% on 50 题**：5 题 S1c 是 60.3%；若 50 题 < 50%，回 S1c 修 prompt（但 S1c 已用 2 次 tweak；若第 3 次 tweak，需独立审查显式批准，否则走 spec fallback 重新评估 50% 门槛）。
- **sentinel 率 > 20% on 50 题**：5 题 S1c 是 39.7%；若 50 题 < 20% → 5 题噪声是问题，S1c prompt 没问题，进 S3；若 50 题 ≥ 20% → prompt 真有缺陷，写进 `docs/STAGE2_REVIEW.md` 作为 S3 / S5 输入，**不在 S2 反复调 prompt 凑数**。

### Step 5: 写 EM 对比表 + 诊断报告进 docs/EVALUATION.md

```bash
# 在 docs/EVALUATION.md 加新节: "## test50-mimo-v2-factslot (n=50, mimo-v2.5, v3 prompt, <date>)"
# 内容: v1 vs v2 EM 对比表 (6 methods) + ETEC actions 分布 + fact_slot/valid_from/sentinel 率 + 可达性测试结果
# 加注: "v1 vs v2 同模型 (mimo-v2.5), 可对比; v2 vs 24 题 deepseek run 跨模型, 禁止对比 (N8)"
# 加注: "若 S4b 未做 (路径 B): v2 vector_rag 延迟仍病态, 与 v1 不可比; EM 对比仍有效"
```

### Step 6: 全套回归（与 S1c 一致）

```bash
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

要求全绿（S2 不改 src/，所以应原样绿；若哪条红了，是 infra 问题不是代码问题，标 BLOCKED）。

### Step 7: 全局一致性扫描

```bash
# 确认 S2 没动 src/ (S2 是测量阶段, 不改代码)
git diff --stat
# 期望: 仅 docs/EVALUATION.md + 可能的 scripts/run50-parallel-v2-factslot.sh (副本)

# 确认未碰 src/evoeventmem/*
git diff src/evoeventmem/
# 期望: 空

# 确认 R3 仍未碰
grep -n "multi_valued" src/evoeventmem/extraction.py
# 期望: 仅 code comment, 无 _EventDraft.multi_valued

# 确认 PROMPT_VERSION 仍 v3 (S2 不动 prompt)
grep -n "PROMPT_VERSION = " src/evoeventmem/extraction.py | head -3
# 期望: rule.v1 + event-extraction.v3

# 确认 50 题 snapshot 真实生成 + v3 prompt 进了 LLM
uv run python -c "
import json
from pathlib import Path
snaps = json.loads(Path('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json').read_bytes())
print(f'total snapshots: {len(snaps)}')
total_events = 0
total_v3 = 0
for s in snaps:
    sid = s.get('conversation_id')
    events = s.get('events', [])
    v3 = sum(1 for e in events if isinstance(e, dict) and e.get('metadata', {}).get('extractor_prompt_version') == 'event-extraction.v3')
    total_events += len(events)
    total_v3 += v3
    print(f'{sid}: events={len(events)} v3={v3}')
print(f'TOTAL: events={total_events} v3={total_v3}')
"

# 确认 S2 没新增 overclaim
rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效' docs/EVALUATION.md src/
# 期望: 无新增 overclaim

# 确认 runs/ 只多了 v2-factslot (gitignored)
git status --short runs/

# 确认 FINALIZED.json 存在 (spec line 248)
ls runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json
```

## 验收标准（全部勾选才算 S2 完成）

- [ ] `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json` 存在
- [ ] 50/50 samples（manifest 显示 expected_sample_count=50, completed_sample_count=50, missing=0, valid=true）
- [ ] `retrieval.jsonl` 行数 = 50 × 4 = 200（no_memory + full_context 不产 retrieval）
- [ ] ETEC actions 报告生成（含 SUPERSEDE 计数）
- [ ] **fact_slot 有效率 ≥ 50%**（spec line 251，非硬 gate，作为"S1c 是否在 50 题上稳定生效"的诊断信号——若低于 50%，回 S1c 修 prompt 或走 spec fallback 重新评估 50% 门槛）
- [ ] **valid_from 非空率 ≥ 50%**（spec line 252，状态变化类事实应都产）
- [ ] **sentinel 率 < 20%**（S1c fallback 路由的未决项——若 50 题 ≥ 20%，写明事实，路由到 S3/S5 决策；不在此处反复调 prompt 凑数）
- [ ] 可达性测试 PASS 或 XFAIL（**两者都算 S2 通过**——S2 只测可达性，不要求 reachability 命中数变化）
- [ ] v1 vs v2 `full` EM 对比表写入 `docs/EVALUATION.md`（不预先声明期望；不跨模型对比）
- [ ] `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q` 全绿
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run mypy src` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"
- [ ] `git diff src/evoeventmem/` 为空（S2 是测量阶段，不改代码）
- [ ] `git status --short runs/` 无 commit（runs/ 是 gitignored）
- [ ] `git diff --stat` 仅触及 `docs/EVALUATION.md` + 可能的 `scripts/run50-parallel-v2-factslot.sh`（副本）+ 新建的 `docs/STAGE2_REVIEW.md`（独立审查产物）
- [ ] 独立审查 PASS（`docs/STAGE2_REVIEW.md`）

## 验证命令（spec 复制 + S1c 复用）

```bash
ls runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json
uv run python -c "
import json
from pathlib import Path
from collections import Counter
actions = Counter()
for f in Path('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/samples').glob('*.json'):
    if 'extraction_snapshot' in f.name: continue
    d = json.loads(f.read_text())
    a = d.get('ingestion',{}).get('etec',{}).get('actions')
    if a: actions.update(a)
print('ETEC actions:', dict(actions))
print('SUPERSEDE count:', actions.get('SUPERSEDE', 0))
"
uv run python -m benchmarks.mechanism.extraction_smoke runs/publication/m13-longmemeval-test50-mimo-v2-factslot
EEM_S1B_SNAPSHOT_PATH=runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json \
  uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## S2 验收失败的 fallback

**如果 50 题 fact_slot 有效率 < 50%**：

1. 不在 S2 调 prompt 凑数（S1c 已用 2 次 tweak；若需第 3 次，独立审查显式批准）。
2. 在 `docs/STAGE2_REVIEW.md` 写明：50 题 v3 prompt 的 fact_slot 有效率 = X%，per-sample 分布，sentinel 占比 = Y%。
3. 触发 spec fallback 替代路径：**重新评估 50% 门槛本身在 50 题上是否合理**。把决策路由到 S3：S3 同时测 QEMR + 决定是否 pivot；S5 决定 paper framing。

**如果 SUPERSEDE = 0**：

1. 不修 R3（AGENTS.md 反 fishing）。
2. 在 `docs/STAGE2_REVIEW.md` 写明：50 题 v3 run 上 SUPERSEDE = 0；R3 阻塞率 = X%（若 multi_valued=0% 则 R3 未阻塞；SUPERSEDE=0 是 R1b `valid_until` 缺失或 interval 不重叠导致）。
3. 触发 S5 path A（negative-result 论文 framing）；S3 仍要做（解释 QEMR 为何失败）。

**如果 mimo provider 不可用**（API key 失效 / 网络不通 / 50 题配额耗尽）：

1. 不强行用 fake extractor 凑数。
2. 把失败原因写进 `docs/STAGE2_REVIEW.md`（HTTP 错误码 / 网络测试 / API key 状态 / 配额状态）。
3. 已完成的批次仍有效；用 `--resume-dir` 续跑未完成批次。
4. 若 mimo 长期不可用，标 BLOCKED，建议回 S0 评估 provider 可用性或换模型（换模型需独立审查显式批准 + spec update）。

**如果 sentinel 率 > 20% on 50 题**：

1. 不在 S2 反复调 prompt 凑数（spec line 345 + AGENTS.md 反 fishing）。
2. 在 `docs/STAGE2_REVIEW.md` 写明：50 题 v3 prompt 的 sentinel 率 = X%（vs S1c 5 题的 39.7%）；per-sample 分布；抽样分析哪些"User enjoys X" / "User plans to do Y" 句被 LLM 误判为 "none"。
3. 路由到 S3：S3 在 SUPERSEDE > 0 时同时测 QEMR + M2 stale-judge；若 SUPERSEDE = 0，sentinel 率 > 20% 是已知 weakness，写进 S5 paper framing 的 limitations。

## 独立审查协议（S2 完成后必须执行）

S2 完成后，**派一个独立 subagent**（不审自己写的代码）执行以下检查，输出 `docs/STAGE2_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **50 题 snapshot 真实性**：50 个 per-sample snapshots；全部 events 标 `extractor_prompt_version == "event-extraction.v3"`；抽样 3-5 个 event 检查 evidence_refs + raw_turn_id + locator 链完整。
3. **ETEC actions 报告真实性**：独立重跑 `Counter` 脚本，确认 SUPERSEDE 计数；与 `summary.json` 的 `methods.etec.actions` 对账。
4. **fact_slot / sentinel 率前后对比**：S1c 5 题 = 60.3% 有效 / 39.7% sentinel；S2 50 题 = X% 有效 / Y% sentinel；per-sample 分布对比；sentinel 率 < 20% 与否决定路由。
5. **可达性测试 sound**：reachability test 调真实 consolidation 函数（无 mock）；PASS 或 XFAIL 都算通过；snapshot 路径参数化正确（`EEM_S1B_SNAPSHOT_PATH` 生效）。
6. **R3 未被碰**：`git diff src/evoeventmem/consolidation.py` 空；`_EventDraft` 仍无 `multi_valued` 字段；`consolidation.py` 的 `multi_valued` / `0.7` / `supersede_contradiction_min` 引用全为 S1a 前已存在。
7. **scope 边界守住**：`git diff --stat` 仅触及 `docs/EVALUATION.md` + 可能的 `scripts/run50-parallel-v2-factslot.sh` + 新建的 `docs/STAGE2_REVIEW.md`；不碰 `src/evoeventmem/*`。
8. **未引入新 overclaim**：`rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效' docs/EVALUATION.md src/` —— S2 不应声称 thesis 翻盘或 ETEC 有效（即使 SUPERSEDE > 0）；只能声称"50 题上测到 SUPERSEDE = N"或"v2 `full` EM = X vs v1 = 0.46"。
9. **未跨模型对比**：`docs/EVALUATION.md` 新节只对比 v1 (mimo-v2.5) vs v2 (mimo-v2.5)；不对比 v2 vs 24 题 deepseek-v4-flash run。
10. **replay/online 一致性**：v2 run 上 replay 与 online `ingestion.etec.actions` 对比；若发散，记录为 known limitation，不静默修复。
11. **git 状态**：除 `runs/` 外工作区干净或变更可解释；HEAD 未被 S2 推进（不擅自 commit）。
12. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；UTC-aware datetime；小纯函数 + ports；不 commit datasets / secrets / model weights / benchmark caches。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S2 修复；CONDITIONAL PASS 可进 S3 但标注未决项。审查通过后才能进 S3。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建的文件 + 每个文件的变更摘要（1-2 行）。
2. **50 题前后对比**：
   - S1c 5 题 baseline：fact_slot 有效率 60.3%，sentinel 39.7%，可达性 107 four-gate pairs
   - S2 50 题：fact_slot 有效率 X%，sentinel 率 Y%，可达性 N four-gate pairs，SUPERSEDE 计数 = M
   - per-sample 分布对比（50 题 vs S1c 5 题）
3. **ETEC actions 分布**：ADD / MERGE / SUPERSEDE / REJECT 计数（v2 vs v1 baseline）
4. **EM 对比**：v1 vs v2 `full` EM（不跨模型）；6 methods 完整对比表
5. **验收标准勾选**：15 条 acceptance criteria 逐条 ✅/❌/⚠️ + 验证命令输出
6. **独立审查结果**：`docs/STAGE2_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现
7. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出
8. **异常/风险**（如有）：
   - mimo provider 不可用 → 走 fallback
   - fact_slot 有效率仍 < 50% on 50 题 → 走替代 fallback（重新评估 50% 门槛，路由到 S3）
   - SUPERSEDE = 0 → 走 S5 path A (negative-result 论文)
   - sentinel 率 > 20% on 50 题 → 路由到 S3/S5 决策
   - replay/online 发散 → 记录为 known limitation
   - S4b 未做（路径 B）→ vector_rag 延迟病态，与 v1 不可比
9. **下一阶段路由**：
   - 若 SUPERSEDE > 0 + EM 翻盘 → S3（QEMR 失效根因 + M2 stale-judge）
   - 若 SUPERSEDE > 0 + EM 不翻盘 → S3（QEMR 失效根因）
   - 若 SUPERSEDE = 0 → S5 path A（negative-result 论文 framing）；S3 仍要做（解释 QEMR 为何失败）
10. **commit 决策**：**不擅自 commit**——报告完成后询问用户是否 `git add -A && git commit && git push`。commit message 模板：`feat(s2): 50-question v3 rerun + ETEC reachability diagnosis + EM comparison`。

## 不做什么（防止 scope creep）

- 不开始 S3/S4/S5（S2 完成并 commit + 审查通过后才允许）。
- 不修 R3（`multi_valued` 过打）——S2 的 scope 边界，AGENTS.md 反 fishing 规则约束。
- 不调 `supersede_contradiction_min=0.7` 阈值——反 fishing。
- 不动 `src/evoeventmem/consolidation.py` / `retrieval.py` / `router.py` / `extraction.py`——S2 是测量阶段，不改代码。
- 不动 prompt——若 sentinel 率 > 20%，路由到 S3/S5 决策；不在 S2 反复调 prompt 第 N 次凑数。
- 不跑 LoCoMo / reader ablation / 500 题——S3 / S5 的事。
- 不擅自 commit（询问用户）。
- 不声称 thesis 翻盘 / ETEC 有效 / SUPERSEDE > 0 经验上（即使测出 SUPERSEDE > 0，只能说"50 题上测到 SUPERSEDE = N"）。
- 不跨模型对比（v2 vs 24 题 deepseek run 禁止）。
- 不预先声明期望（预注册的 negative-result 框架）。
- 不调 stats 脚本计算逻辑让 fact_slot 看上去达标——若 < 50%，如实记录，走 fallback。

## 故障排查

| 问题 | 解决 |
|---|---|
| mimo HTTP 429 / 超时 / "Remote end closed" | 失败批次用 `--resume-dir <batch_run_dir>` 重跑；每批独立 run dir 避免 manifest drift |
| embedding tunnel 断开 | `ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090` 重建；中断批次用 `--resume-dir` 续跑 |
| vector_rag p50 search 仍 ~437s | 路径 B（S4b 未做）；并行度降到 5；run 拖到 2-3 天；显式声明 latency 与 v1 不可比 |
| manifest drift (多批共享 run dir 冲突) | 每批用独立 `--run-dir runs/.../batch-N`；finalize 阶段合并 |
| 50 题 SUPERSEDE 仍 0 | 真实可能——extraction 产了 fact_slot 但 LLM 仍过打 multi_valued（R3 未修）OR valid_until 缺失 OR interval 不重叠；记录事实，路由到 S5 path A (negative-result 论文) |
| sentinel 率 > 20% on 50 题 | 不在 S2 调 prompt；记录事实，路由到 S3/S5 |
| v2 `full` EM 翻盘但 v2 `etec` / `event_no_etec` EM 不变 | 写明事实——SUPERSEDE > 0 但 ETEC 贡献 = full - event_no_etec；进 S3 测 QEMR |
| reader LLM 配额耗尽 | 已完成批次的 extraction + retrieval 仍有效；reader 配额恢复后用 `--resume-dir` 续跑 |

## 预计时间

- 2-3 天，单窗口可完成（50 题 run + finalize + 诊断 + 报告）。
- Step 0（S4b 前置检查 + 可能的 S4b 修复）：0-1 天（取决于 S4b 是否已做）。
- Step 1（侦察）：30 分钟。
- Step 2（跑 50 题 v2 run）：1-2 天（10 批并行，~1.5 天若 S4b 已做；2-3 天若 S4b 未做用路径 B）。
- Step 3-5（finalize + 诊断 + 写报告）：0.5 天。
- Step 6-7（回归 + 扫描）：0.5 天。
- 独立审查：1-2 小时。

## 文献依据

- **LongMemEval §5.3** (arXiv:2410.10813, ICLR 2025)：reported positive recall (+9.4% recall@k) and QA (+5.4% accuracy) gains from "fact-augmented key expansion"——**注：具体百分比来自论文正文 §5.3，独立审查未从摘要验证，按定性"reported positive gains"采纳**。S2 的 50 题 v2 run 是这个 thesis 的本地复现——若 SUPERSEDE > 0 + EM 翻盘，本地数据**部分支持** thesis（不能声称"复现 +9.4% recall"，5 题/50 题样本太小）；若 SUPERSEDE = 0 或 EM 不翻盘，thesis 在本地数据上**不被支持**。
- **S1c 实测证据**（`docs/STAGE1c_REVIEW.md`）：v3 prompt 在 5 题上 fact_slot 有效率 60.3%、sentinel 率 39.7%；S2 在 50 题上验证这两个率是否稳定。
- **spec fallback**（`docs/REMEDIATION_SPEC.md` Stage 1c fallback + Stage 2）：明确路由"sentinel > 20% → S2 在 50 题上重新评估 + 测 R3 阻塞率 + 决定 pivot"——S2 是 fallback 的直接落地。
- **预注册的 negative-result 框架**（`METHODOLOGY_CHANGE.md`）：要求不 bias 结果解读；S2 不预先声明期望；测出什么写什么。
