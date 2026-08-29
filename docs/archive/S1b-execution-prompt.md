# Stage 1b 执行提示词：ETEC 真实数据可达性 smoke + 单元测试

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `162183c`）刚完成 Stage 1a：在 `src/evoeventmem/extraction.py` 给 `_EventDraft` 加 4 个可选字段（`fact_slot` / `fact_value` / `valid_from` / `valid_until`），加归一化与契约 validator；把 `LLMEventExtractor.PROMPT_VERSION` 升到 `event-extraction.v2`；扩 `_build_llm_prompt` 加 schema 字段说明 + `fact_slot_rules` + 4 个 few-shot 示例（含状态变化两-event 拆分）；改 `_build_memory` mirror 4 字段到 `metadata` 并设 `MemoryRecord.valid_from`/`valid_to`（让 consolidation 已有的 `_fact_effective_time` / `_interval` 闭合 R1b，零 consolidation 改动）。8 个新单元测试覆盖 schema/prompt/wiring/端到端可达性/负控制。独立审查 PASS（`docs/STAGE1a_REVIEW.md`）。工作区 clean，HEAD `162183c` = `feat(s1a): ETEC R1/R1b schema`。

整改 spec v1.1（`docs/REMEDIATION_SPEC.md` Stage 1b，lines 173-225）定义本窗口：在 S1a schema 落地之上，跑 LongMemEval 前 5 题（`e47becba 118b2229 51a45a95 58bf7951 1e043500`，已用 `data/raw/longmemeval/longmemeval_s_cleaned.json` 验证确实是前 5 条）的真实 extraction smoke，写**真实数据可达性测试**与**fact_slot 非空率统计**，第一次经验性确认 v2 prompt 真的让 LLM 产新字段，并测量四重 gate 在真实数据上是否命中。

**scope 边界（明确声明，不藏着）**：S1b **只测可达性与命中率，不修 R3**。S1a 后 SUPERSEDE 在逻辑上可达（schema 落地 + `_fact_effective_time` 用 `valid_from`），但经验上仍可能 = 0——LLM 可能继续把单值 fact 过打成 `multi_valued=True`（R3，审计 `docs/8of10_AUDIT.md:340` 占 SUPERSEDE-blocking 候选的 18/29=62%）。**S1b 的可达性测试必须内建 xfail fallback**：四重 gate（`multi_valued=False` + `_same_fact_slot=True` + `not _same_fact_value` + `_intervals_overlap=True`）不命中是**预期内结果**，不是失败——S2 才用 50 题统计 R3 阻塞率决定是否 pivot。

**为什么是这一阶段**：S1a 用受控夹具（`benchmarks/experiments/fixtures/etec_stress_v1.json`，显式带 fact metadata）证明 consolidation 逻辑链通；但**夹具不证明真实数据价值**（审计 N1）。S1b 第一次让 v2 prompt 与真实 LongMemEval 对话碰面，回答："LLM 真的产 `fact_slot`/`valid_from` 吗？真实对话里能不能找到至少一对命中四重 gate 的 event？" 若四重 gate 命中 → S2 重跑有理由相信会测到 SUPERSEDE > 0；若 R3 阻塞 → S2 直接测 R3 阻塞率并按 §S2 spec 决定 pivot。**S1b 不论结果都是赢**——它产出 S2 决策的关键诊断证据。

### 已完成的前置工作

