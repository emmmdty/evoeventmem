# Stage 1c 执行提示词：S1a prompt 加固（required fact_slot + retry on missing fact_slot）

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `<S1b-commit-hash>`，**待用户 commit S1b 后填入**）刚完成 Stage 1b：在 S1a schema 落地之上跑了 5 题 LongMemEval（`e47becba 118b2229 51a45a95 58bf7951 1e043500`）真实 mimo-v2.5 extraction smoke + fact_slot 非空率统计 + 四重 gate 真实数据可达性测试。独立审查 **CONDITIONAL PASS**（`docs/STAGE1b_REVIEW.md`）—— 13/14 验收绿、唯一未决项是 `fact_slot 非空率 = 48.2% (321/666) < spec 的 50% 门槛`。

S1b 的关键诊断证据（写进 `docs/STAGE1b_REVIEW.md`）：

- **5 题真实数据测量**：666 events，per-sample fact_slot 非空率分布 = `118b2229:51.7% / 1e043500:33.3% / 51a45a95:52.8% / 58bf7951:42.5% / e47becba:58.2%`。3/5 已 ≥ 50%；`1e043500=33.3%` 是单点拖低均值的离群。
- **可达性 PASS（非 xfail）**：22 对 within-sample event 在真实 LLM 输出上满足全部四重 gate（44,678 对枚举中）；`blocked_by_multi_valued=0`——R3 在本切片未阻塞（S1a 没 emit `multi_valued`，所以四重 gate 真正 reachable，不是 R3 bypass）。
- **valid_until ≈ 0**：`valid_to=0.3%`（2/666），多数 fact 是 start-only 单 event 形式（`valid_until=None`）。
- **multi_valued=0%**：S1a 没 emit；R3 未修也不在 S1c scope。
- **S1a v2 prompt 真进了 LLM 调用**：666/666 events 的 `metadata.extractor_prompt_version == "event-extraction.v2"`。

整改 spec v1.1（`docs/REMEDIATION_SPEC.md` Stage 1b fallback，lines 320-324）明确路由：

> 如果 fact_slot 非空率 < 50% → S1a prompt 在真实数据上未生效：
> 1. 不在 S1b 调 prompt——回 S1a 修 prompt（S1a 才管 prompt）。
> 2. 把 5 题 snapshot 的低 fact_slot 非空率证据写进 `docs/STAGE1b_REVIEW.md`，建议回到 S1a 加 prompt 强约束（如 required field + retry on missing fact_slot）。
> 3. 可达性测试 xfail（四重 gate 命中数为 0 是 fact_slot 缺失的下游结果）。

S1c 是 spec fallback 路由出来的窗口：**回到 S1a 范围（extraction.py 的 schema/prompt/wiring），加 required `fact_slot` + retry on missing fact_slot，把 fact_slot 非空率从 48.2% 推到 comfortably ≥ 50%**。S1b 的可达性 PASS 证明 S1a v2 prompt 已让 LLM 部分产 fact_slot——S1c 是把"部分"变成"稳定多数"。

**scope 边界（明确声明，不藏着）**：S1c **只动 `src/evoeventmem/extraction.py` 的 schema + prompt + retry 三处**，不动 consolidation/retrieval/router；不修 R3；不调阈值；不跑 50 题。S1c 是 S1a 的窄化补丁，不是新 stage——PROMPT_VERSION 从 `event-extraction.v2` 升到 `event-extraction.v3` 以便 snapshot 可追溯。

**为什么是这一阶段**：S1b CONDITIONAL PASS 的唯一未决项是 fact_slot < 50%。S2 spec（`docs/REMEDIATION_SPEC.md` Stage 2，lines 228+）要求 50 题 statistically meaningful 的 R3 阻塞率测量——但 S2 在 48.2% fact_slot 下重跑会继承 S1a prompt 的不稳定，结果噪声大。S1c 先在 5 题上证明 prompt 加固能把 fact_slot 推到 ≥ 50%，S2 才有理由相信 50 题上 fact_slot rate 会稳定 ≥ 50%。**S1c 不论结果都是赢**：若 fact_slot 推到 ≥ 50% → S2 重跑有理由相信 SUPERSEDE > 0；若仍 < 50% → 重新评估 50% 门槛本身（spec fallback 替代路径），写进 `docs/STAGE1c_REVIEW.md` 作为 S2 的输入。

### 已完成的前置工作

- S0 完成（commit `b60b38d`）。
- S1a 完成（commit `162183c`，独立审查 PASS，schema + prompt v2 落地）。
- S1b 完成（commit `<S1b-commit-hash>`，独立审查 CONDITIONAL PASS，5 题 smoke + stats + reachability 全套落地）。
- S1b 5 题 snapshot 已生成于 `runs/s1b/smoke5/`（gitignored），作为 S1c 的"加固前 baseline"对照——**S1c 跑完后应用相同 5 题、相同 mimo provider 重测**，直接比较 fact_slot 非空率前后变化。
- S1b 可复用基础设施：
  - `configs/longmemeval/smoke5-mimo.toml`（5 题 mimo provider 配置，`sample_limit = 5`）
  - `benchmarks/longmemeval/run.py --extraction-only`（短路 flag，跳过 retrieval/reader/finalize）
  - `benchmarks/mechanism/extraction_smoke.py`（stats 脚本，`uv run python -m benchmarks.mechanism.extraction_smoke <run_dir>`）
  - `tests/consolidation/test_etec_real_data_reachability.py`（四重 gate 真实数据可达性测试，读 `runs/s1b/smoke5/extraction_snapshot.json`，**S1c 需把路径改成 `runs/s1c/smoke5/...` 或参数化**）
  - `tests/benchmarks/test_extraction_smoke.py`（stats 脚本单测）
- S1a extraction.py 已有 `_extract_single` 3 次 retry 框架（`extraction.py:669-690` 附近，处理 LLM invalid JSON）——S1c 在此框架上加 `retry on missing fact_slot`，不重写 retry 基础设施。
- mimo provider 已验证可用（`OPENAI_API_KEY` 在 `.env`；网络可达 `https://opencode.ai/zen/go/v1`）。

### 关键约束（违反即 spec 失败）

- **只做 S1c，不开始 S2/S3/S4/S5**——S1c 完成并 commit + 独立审查通过后才允许进入下一阶段。
- **只动 `src/evoeventmem/extraction.py`**——schema（`_EventDraft` 加 `fact_slot` required 校验）+ prompt（v2 → v3，加强 fact_slot 必产约束）+ retry（`_extract_single` 在 fact_slot 缺失时重试）三处。不动 `consolidation.py` / `retrieval.py` / `router.py`；不动 S1b 的 runner / stats / reachability test 主干（除非 reachability test 需把 snapshot 路径参数化——此时只改路径常量，不改逻辑）。
- **不修 R3（`multi_valued` 过打）**——S1c 的 scope 边界，AGENTS.md 反 fishing 规则约束。`consolidation.py:876` 的 `multi_valued` 短路保留；`_EventDraft` 仍**不**含 `multi_valued` 字段（S1a/S1b 已确认）。
- **不调阈值**——`supersede_contradiction_min=0.7` 不动；不动任何 weight profile；不动 retrieval budget。
- **不跑 50 题 / 500-run / 新 benchmark / reader**——S1c 只跑 S1b 已有的 5 题 extraction smoke；不跑 reader；不跑 retrieval；不消耗 reader LLM 配额（仅消耗 extractor 配额，含 retry 增量）。
- **不破坏既有契约**：`uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q` + `uv run ruff check .` + `uv run mypy src` + `uv run python -m evoeventmem.cli smoke` 必须全绿（S1b 已加 `tests/benchmarks/test_extraction_smoke.py`，S1c 也跑它）。
- **不破坏 evidence provenance**——`evidence_refs` + `raw_turn_id` + `locator` 链不动；S1c 的 schema 改动是加 required 校验，不改字段名/语义。
- **不擅自 commit**——完成后报告变更清单，询问用户是否 commit + push。
- **不声称 SUPERSEDE > 0**——S1c 只测 fact_slot 非空率，不声称"经验上 SUPERSEDE > 0"（5 题样本太小，S2 才是 50 题 statistically meaningful 的测量）；任何"提升 / outperform"措辞属于 overclaim。
- **不删 S1a/S1b 落地**——`_EventDraft` 的 `fact_slot` / `fact_value` / `valid_from` / `valid_until` 4 字段保留；S1a 的 few-shot 示例保留；S1b 的 runner flag / stats 脚本 / reachability test 保留。
- **不调阈值让 fact_slot 看上去达标**——若 prompt 加固后 fact_slot 仍 < 50%，**不**在 stats 脚本里调计算逻辑、**不**改 required 校验让某些 event 类型豁免；如实记录 < 50% 的事实，走 fallback 路径。