- S0 完成（commit `b60b38d`，独立审查 PASS，诚信止血）。
- S1a 完成（commit `162183c`，独立审查 PASS，schema + prompt v2 落地）。
- 真实数据可达：`data/raw/longmemeval/longmemeval_s_cleaned.json` 共 500 题；前 5 题 ID 与 spec 写的固定 ID 一致（`e47becba 118b2229 51a45a95 58bf7951 1e043500`）。
- LongMemEval 实验 runner：`benchmarks/longmemeval/run.py`（`run_experiment` / `_process_sample` / `_snapshot_path` / `_write_run_root_artifacts`）。CLI 已支持 `--sample-ids` 选择题，已支持 `--config` 指定 provider/model，已写 per-sample `samples/<safe_id>.extraction_snapshot.json` 与 combined `extraction_snapshot.json`。
- 已有 mimo provider 配置 `configs/longmemeval/test50-mimo.toml`（extractor = `mimo-v2.5`，openai_compatible，base_url `https://opencode.ai/zen/go/v1`，`api_key_env = "OPENAI_API_KEY"`，`thinking = "disabled"`）—— S1b 复用此 provider，仅 sample 数从 50 缩到 5。
- 现有 etec_stress fixture（4/12 SUPERSEDE）与 S1a 端到端测试（`tests/extraction/test_event_extraction.py::test_fact_extraction_chain_reaches_supersede_on_real_extraction_output`）证明逻辑链通—— S1b 把"真实 LLM 输出"塞进这条链。

### 关键约束（违反即 spec 失败）

- **只做 S1b，不开始 S2/S3/S4/S5**——S1b 完成并 commit + 独立审查通过后才允许进入下一阶段。
- **不修 R3（`multi_valued` 过打）**——S1b 的 scope 边界，AGENTS.md 反 fishing 规则约束。`consolidation.py:876` 的 `multi_valued` 短路保留；`extraction.py` 的 `_EventDraft` 仍**不**含 `multi_valued` 字段（S1a 已确认）。
- **不调阈值**——`supersede_contradiction_min=0.7` 不动；不动任何 weight profile；不动 retrieval budget。
- **不跑 50 题 / 500-run / 新 benchmark**——S1b 只跑 5 题 extraction smoke；不跑 reader；不跑 retrieval；不消耗 reader LLM 配额（仅消耗 extractor 配额，最小化）。
- **不破坏既有契约**：`uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py -q` + `uv run ruff check .` + `uv run mypy src` + `uv run python -m evoeventmem.cli smoke` 必须全绿（与 S1a 一致）。
- **不破坏 evidence provenance**——`evidence_refs` + `raw_turn_id` + `locator` 链不动；新加的字段在 S1a 已落地，S1b 不再改 extraction.py 的 schema/prompt/wiring（除非 5 题暴露 prompt 必须修的真实 bug——此时回到 S1a 修，不在 S1b 凑数）。
- **不擅自 commit**——完成后报告变更清单，询问用户是否 commit + push。
- **xfail 不等于硬调阈值**：可达性测试 xfail 时**只**打印 R3 阻塞统计，**不**调阈值、**不**改 prompt 让 LLM 不产 `multi_valued=True`（那是 R3 修复，超出 S1b scope）。
- **不声称 SUPERSEDE > 0**——S1b 只在 5 题上经验测量"四重 gate 是否命中"，不声称"经验上 SUPERSEDE > 0"（5 题样本太小，S2 才是 50 题 statistically meaningful 的测量）；任何"提升 / outperform"措辞属于 overclaim。

## 执行步骤

### Step 1: 侦察（read-only，不改文件）

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -3   # 确认 HEAD 是 162183c 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）

# 确认 spec + S1a 审查 + 真实数据都存在
test -f docs/REMEDIATION_SPEC.md && echo "spec OK"
test -f docs/S1a-execution-prompt.md && echo "s1a prompt OK"
test -f docs/STAGE1a_REVIEW.md && echo "s1a review OK"
test -f data/raw/longmemeval/longmemeval_s_cleaned.json && echo "lme data OK"

# 确认 S1a 已落地 4 字段（应输出 >=4）
grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py

# 确认 PROMPT_VERSION 已升 v2
grep -n "PROMPT_VERSION" src/evoeventmem/extraction.py | head -3   # LLMEventExtractor 应是 event-extraction.v2

# 确认 R3 仍未修（multi_valued 字段不应在 _EventDraft）
grep -n "multi_valued" src/evoeventmem/extraction.py   # 期望: 无 _EventDraft.multi_valued 字段定义

# 读 spec S1b
sed -n '173,225p' docs/REMEDIATION_SPEC.md   # 或用 Read 工具读

# 读 LongMemEval runner 的 _process_sample / _snapshot_path（S1b 要复用）
grep -n "def _process_sample\|def _snapshot_path\|extraction_snapshot\|def _write_run_root_artifacts\|def run_experiment\|sample_ids\|argparse" benchmarks/longmemeval/run.py | head -25

# 读 mimo provider 配置（S1b 复用）
cat configs/longmemeval/test50-mimo.toml

# 确认 5 题真实数据 ID
uv run python -c "
import json
from pathlib import Path
data = json.loads(Path('data/raw/longmemeval/longmemeval_s_cleaned.json').read_bytes())
print('first 5:', [r['question_id'] for r in data[:5]])
"   # 应输出 ['e47becba', '118b2229', '51a45a95', '58bf7951', '1e043500']

# 读 ETEC 四重 gate + _fact_effective_time + _interval（S1b 可达性测试要复用）
grep -n "_contradiction_score\|_same_fact_slot\|_same_fact_value\|_intervals_overlap\|_fact_effective_time\|_interval\b" src/evoeventmem/consolidation.py | head -15

# 读 etec_stress fixture（4/12 SUPERSEDE，作为可达性测试的对照）
head -40 benchmarks/experiments/fixtures/etec_stress_v1.json
```

预期发现：
- `benchmarks/longmemeval/run.py` 没有现成的 `--extraction-only` 短路 flag——`_process_sample` 一次跑完 extraction + retrieval + reader。S1b 要么加 minimal `--extraction-only` flag（推荐，cleanest），要么写独立脚本调 `_process_sample` 前段（提取 + snapshot 写）。**只动这一处**，不动 retrieval / reader 主干。
- `benchmarks/mechanism/extraction_smoke.py` 不存在——S1b 新建。
- `tests/consolidation/test_etec_real_data_reachability.py` 不存在——S1b 新建。
- mimo provider 已配置（`OPENAI_API_KEY` 环境变量；base_url `https://opencode.ai/zen/go/v1`）。
- `_contradiction_score`（`consolidation.py:869-886`）四重 gate 逻辑保留；`_fact_effective_time`（`:770`）已支持 `memory.valid_from`；`_interval`（`:916`）已支持 `memory.valid_from`/`valid_to`。
- `_EventDraft` 不含 `multi_valued` 字段（S1a 确认）。

**关键判断**：侦察完后，确认 mimo provider 是否可用（`echo $OPENAI_API_KEY` 非空；网络可达 `https://opencode.ai/zen/go/v1`）。**若不可用**，S1b 标 BLOCKED，不强行用 fake extractor 凑数（fake extractor 不产真实 fact_slot，违反"真实数据 smoke"目的）。

### Step 2: 写 S1b 5 题 mimo 配置（`configs/longmemeval/smoke5-mimo.toml`）

复制 `configs/longmemeval/test50-mimo.toml` 改 4 处：

```toml
schema_version = "longmemeval.config.v1"
run_id_prefix = "m13-longmemeval-smoke5-mimo"
dataset_path = "data/raw/longmemeval/longmemeval_s_cleaned.json"
methods = ["no_memory", "full_context", "vector_rag", "event_no_etec", "etec", "full"]   # 保留全集；extraction-only flag 让 reader 不跑
provider = "openai_compatible"
max_input_tokens = 4096
max_extraction_tokens = 262144
max_candidates_per_source = 128
max_items_per_source = 8
sample_limit = 5   # 5 题（CLI --sample-ids 也限定；双保险）

[reader]
provider = "openai_compatible"
model_id = "mimo-v2.5"
base_url = "https://opencode.ai/zen/go/v1"
api_key_env = "OPENAI_API_KEY"
timeout_s = 120
thinking = "disabled"

[extractor]
provider = "openai_compatible"
model_id = "mimo-v2.5"
base_url = "https://opencode.ai/zen/go/v1"
api_key_env = "OPENAI_API_KEY"
timeout_s = 180
thinking = "disabled"
max_tokens = 65536

[embedding]
provider = "openai_compatible"
model_id = "qwen3-embedding-0.6b"
base_url = "http://127.0.0.1:11436/v1"
api_key_env = "EMBEDDING_API_KEY"
timeout_s = 60
```