## 执行步骤

### Step 1: 侦察（read-only，不改文件）

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -4   # 确认 HEAD 是 S1b commit 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）

# 确认 spec + S1b 审查 + 真实数据都存在
test -f docs/REMEDIATION_SPEC.md && echo "spec OK"
test -f docs/S1a-execution-prompt.md && echo "s1a prompt OK"
test -f docs/S1b-execution-prompt.md && echo "s1b prompt OK"
test -f docs/STAGE1a_REVIEW.md && echo "s1a review OK"
test -f docs/STAGE1b_REVIEW.md && echo "s1b review OK"
test -f runs/s1b/smoke5/extraction_snapshot.json && echo "s1b baseline snapshot OK"

# 确认 S1a 4 字段仍在（应输出 >=4）
grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py

# 确认 PROMPT_VERSION 仍 v2（S1c 才升 v3）
grep -n "PROMPT_VERSION" src/evoeventmem/extraction.py | head -3   # LLMEventExtractor 应是 event-extraction.v2

# 确认 R3 仍未修（_EventDraft 仍无 multi_valued 字段）
grep -n "multi_valued" src/evoeventmem/extraction.py   # 期望: 无 _EventDraft.multi_valued 字段定义

# 读 S1b 审查（fact_slot < 50% 的诊断证据 + per-sample 分布）
sed -n '60,90p' docs/STAGE1b_REVIEW.md   # 或用 Read 工具读 §3

# 读 LLMEventExtractor 当前 prompt + _EventDraft schema + _extract_single retry 框架
grep -n "class LLMEventExtractor\|class _EventDraft\|PROMPT_VERSION\|def _build_llm_prompt\|def _extract_single\|def _build_memory\|retry\|max_retries\|fact_slot_rules" src/evoeventmem/extraction.py | head -30

# 读 S1a 的 prompt 内容（system prompt + few-shot 示例 + fact_slot_rules）
# 这部分 S1a 升 v2 时加的，S1c 在此基础上加强约束（不重写）
sed -n '700,900p' src/evoeventmem/extraction.py   # 大致 LLMEventExtractor 类的 prompt 区域