**注意**：`methods` 保留全集不影响——`--extraction-only` flag（Step 3）会让 reader 不跑。

### Step 3: 加 `--extraction-only` 短路 flag（`benchmarks/longmemeval/run.py`）

在 `main()` 的 argparse 加：
```python
parser.add_argument(
    "--extraction-only",
    action="store_true",
    help=(
        "Stop after writing per-sample extraction snapshots; skip "
        "retrieval/reader/answer metrics. Used for S1b reachability smoke."
    ),
)
```

在 `_process_sample` 或 `run_experiment` 内部 short-circuit：snapshot 写完（`_snapshot_path` 存在）后，若 `args.extraction_only`（或 config flag），跳过 retrieval + reader，把 `methods` 留空 / 跳过 method 循环。**只动这一处控制流**，不改 retrieval / reader / metrics 主干；不动 `_write_run_root_artifacts` 的 snapshot 汇总（它本来就只读已存在的 snapshot）。

**fallback（若加 flag 风险大）**：写一个独立脚本 `benchmarks/mechanism/run_extraction_smoke.py`，直接调 `LLMEventExtractor` + `_process_sample` 的等价前段（构建 `ExtractionInput` → `extract` → 写 per-sample snapshot JSON），不进 runner 主干。**任选其一**，flag 是 cleanest。

### Step 4: 跑 5 题 extraction smoke

```bash
mkdir -p runs/s1b/smoke5
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/smoke5-mimo.toml \
  --sample-ids e47becba 118b2229 51a45a95 58bf7951 1e043500 \
  --extraction-only \
  --run-dir runs/s1b/smoke5 \
  2>&1 | tee runs/s1b/smoke5/run.log
```

**预期产物**（均在 `runs/s1b/smoke5/`，gitignored）：
- `samples/<safe_id>.extraction_snapshot.json` × 5（per-sample snapshot）
- `extraction_snapshot.json`（combined，5 条 record 数组）
- `run.log`（CLI stdout）
- `FINALIZED.json`（runner finalize marker——若 `--extraction-only` 也跳过 finalize，标 `incomplete` 或不写均可，但要在 run.log 里说明）

**失败处理**：
- HTTP 429 / 网络超时 → 重试 1 次；仍失败标 BLOCKED，不强行用 fake 凑。
- LLM 产 invalid JSON → runner 已有 3 次 retry（`extraction.py:669-690` `_extract_single`），不调；若 5 题有 0 个候选 event，记录在 `run.log` 并继续（snapshot 仍写空 events 列表）。

### Step 5: 写 fact_slot 非空率统计脚本（`benchmarks/mechanism/extraction_smoke.py`）

```python
"""S1b extraction smoke statistics: fact_slot / valid_from / valid_until 非空率。

读 combined `extraction_snapshot.json`，对 5 题的所有 extracted memory 统计：
- 总 event 数
- fact_slot 非空率（fact_slot is not None 的 event 数 / 总 event 数）
- valid_from 非空率
- 配对 valid_until 出现率（valid_until is not None 的 event 数 / 总 event 数）
- multi_valued metadata 出现率（应 ~0%——S1a 没加该字段；若 >0%，说明 LLM 在 metadata 里塞了 multi_valued——记录为 R3 信号）
- 同 fact_slot 不同 fact_value 的对数（潜在 SUPERSEDE 候选对数，未走 consolidation）

输出格式：JSON + 人类可读 stdout。CLI: `uv run python -m benchmarks.mechanism.extraction_smoke <run_dir>`
"""
```

实现要求：
- 纯函数：`load_snapshot(path) -> list[dict]`、`compute_stats(snapshots) -> dict`、`format_stats(stats) -> str`。
- 不依赖 consolidation / retrieval——只读 snapshot JSON。
- 写 unit test（`tests/benchmarks/test_extraction_smoke.py`）测 `compute_stats` 在 fixture 数据上输出预期形状（用静态 JSON 样例，不依赖真实 run）。

### Step 6: 写真实数据可达性测试（`tests/consolidation/test_etec_real_data_reachability.py`）