# 读 S1b 的 stats + reachability 测试（S1c 复用，可能改路径常量）
grep -n "SNAPSHOT_PATH\|runs/s1b" tests/consolidation/test_etec_real_data_reachability.py
head -30 benchmarks/mechanism/extraction_smoke.py
```

预期发现：
- `LLMEventExtractor.PROMPT_VERSION = "event-extraction.v2"`（S1a 落地，S1c 升 v3）。
- `_EventDraft` 已有 `fact_slot` / `fact_value` / `valid_from` / `valid_until` 4 个 Optional 字段（S1a 落地，S1c 把 `fact_slot` 从 Optional 改成 required + validator）。
- `_extract_single` 已有 3 次 retry 框架（处理 invalid JSON）—— S1c 加 `retry on missing fact_slot`。
- `tests/consolidation/test_etec_real_data_reachability.py` 顶部有 `SNAPSHOT_PATH = Path("runs/s1b/smoke5/extraction_snapshot.json")` 常量——S1c 需参数化或改成 S1c 路径。
- `benchmarks/mechanism/extraction_smoke.py` 是 run-dir 参数化的（CLI 接 `run_dir`），不动。
- mimo provider 可用（`OPENAI_API_KEY` 在 `.env`；`uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/smoke5-mimo.toml --validate-config` 应显示 extractor `api_key_set=true`，但需 `set -a; source .env; set +a` 先加载）。

**关键判断**：侦察完后，确认 mimo provider 仍可用。**若不可用**（API key 失效 / 网络不通），S1c 标 BLOCKED，不强行用 fake extractor 凑数（fake 不产真实 fact_slot，违反"真实数据 smoke"目的）。

### Step 2: 加固 prompt + schema + retry（`src/evoeventmem/extraction.py`）

S1c 三处改动，**只动 extraction.py**：

**(a) `_EventDraft.fact_slot` 从 Optional 改成 required-with-validator**：

- 把 `fact_slot: str | None = None` 改成 `fact_slot: str = Field(min_length=1, max_length=128)`（required，非空，长度限制）。
- 加 pydantic `field_validator` 或 `model_validator`：若 LLM 输出 `fact_slot=null` 或 `fact_slot=""`，validator 抛 `ValidationError`，由 `_extract_single` 的 retry 捕获重试。
- `fact_value` / `valid_from` / `valid_until` 保留 Optional（S1a 已确认——非状态类事实可能不产 valid_until）。
- **风险**：required `fact_slot` 可能让 LLM 在非事实陈述句上也强行产 fact_slot（噪声）。**缓解**：validator 接受 LLM 显式标注的 `"fact_slot": "none"` 或 `"fact_slot": "n/a"` 作为合法值（不算 missing，不触发 retry），但 stats 脚本统计 fact_slot 非空率时应排除这些 sentinel 值（**S1c 不改 stats 脚本——若 LLM 大量产 "none"，fact_slot 非空率看上去高但实际无意义，需在 STAGE1c_REVIEW.md 标注**）。**首选**：让 LLM 在非事实句上产 `fact_slot="none"`（显式 sentinel），而不是 `null`（隐式 missing）——这区分了"LLM 判断此句非事实"和"LLM 没遵守 schema"。

**(b) prompt v2 → v3，加强 fact_slot 必产约束**：

- `LLMEventExtractor.PROMPT_VERSION = "event-extraction.v3"`。
- 在 system prompt 加强约束：
  - 显式声明 `fact_slot` 是 required（非空，非 null）。
  - 对非事实陈述句（greetings / chitchat / meta-discussion），允许 `fact_slot = "none"`，但**禁止** `null` / `""`。
  - 加 1-2 个 few-shot 示例：一个非事实句产 `fact_slot="none"`，一个事实句产显式 `fact_slot`。
  - 不删 S1a 的 4 个 few-shot 示例（含状态变化两-event 拆分）——S1c 在它们旁边加新示例。
- **不重写 prompt 主体**——S1a 的 prompt 结构保留；S1c 只加约束段 + 新 few-shot。

**(c) `_extract_single` 加 retry on missing fact_slot**：

- 在现有 3 次 retry 框架内（处理 invalid JSON）加 fact_slot 校验：解析 JSON 成功后，若 `_EventDraft.model_validate` 抛 `ValidationError` 且错误字段是 `fact_slot`，记一次 retry 原因 `"missing_fact_slot"`，重试（最多 3 次，复用现有 retry 计数）。
- 若 3 次 retry 仍失败（LLM 持续产 `null` fact_slot），**不**抛掉整条 event——降级 fallback：把 `fact_slot` 设为 `"none"`（sentinel），让 event 仍被记录（保留 evidence provenance + 其他字段），但 stats 脚本会把它计入 `fact_slot 非空率` 的分母。**在 `STAGE1c_REVIEW.md` 标注 fallback 触发率**。
- 若 retry 框架不易加 fact_slot 校验（耦合太深），**fallback 路径**：在 `_build_memory` 阶段加 post-validation——若 `_EventDraft.fact_slot` 为空，记一次 `missing_fact_slot` 警告到 snapshot 的 `rejections` 列表，仍写 event 但 `metadata.fact_slot = "none"`。**任选其一，cleanest 是 `_extract_single` 内 retry**。

### Step 3: 参数化 reachability test 的 snapshot 路径

把 `tests/consolidation/test_etec_real_data_reachability.py` 顶部的：

```python
SNAPSHOT_PATH = Path("runs/s1b/smoke5/extraction_snapshot.json")
```

改成环境变量或 pytest fixture 参数化，让 S1c 能指向 `runs/s1c/smoke5/extraction_snapshot.json`：

```python
SNAPSHOT_PATH = Path(
    os.environ.get(
        "EEM_S1B_SNAPSHOT_PATH",
        "runs/s1b/smoke5/extraction_snapshot.json",
    )
)
```

或加一个 `--snapshot-path` pytest CLI 选项。**只动这一处常量/参数化**，不改 reachability 逻辑。S1c 跑 reachability 时 `EEM_S1B_SNAPSHOT_PATH=runs/s1c/smoke5/extraction_snapshot.json pytest ...`。

### Step 4: 跑 S1c 5 题 smoke（复用 S1b infra，新建 run_dir）

```bash
mkdir -p runs/s1c/smoke5
set -a; source .env; set +a
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/smoke5-mimo.toml \
  --sample-ids e47becba 118b2229 51a45a95 58bf7951 1e043500 \
  --extraction-only \
  --run-dir runs/s1c/smoke5 \
  2>&1 | tee runs/s1c/smoke5/run.log