```python
"""S1b real-data reachability test: do the four-gate SUPERSEDE conditions
ever co-occur on real LongMemEval extraction output?

Gate contract (consolidation.py:869-886):
    multi_valued=False
    AND _same_fact_slot(source, target) is True
    AND not _same_fact_value(source, target)
    AND _intervals_overlap(source_start, source_end, target_start, target_end) is True

If the four gates co-occur on at least one pair within the 5-question
extraction snapshot, the test PASSES — SUPERSEDE is empirically reachable
on real LLM output, and S2 has reason to expect SUPERSEDE > 0.

If the four gates do NOT co-occur, the test is XFAIL with a printed
breakdown of which gate blocked the most pairs (R3 = multi_valued=True
over-flagging is the expected blocker; S1b explicitly does NOT fix R3).
This is a real negative result, not a failure — S2 measures the R3
block rate on 50 questions and decides whether to pivot.

This test does NOT claim SUPERSEDE > 0 empirically — 5 questions are
too small for a statistically meaningful trigger-rate claim. It only
checks reachability (does any pair satisfy all four gates?).
"""
```

实现要求：
- 读 `runs/s1b/smoke5/extraction_snapshot.json`（path 用 `pathlib.Path` 常量，若文件不存在 `pytest.skip("S1b smoke snapshot not generated; run benchmarks/longmemeval/run --extraction-only first")`）。
- 把 snapshot 里的 memory 反序列化成 `MemoryRecord`（用 `MemoryRecord.model_validate_json` 或 `model_validate`）。
- 枚举所有 (source, target) 对（5 题 × ~10 events/题 = ~50 events → ~2500 对，可枚举），对每对算四重 gate（直接调 `consolidation._same_fact_slot` / `_same_fact_value` / `_interval` / `_intervals_overlap`，或调 `ETECConsolidator._score_pair` 看 `decision.features` + `rule_hits`）。
- 断言：**至少一对**满足四重 gate。命中 → PASS；不命中 → `pytest.xfail`，stdout 打印："R3 阻塞：5 题中 N 对 event 满足前三 gate 但被 multi_valued=True 屏蔽（M 对总计）" + 各 gate 命中率统计。
- 不调 `supersede_contradiction_min=0.7` 阈值（那是 `consolidation._contradiction_score` 内部的事，可达性只看四重 gate 的合取）。

### Step 7: 跑可达性测试 + smoke 统计脚本

```bash
uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v
uv run python -m benchmarks.mechanism.extraction_smoke runs/s1b/smoke5
```

要求：
- 可达性测试 PASS 或 XFAIL（**两者都算 S1b 通过**——xfail 是预期内的 R3 阻塞，不是失败）。
- smoke 统计脚本跑通并打印 `fact_slot 非空率`。
- **fact_slot 非空率 ≥ 50%**（spec 验收标准 #3；容许非状态类事实不产，门槛低于 S0 v1.0 的 80%——独立审查 N4 指出 80% 无依据）。若 < 50%，回 S1a 修 prompt（**不在 S1b 调 prompt**——S1a 才管 prompt）。

### Step 8: 全套回归（与 S1a 一致）

```bash
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

要求全绿。新增的 `benchmarks/longmemeval/run.py --extraction-only` flag 不破坏现有 `tests/benchmarks/test_longmemeval_run.py`（若它跑的话——若该 suite 太慢不在 S1b 验收范围，可跳过并注明）。

### Step 9: 全局一致性扫描

```bash
# 确认 S1a 4 字段仍在（>=4）
grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py   # >=4

# 确认 R3 未碰（_EventDraft 仍无 multi_valued 字段）
grep -n "multi_valued" src/evoeventmem/extraction.py   # 期望: 无 _EventDraft.multi_valued 字段定义
grep -n "multi_valued\|0\.7\|supersede_contradiction_min" src/evoeventmem/consolidation.py | head -5   # 期望: 仅有现有 _memory_is_multi_valued / _is_multi_valued 调用，无新增改动

# 确认没碰 retrieval/router/weight profile/阈值
git diff --stat src/evoeventmem/retrieval.py src/evoeventmem/router.py 2>&1   # 期望空
git diff src/evoeventmem/consolidation.py   # 期望空

# 确认 S1b 没新增 overclaim
grep -rE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" docs/ src/ 2>&1 | grep -v "S1a-execution-prompt\|8of10_AUDIT\|REMEDIATION_SPEC\|S1b-execution-prompt" | head
# 期望: 无新增 overclaim（允许 S1b-execution-prompt.md 自身提到这些短语作为反 overclaim 规则）

# 确认 PROMPT_VERSION 仍 v2（S1b 不动 prompt）
grep -n "PROMPT_VERSION" src/evoeventmem/extraction.py | head -3   # LLMEventExtractor 应仍 event-extraction.v2

# 确认 runs/ 只多了 s1b/smoke5（gitignored，不进 commit）
git status --short runs/ 2>/dev/null | head   # 期望无输出（runs/ 是 gitignored）

# 确认 5 题 snapshot 真实生成
ls runs/s1b/smoke5/samples/*.extraction_snapshot.json | wc -l   # 期望 5
```

## 验收标准（全部勾选才算 S1b 完成）

- [ ] `configs/longmemeval/smoke5-mimo.toml` 存在（5 题 mimo 配置）
- [ ] `benchmarks/longmemeval/run.py` 加了 `--extraction-only` flag（或等价独立脚本 `benchmarks/mechanism/run_extraction_smoke.py`），不破坏现有 suite
- [ ] `runs/s1b/smoke5/extraction_snapshot.json` 存在并含 5 题 snapshot
- [ ] `benchmarks/mechanism/extraction_smoke.py` 存在并跑通，打印 `fact_slot 非空率 ≥ 50%`
- [ ] `tests/consolidation/test_etec_real_data_reachability.py` 存在，要么 PASS（四重 gate 命中），要么 XFAIL 并打印 R3 阻塞统计
- [ ] `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py -q` 全绿（S1a 回归不破）
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run mypy src` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"
- [ ] `git diff src/evoeventmem/consolidation.py` / `retrieval.py` / `router.py` 为空（不碰主算法）
- [ ] `git diff src/evoeventmem/extraction.py` 为空（S1b 不动 S1a 的 schema/prompt/wiring；若发现 prompt 必须修的真实 bug，回 S1a 修，不在 S1b 凑数）
- [ ] `git status --short runs/` 无 commit（runs/ 是 gitignored）
- [ ] LLMEventExtractor 的 `PROMPT_VERSION` 仍 `event-extraction.v2`（S1b 不动）
- [ ] 独立审查 PASS（`docs/STAGE1b_REVIEW.md`）

## 验证命令（spec 复制）

```bash
uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v
uv run python -m benchmarks.mechanism.extraction_smoke runs/s1b/smoke5
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## S1b 验收失败的 fallback

**如果 5 题无法跑出真实 LLM 输出（mimo provider 不可用 / 网络 / API key）** → S1b 标 BLOCKED：

1. 不强行用 fake extractor 凑数——fake extractor 不产真实 fact_slot，违反"真实数据 smoke"目的。
2. 把失败原因写进 `docs/STAGE1b_REVIEW.md`（含 HTTP 错误码 / 网络测试结果 / API key 状态）。
3. 已落地的代码（`--extraction-only` flag、`extraction_smoke.py`、`test_etec_real_data_reachability.py` 的 skip 守卫）仍 commit——但 `runs/s1b/smoke5/` 不存在，可达性测试 skip 而非 xfail。
4. 询问用户是否进 S2（若 mimo 恢复）或回 S0 评估 provider 可用性。

**如果 fact_slot 非空率 < 50%** → S1a prompt 在真实数据上未生效：

1. 不在 S1b 调 prompt——回 S1a 修 prompt（S1a 才管 prompt）。
2. 把 5 题 snapshot 的低 fact_slot 非空率证据写进 `docs/STAGE1b_REVIEW.md`，建议回到 S1a 加 prompt 强约束（如 required field + retry on missing fact_slot）。
3. 可达性测试 xfail（四重 gate 命中数为 0 是 fact_slot 缺失的下游结果）。

**如果四重 gate 不命中（R3 阻塞）** → 这是**预期内**，不是失败：

1. 可达性测试 xfail，打印 R3 阻塞统计。
2. S1b PASS（xfail 是预期路径）。
3. 把 R3 阻塞率写进 `docs/STAGE1b_REVIEW.md`，作为 S2 的输入（S2 用 50 题统计 R3 阻塞率，决定是否 pivot）。

## 独立审查协议（S1b 完成后必须执行）

S1b 完成后，**派一个独立 subagent**（不审自己写的代码）执行以下检查，输出 `docs/STAGE1b_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **5 题 snapshot 真实性**：`extraction_snapshot.json` 含 5 题；抽查 1 题 snapshot，确认其 events 数组里至少有 1 个 event 的 `metadata.extractor_prompt_version == "event-extraction.v2"`（确认 S1a v2 prompt 真的进了 LLM 调用）。
3. **fact_slot 非空率落地**：`extraction_smoke.py` 输出 ≥ 50%；抽查 1 题 snapshot 看 fact_slot 字段分布。
4. **可达性测试 sound**：读 `test_etec_real_data_reachability.py`，确认它枚举所有 (source, target) 对、调真实的 consolidation 函数（不是 mock）、PASS 或 XFAIL 都算通过、XFAIL 时打印 R3 阻塞统计（不是静默 skip）。
5. **R3 未被碰**：`git diff src/evoeventmem/consolidation.py` 不含 `multi_valued` / `0.7` / `supersede_contradiction_min` 改动；`_EventDraft` 仍无 `multi_valued` 字段。
6. **scope 边界守住**：`git diff --stat` 仅触及 `benchmarks/longmemeval/run.py`（仅 `--extraction-only` flag，若选 flag 路径）、`benchmarks/mechanism/extraction_smoke.py`（新建）、`configs/longmemeval/smoke5-mimo.toml`（新建）、`tests/consolidation/test_etec_real_data_reachability.py`（新建）、`tests/benchmarks/test_extraction_smoke.py`（新建，若加 unit test）；不碰 `src/evoeventmem/extraction.py`、`consolidation.py`、`retrieval.py`、`router.py`、`benchmarks/experiments/`、`tests/mechanism/`。
7. **未跑 reader**：抽查 `runs/s1b/smoke5/` 应无 `answers.json` / `predictions.json` / `metrics.json`（reader 没跑）；`run.log` 应含 "extraction-only" 字样。
8. **未引入新 overclaim**：`grep -rE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" docs/ src/` —— S1b 不应声称 SUPERSEDE > 0（5 题样本太小，S2 才是 50 题 statistically meaningful 的测量）；只能声称"四重 gate 命中"或"R3 阻塞率 N%"。
9. **git 状态**：除 `runs/` 外工作区干净或变更可解释；HEAD 未被 S1b 推进（不擅自 commit）。
10. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；UTC-aware datetime；小纯函数 + ports；不 commit datasets / secrets / model weights / benchmark caches。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S1b 修复；CONDITIONAL PASS 可进 S2 但标注未决项（如 mimo provider 不稳）。审查通过后才能进 S2。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建的文件 + 每个文件的变更摘要（1-2 行）。
2. **5 题 smoke 结果**：
   - 5 题 question_id 列表 + 每题 events 数 + 每题 fact_slot 非空率
   - combined snapshot 路径 + 总 event 数 + 全局 fact_slot/valid_from/valid_until/multi_valued metadata 非空率
3. **可达性测试结果**：PASS（四重 gate 命中）或 XFAIL（R3 阻塞）+ 阻塞统计（哪一 gate 屏蔽了多少对）
4. **验收标准勾选**：14 条 acceptance criteria 逐条 ✅/❌ + 验证命令输出
5. **独立审查结果**：`docs/STAGE1b_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现
6. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出
7. **异常/风险**（如有）：
   - mimo provider 不可用 → 走 fallback
   - fact_slot 非空率 < 50% → 回 S1a 修 prompt
   - R3 阻塞率 > 50% → 写进 STAGE1b_REVIEW.md，建议 S2 pivot
   - 端到端测试发现 S1a 的 schema/prompt/wiring 在真实数据上有 bug → 回 S1a 修
8. **commit 决策**：**不擅自 commit**——报告完成后询问用户是否 `git add -A && git commit && git push`。commit message 模板：`feat(s1b): real-data reachability smoke + fact_slot stats + xfail fallback for R3 block`。

## 不做什么（防止 scope creep）

- 不开始 S2/S3/S4/S5（S1b 完成并 commit + 审查通过后才允许）。
- 不修 R3（`multi_valued` 过打）——S1b 的 scope 边界，AGENTS.md 反 fishing 规则。
- 不调 `supersede_contradiction_min=0.7` 阈值——反 fishing。
- 不跑 50 题 / 500 题 / reader——S2 的事，S1b 只跑 5 题 extractor。
- 不动 `src/evoeventmem/extraction.py` 的 schema/prompt/wiring——S1a 才管；若 S1b 发现 prompt bug，回 S1a 修。
- 不动 `src/evoeventmem/consolidation.py` / `retrieval.py` / `router.py`——主算法不碰。
- 不擅自 commit（询问用户）。
- 不声称 SUPERSEDE > 0——5 题样本太小，S2 才是 statistically meaningful 的测量。
- 不用 fake extractor 凑数——违反"真实数据 smoke"目的；fake 不产真实 fact_slot。
- 不调阈值让可达性测试 PASS——xfail 是预期路径，硬调阈值是 fishing。

## 故障排查

| 问题 | 解决 |
|---|---|
| mimo provider HTTP 429 / 超时 | 重试 1 次；仍失败标 BLOCKED，不强行用 fake |
| `extraction_snapshot.json` 缺某题 | 检查 `--sample-ids` 是否传对；检查 `run.log` 的 LLM 错误 |
| fact_slot 非空率 < 50% | 回 S1a 修 prompt（不在 S1b 调）；把 5 题低非空率证据写进 STAGE1b_REVIEW.md |
| 四重 gate 不命中 | 这是**预期内**——xfail + 打印 R3 阻塞统计；S2 用 50 题测量 |
| `--extraction-only` flag 破坏现有 suite | 用独立脚本路径 `benchmarks/mechanism/run_extraction_smoke.py` 替代 |
| 端到端测试发现 S1a schema bug | 回 S1a 修；不在 S1b 凑数 |
| 独立审查 FAIL | 按审查指出的具体问题修复，再跑一次审查 |
| mypy 红 | 检查 `extraction_smoke.py` 的 stats dict 类型；检查 snapshot 反序列化的 `MemoryRecord` 字段类型 |
| ruff 红 | 修 import 排序；修 unused imports |

## 预计时间

- 1-2 天，单窗口可完成（5 题 smoke + 可达性测试 + 统计脚本）。
- Step 1（侦察）30 分钟。
- Step 2-3（配置 + flag）半天。
- Step 4（跑 5 题 smoke）半天（含 LLM 调用等待）。
- Step 5-6（统计脚本 + 可达性测试）半天。
- Step 7-9（回归 + 扫描）半天。
- 独立审查 1-2 小时。

## 文献依据

- **LongMemEval §5.3** (arXiv:2410.10813, ICLR 2025)：reported positive recall (+9.4% recall@k) and QA (+5.4% accuracy) gains from "fact-augmented key expansion" —— **注：具体百分比来自论文正文 §5.3，独立审查未从摘要验证，按定性"reported positive gains"采纳**。`fact_slot` 就是 extracted user fact；S1b 第一次在真实数据上测 LLM 是否产 fact_slot。
- **LongMemEval §5.4**: "explicitly associate timestamps with facts" —— `valid_from`/`valid_until` 就是 timestamp-fact association；S1b 测真实数据上 valid_from 非空率。
- **审计 N1（`docs/8of10_AUDIT.md`）**：fixture 证明 consolidation 逻辑，**不**证明真实数据价值——S1b 是补这块的关键窗口。
- **审计 N4（`docs/8of10_AUDIT.md`）**：S0 v1.0 的 80% fact_slot 非空率门槛无依据——S1b 用 50%（容许非状态类事实不产）。