```

**预期产物**（均在 `runs/s1c/smoke5/`，gitignored）：
- `samples/<safe_id>.extraction_snapshot.json` × 5
- `extraction_snapshot.json`（combined）
- `run.log`
- 每个 event 的 `metadata.extractor_prompt_version == "event-extraction.v3"`（确认 v3 prompt 真进了 LLM 调用）

**注意 retry 增量成本**：5 题 × ~127 events × 平均 1.X retry = 最多 ~5×4 = 20 次 LLM 调用（最坏 3 次 retry 全触发）；实测应在 5-15 次之间。允许 30-45 分钟完成。

**失败处理**：
- HTTP 429 / 网络超时 → 重试 1 次；仍失败标 BLOCKED，不强行用 fake 凑。
- LLM 产 invalid JSON → 现有 retry 已处理；若 5 题有 0 个候选 event，记录在 `run.log` 并继续。
- v3 prompt 让 LLM 完全不响应（如要求太严）→ 记录在 `run.log`，回 S1c Step 2 微调 prompt 约束。

### Step 5: 跑 S1c stats + reachability（复用 S1b 脚本）

```bash
uv run python -m benchmarks.mechanism.extraction_smoke runs/s1c/smoke5
EEM_S1B_SNAPSHOT_PATH=runs/s1c/smoke5/extraction_snapshot.json \
  uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s
```

要求：
- **fact_slot 非空率 ≥ 50%**（spec 验收标准；目标 comfortably above 50%，如 ≥ 60%，留噪声空间）。
- 若 LLM 大量产 `fact_slot="none"`（sentinel），stats 脚本会把它计入非空（"none" 是非空字符串）。**需在 STAGE1c_REVIEW.md 显式标注**："none" sentinel 占比 X%——若 X > 20%，说明 LLM 大量把事实句误判为非事实，仍是 prompt 问题，回 Step 2 调 few-shot。
- 可达性测试 PASS 或 XFAIL（**两者都算 S1c 通过**——S1c 只测 fact_slot 非空率，不要求 reachability 命中数变化）。

### Step 6: 全套回归（与 S1b 一致 + 加 stats 单测）

```bash
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

要求全绿。S1c 改了 `extraction.py` schema——`tests/extraction/` 应有新 required field 的单测覆盖（若无，S1c 加 1-2 个单测验证 `fact_slot` 缺失抛 `ValidationError` + retry 触发）。

### Step 7: 全局一致性扫描

```bash
# 确认 S1c 只动了 extraction.py + reachability test 路径常量
git diff --stat
# 期望: src/evoeventmem/extraction.py + tests/consolidation/test_etec_real_data_reachability.py（仅路径常量）

# 确认未碰 consolidation/retrieval/router
git diff src/evoeventmem/consolidation.py src/evoeventmem/retrieval.py src/evoeventmem/router.py
# 期望: 空

# 确认 R3 未碰
grep -n "multi_valued" src/evoeventmem/extraction.py
# 期望: 无 _EventDraft.multi_valued 字段定义

# 确认 PROMPT_VERSION 升 v3
grep -n "PROMPT_VERSION" src/evoeventmem/extraction.py | head -3
# 期望: LLMEventExtractor = event-extraction.v3

# 确认 5 题 snapshot 真实生成 + v3 prompt 进了 LLM
uv run python -c "
import json
from pathlib import Path
snaps = json.loads(Path('runs/s1c/smoke5/extraction_snapshot.json').read_bytes())
for s in snaps:
    sid = s.get('conversation_id')
    events = s.get('events', [])
    v3 = sum(1 for e in events if isinstance(e, dict) and e.get('metadata', {}).get('extractor_prompt_version') == 'event-extraction.v3')
    print(f'{sid}: events={len(events)} v3={v3}')
"

# 确认 S1c 没新增 overclaim
grep -rE "显著提升|significant improvement|outperform|SUPERSEDE > 0|supersede reachable" docs/ src/ 2>&1 | grep -v "S1a-execution-prompt\|S1b-execution-prompt\|S1c-execution-prompt\|8of10_AUDIT\|REMEDIATION_SPEC\|STAGE.*_REVIEW\|STRONG_RESULTS_SMALL_SAMPLE\|8of10_ACCEPTANCE\|specs/2026-08-17-o09" | head
# 期望: 无新增 overclaim

# 确认 runs/ 只多了 s1c/smoke5（gitignored）
git status --short runs/   # 期望: 无输出

# 确认 5 题 snapshot 真实生成
ls runs/s1c/smoke5/samples/*.extraction_snapshot.json | wc -l   # 期望 5
```

## 验收标准（全部勾选才算 S1c 完成）

- [ ] `src/evoeventmem/extraction.py` schema 改动落地（`_EventDraft.fact_slot` required + validator；`PROMPT_VERSION = "event-extraction.v3"`；prompt 加 fact_slot 必产约束 + 1-2 个新 few-shot；`_extract_single` 加 retry on missing fact_slot）
- [ ] `tests/consolidation/test_etec_real_data_reachability.py` 的 `SNAPSHOT_PATH` 参数化（环境变量 `EEM_S1B_SNAPSHOT_PATH` 或等价），不改 reachability 逻辑
- [ ] `runs/s1c/smoke5/extraction_snapshot.json` 存在并含 5 题 snapshot，全部 events 标 `extractor_prompt_version == "event-extraction.v3"`
- [ ] **fact_slot 非空率 ≥ 50%**（spec 验收标准；目标 comfortably above 50% 如 ≥ 60%；若 LLM 大量产 "none" sentinel，需在 STAGE1c_REVIEW.md 标注 sentinel 占比，且"none" 不算入"有效 fact_slot"——分母里排除）
- [ ] 可达性测试 PASS 或 XFAIL（**两者都算 S1c 通过**——S1c 不要求 reachability 命中数变化）
- [ ] `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q` 全绿（S1b 回归不破 + stats 单测仍绿）
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run mypy src` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"
- [ ] `git diff src/evoeventmem/consolidation.py` / `retrieval.py` / `router.py` 为空
- [ ] `git status --short runs/` 无 commit（runs/ 是 gitignored）
- [ ] `git diff --stat` 仅触及 `src/evoeventmem/extraction.py` + `tests/consolidation/test_etec_real_data_reachability.py`（仅路径常量参数化）+ 可能新增的 `tests/extraction/test_fact_slot_required.py` 单测
- [ ] 独立审查 PASS（`docs/STAGE1c_REVIEW.md`）

## 验证命令（spec 复制）

```bash
uv run python -m benchmarks.mechanism.extraction_smoke runs/s1c/smoke5
EEM_S1B_SNAPSHOT_PATH=runs/s1c/smoke5/extraction_snapshot.json \
  uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## S1c 验收失败的 fallback

**如果 prompt 加固后 fact_slot 非空率仍 < 50%**：

1. **不**继续调 prompt 凑数（调到第 N 次凑出 ≥ 50% 属于 fishing）。
2. 在 `docs/STAGE1c_REVIEW.md` 写明：5 题 sample 上 v3 prompt 的 fact_slot 非空率 = X%，per-sample 分布，"none" sentinel 占比 = Y%。
3. 触发 spec fallback 替代路径：**重新评估 50% 门槛本身在 50 题上是否合理**（5 题噪声大，1e043500 单点拖低均值；50 题上均值可能自然稳定 ≥ 50%）。把决策路由到 S2：S2 在 50 题上同时测 v3 prompt 的 fact_slot 非空率 + R3 阻塞率，若 fact_slot ≥ 50% → 继续 S2 R3 测量；若 < 50% → S2 spec 决定 pivot（如放弃 R1 修复，转 R3 直接修复）。
4. S1c 仍 commit 已落地代码（schema required + retry + v3 prompt + 路径参数化），但 `docs/STAGE1c_REVIEW.md` 标 CONDITIONAL PASS / FAIL（视事实而定）。

**如果 mimo provider 不可用**（API key 失效 / 网络不通）：

1. 不强行用 fake extractor 凑数。
2. 把失败原因写进 `docs/STAGE1c_REVIEW.md`（HTTP 错误码 / 网络测试 / API key 状态）。
3. 已落地的 schema + prompt + retry 代码仍 commit——但 `runs/s1c/smoke5/` 不存在，可达性测试 skip 而 stats 报错。
4. 询问用户是否进 S2（若 mimo 恢复）或回 S0 评估 provider 可用性。

**如果 v3 prompt 让 LLM 完全不产 event**（要求过严）：

1. 不在 S1c 反复调 prompt——回 Step 2 微调一次（如放宽 `fact_slot="none"` 的接受条件、减少 few-shot 严格度）。
2. 若微调后仍 0 event，标 BLOCKED，写进 `docs/STAGE1c_REVIEW.md`，建议回 S1a 重新设计 prompt（不在 S1c 凑数）。

## 独立审查协议（S1c 完成后必须执行）

S1c 完成后，**派一个独立 subagent**（不审自己写的代码）执行以下检查，输出 `docs/STAGE1c_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **prompt 加固真实性**：`extraction.py` 的 v3 prompt 真的加了 fact_slot required 约束（grep "required" / "fact_slot" 在 prompt 字符串里）；`_extract_single` 的 retry 真的捕获 fact_slot ValidationError；抽查 5 题 snapshot 看 `extractor_prompt_version == "event-extraction.v3"`。
3. **fact_slot 非空率前后对比**：S1b baseline = 48.2% / S1c 加固后 = X%；per-sample 分布对比（5 题前后）；"none" sentinel 占比（若 > 20%，标注为 prompt 仍需调）。
4. **可达性测试 sound**：reachability test 仍调真实 consolidation 函数（无 mock）；PASS 或 XFAIL 都算通过；snapshot 路径参数化正确（环境变量生效）。
5. **R3 未被碰**：`git diff src/evoeventmem/consolidation.py` 空；`_EventDraft` 仍无 `multi_valued` 字段；`consolidation.py` 的 `multi_valued` / `0.7` / `supersede_contradiction_min` 引用全为 S1a 前已存在。
6. **scope 边界守住**：`git diff --stat` 仅触及 `src/evoeventmem/extraction.py` + `tests/consolidation/test_etec_real_data_reachability.py`（仅路径常量）+ 可能新增的 `tests/extraction/test_fact_slot_required.py`；不碰 `consolidation.py` / `retrieval.py` / `router.py` / `benchmarks/longmemeval/run.py` / `benchmarks/mechanism/extraction_smoke.py` / `tests/benchmarks/test_extraction_smoke.py` / `configs/longmemeval/smoke5-mimo.toml`。
7. **未跑 reader**：`runs/s1c/smoke5/` 无 `answers.json` / `predictions.json` / `metrics.json`；`run.log` 含 "extraction-only" 字样。
8. **未引入新 overclaim**：`grep -rE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" docs/ src/` —— S1c 不应声称 SUPERSEDE > 0（5 题样本太小，S2 才是 50 题 statistically meaningful 的测量）；只能声称"fact_slot 非空率 ≥ 50%"或"v3 prompt 让 LLM 稳定产 fact_slot"。
9. **git 状态**：除 `runs/` 外工作区干净或变更可解释；HEAD 未被 S1c 推进（不擅自 commit）。
10. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；UTC-aware datetime；小纯函数 + ports；不 commit datasets / secrets / model weights / benchmark caches。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S1c 修复；CONDITIONAL PASS 可进 S2 但标注未决项。审查通过后才能进 S2。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建的文件 + 每个文件的变更摘要（1-2 行）。
2. **5 题 smoke 前后对比**：
   - S1b baseline：fact_slot 非空率 48.2%，per-sample [33.3% / 42.5% / 51.7% / 52.8% / 58.2%]
   - S1c 加固后：fact_slot 非空率 X%，per-sample [...]
   - "none" sentinel 占比 Y%（若适用）
   - retry 触发率（每次 extraction 平均 retry 次数）
3. **可达性测试结果**：PASS 或 XFAIL + 命中数（S1b baseline = 22 four-gate pairs，S1c 后 = N pairs）
4. **验收标准勾选**：13 条 acceptance criteria 逐条 ✅/❌/⚠️ + 验证命令输出
5. **独立审查结果**：`docs/STAGE1c_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现
6. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出
7. **异常/风险**（如有）：
   - mimo provider 不可用 → 走 fallback
   - fact_slot 非空率仍 < 50% → 走替代 fallback（重新评估 50% 门槛，路由到 S2）
   - v3 prompt 让 LLM 产 0 event → 回 Step 2 微调
   - "none" sentinel 占比 > 20% → prompt 仍需调
8. **commit 决策**：**不擅自 commit**——报告完成后询问用户是否 `git add -A && git commit && git push`。commit message 模板：`feat(s1c): strengthen S1a prompt to required fact_slot + retry on missing fact_slot (v3)`。

## 不做什么（防止 scope creep）

- 不开始 S2/S3/S4/S5（S1c 完成并 commit + 审查通过后才允许）。
- 不修 R3（`multi_valued` 过打）——S1c 的 scope 边界，AGENTS.md 反 fishing 规则约束。
- 不调 `supersede_contradiction_min=0.7` 阈值——反 fishing。
- 不跑 50 题 / 500 题 / reader——S2 的事，S1c 只跑 S1b 已有的 5 题 extractor。
- 不动 `src/evoeventmem/consolidation.py` / `retrieval.py` / `router.py`——主算法不碰。
- 不动 S1b 已落地的 `benchmarks/longmemeval/run.py` / `benchmarks/mechanism/extraction_smoke.py` / `tests/benchmarks/test_extraction_smoke.py` / `configs/longmemeval/smoke5-mimo.toml`——S1c 复用，不改。
- 不擅自 commit（询问用户）。
- 不声称 SUPERSEDE > 0——5 题样本太小，S2 才是 statistically meaningful 的测量。
- 不用 fake extractor 凑数——违反"真实数据 smoke"目的。
- 不调 stats 脚本计算逻辑让 fact_slot 看上去达标——若 < 50%，如实记录，走 fallback。
- 不在 S1c 反复调 prompt 第 N 次凑出 ≥ 50%——那是 fishing；调一次，结果出来后走 fallback 路径。

## 故障排查

| 问题 | 解决 |
|---|---|
| mimo provider HTTP 429 / 超时 | 重试 1 次；仍失败标 BLOCKED，不强行用 fake |
| `extraction_snapshot.json` 缺某题 | 检查 `--sample-ids` 是否传对；检查 `run.log` 的 LLM 错误 |
| fact_slot 非空率仍 < 50% | 走替代 fallback：在 STAGE1c_REVIEW.md 写明，路由到 S2 在 50 题上重新评估 50% 门槛 |
| "none" sentinel 占比 > 20% | prompt 仍需调（LLM 把事实句误判为非事实）；回 Step 2 加 1-2 个事实句 few-shot，重跑 |
| v3 prompt 让 LLM 产 0 event | 放宽 `fact_slot="none"` 接受条件、减少 few-shot 严格度，微调一次；若仍 0 event，标 BLOCKED |
| required `fact_slot` 让 mypy 红 | 用 `Field(min_length=1, max_length=128)` + `field_validator`；类型仍是 `str`（非 Optional） |
| ruff 红（validator 装饰器位置） | 把 `@field_validator("fact_slot")` 放在方法上方；导入 `field_validator` from pydantic |
| mypy 红（retry 计数类型） | 用 `int` 显式标注 `retries: int = 0`；不要用 `Optional[int]` |
| reachability test 找不到新 snapshot | 确认 `EEM_S1B_SNAPSHOT_PATH` 环境变量传对；或确认 test 路径参数化正确 |

## 预计时间

- 0.5-1 天，单窗口可完成（schema required + retry + prompt 微调 + 5 题 smoke 重跑 + 回归）。
- Step 1（侦察）30 分钟。
- Step 2（schema + prompt + retry）半天。
- Step 3（reachability test 路径参数化）30 分钟。
- Step 4（跑 5 题 smoke）半天（含 LLM 调用 + retry 增量等待）。
- Step 5-7（stats + reachability + 回归 + 扫描）半天。
- 独立审查 1-2 小时。

## 文献依据

- **LongMemEval §5.3** (arXiv:2410.10813, ICLR 2025)：reported positive recall (+9.4% recall@k) and QA (+5.4% accuracy) gains from "fact-augmented key expansion"——**注：具体百分比来自论文正文 §5.3，独立审查未从摘要验证，按定性"reported positive gains"采纳**。`fact_slot` 就是 extracted user fact；S1c 的 required fact_slot 是让 LLM 稳定产 fact-augmented key 的最小约束。
- **S1b 实测证据**（`docs/STAGE1b_REVIEW.md`）：v2 prompt 在 5 题上 fact_slot 非空率 48.2%（321/666），3/5 已 ≥ 50%，证明 prompt 改动方向对但约束不够强；S1c 加 required + retry 是把"对的方向"推到"稳定达标"。
- **spec fallback**（`docs/REMEDIATION_SPEC.md` Stage 1b fallback，lines 320-324）：明确路由"fact_slot < 50% → 回 S1a 修 prompt（required field + retry on missing fact_slot）"——S1c 是 fallback 的直接落地。
