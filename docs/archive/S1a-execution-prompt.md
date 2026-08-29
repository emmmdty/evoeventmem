# Stage 1a 执行提示词：修 ETEC 第一道闸门（R1 fact_slot + R1b valid_from，schema + prompt）

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `b60b38d`）刚完成 Stage 0 整改（诚信止血）：披露 test50-mimo（`full`=0.46 最差）、撤自评 9/10→8/10、改 headline baseline（vs `vector_rag` `full` 贵 41% 且 EM 更低）、写 `NEGATIVE_RESULT_DISCLOSURE.md`。独立审查 PASS（`docs/STAGE0_REVIEW.md`）。工作区 clean。

整改 spec v1.1（`docs/REMEDIATION_SPEC.md` Stage 1a，lines 112-170）定义本窗口：让 ETEC SUPERSEDE 分支的**第一道闸门 `_same_fact_slot` 在真实 LongMemEval 数据上第一次变得可满足**，并让 `_fact_effective_time` 拿到 `valid_from` 而非回落到粗粒度 `event_time`。这是 LongMemEval §5.3 "fact-augmented keys" 的直接落地。

**scope 边界（明确声明，不藏着）**：S1a **只修 R1/R1b，不修 R3**。审计 `docs/8of10_AUDIT.md:340` 把 R3（`multi_valued` 过打，占 SUPERSEDE-blocking 候选的 18/29=62%）归类为"borderline 调参凑数"，受 AGENTS.md 反 fishing 规则约束。**因此 S1a 后 SUPERSEDE 仍可能 = 0**——LLM 可能继续把单值 fact 过打成 `multi_valued=True`。S2 才是经验性测量 SUPERSEDE 实际触发数的阶段；**S1a 只保证"如果 LLM 没过打 multi_valued，逻辑上可达"，不保证"经验上 SUPERSEDE > 0"**。

**为什么是这一阶段**：当前所有"ETEC 无效"结论都建立在 SUPERSEDE 永远走不到的前提上——`src/evoeventmem/consolidation.py:876` 的 `_contradiction_score` 在 `multi_valued` / `_same_fact_slot` / `_same_fact_value` / 区间重叠四重 gate 全满足才非零，真实数据上四者永不同时满足。审计标"fixture 证明逻辑，不证明真实数据价值"。不修这个，S2 重跑只是更精确的 null。

### 已完成的前置工作

- S0 完成（commit `b60b38d`，独立审查 PASS，工作区 clean）。
- `docs/REMEDIATION_SPEC.md` v1.1 Stage 1a（lines 112-170）= 本窗口的 spec。
- 受控夹具 `benchmarks/experiments/fixtures/etec_stress_v1.json` 已显式带 `fact_slot` + `fact_value` + `valid_from`，consolidation 逻辑已能处理 → S1a 改的是 **extraction 侧**，不动 consolidation 决策树。
- v2/v2.1 实验已回退（生产保持 v1，PROMPT_VERSION=`event-extraction.v1`），diff 存档 `runs/mechanism/diagnostics/v2-v21-gap-closure-experiment.diff`（930 行）—— **本窗口应参考此 diff 避免重复设计**，但不要直接 `git apply`（v2/v2.1 当时还动了超出 S1a scope 的东西，且 R3 仍未闭合导致 SUPERSEDE=0，符合 S1a 预期不需要回退）。

### 关键约束（违反即 spec 失败）

- **只做 S1a，不开始 S1b/S2/S3/S4/S5**——S1a 完成并 commit + 独立审查通过后才允许进入下一阶段。
- **不修 R3（`multi_valued` 过打）**——S1a 的 scope 边界，AGENTS.md 反 fishing 规则约束。`consolidation.py:876` 的 `multi_valued` 短路保留，不碰。
- **不调阈值**——`supersede_contradiction_min=0.7` 不动；不动任何 weight profile；不动 retrieval budget。
- **不跑 5 题 smoke extraction**——那是 S1b 的事；S1a 只做 schema + prompt + fixture 回归。
- **不跑 50 题 / 500-run / 新 benchmark**——S1a 不消耗 LLM 配额。
- **不破坏既有契约**：`uv run pytest tests/consolidation tests/retrieval -q` + `uv run ruff check .` + `uv run mypy src` + `uv run python -m evoeventmem.cli smoke` 必须全绿。
- **不破坏 evidence provenance**——`evidence_refs` + `raw_turn_id` + `locator=chars=X:Y` 链不动；新加的 `fact_slot`/`valid_from`/`valid_until`/`fact_value` 是 **metadata 字段**，不替代 evidence。
- **不擅自 commit**——完成后报告变更清单，询问用户是否 commit + push。
- **不删既有 metadata 字段**——`extractor_prompt_version` / `source_dataset` / `source_sample_id` 保留，新字段加在它们旁边。
- **不声称 SUPERSEDE > 0**——S1a 只保证逻辑上可达，不保证经验上触发；任何"提升"/"可达"措辞属于 overclaim（S2 才测）。

## 执行步骤

### Step 1: 侦察（read-only，不改文件）

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -3   # 确认 HEAD 是 b60b38d 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）

# 确认 spec + 审计 + 失败实验存档都存在
test -f docs/REMEDIATION_SPEC.md && echo "spec OK"
test -f docs/8of10_AUDIT.md && echo "audit OK"
test -f runs/mechanism/diagnostics/v2-v21-gap-closure-experiment.diff && echo "v2/v2.1 diff OK"
test -f benchmarks/experiments/fixtures/etec_stress_v1.json && echo "etec_stress fixture OK"

# 定位 extraction prompt（spec 提示 prompts/ 但实际 inline 在 extraction.py——ls 确认）
ls prompts/
grep -n "PROMPT_VERSION\|event-extraction" src/evoeventmem/extraction.py | head

# 读 LLMEventExtractor 当前结构（PROMPT_VERSION='event-extraction.v1' 在 :641）
grep -n "class.*Extractor\|PROMPT_VERSION\|_EventDraft\|_build_memory\|extractor_prompt_version\|source_dataset\|source_sample_id" src/evoeventmem/extraction.py | head -40

# 读 consolidation 四重 gate + _fact_effective_time（确认 valid_from 是否已被支持）
grep -n "_contradiction_score\|_same_fact_slot\|fact_slot_key\|_fact_effective_time\|_same_fact_value\|_intervals_overlap\|valid_from" src/evoeventmem/consolidation.py | head -30

# 读 etec_stress fixture（已显式带新字段的样例，schema 参考）
head -80 benchmarks/experiments/fixtures/etec_stress_v1.json

# 读 v2/v2.1 失败实验 diff（参考，避免重复设计；不直接 apply）
head -120 runs/mechanism/diagnostics/v2-v21-gap-closure-experiment.diff
```

预期发现：
- `prompts/` 只有 `FIRST_CODEX_SESSION.md`；extraction prompt **inline 在 `src/evoeventmem/extraction.py`**。
- `extraction.py:641` 是 `PROMPT_VERSION = "event-extraction.v1"`（LLMEventExtractor）—— S1a 升到 `event-extraction.v2`。
- `extraction.py:998-1003` 是 `_build_memory` 当前只写 3 个 metadata 字段（`extractor_prompt_version` / `source_dataset` / `source_sample_id`）。
- `consolidation.py:876` 是 `_contradiction_score` 四重 gate 短路逻辑（不碰 multi_valued 短路）。
- `consolidation.py:880-885` 是 interval overlap 检查（在 `0.6 + ...` 公式 `:886` 之前）。
- `consolidation.py:943-946` 是 `_same_fact_slot` 要求 `metadata["fact_slot"]` 存在。
- etec_stress fixture 已有 `fact_slot`/`fact_value`/`valid_from`，可作为 schema 参考。
- v2/v2.1 diff 含 `_EventDraft` 加字段 + `_build_memory` 赋值 + few-shot 改动 —— 可参考但需对照 S1a scope 边界筛除超 scope 部分。

**关键判断**：侦察完后，确认 `_fact_effective_time`（consolidation 侧）**是否已支持从 metadata 读 `valid_from`**。审计 v2.1 笔记说 v2.1 只改了 extraction 的 `_build_memory`（设 `valid_from=event_time`），未改 consolidation → 暗示 `_fact_effective_time` 已支持 `valid_from`。**若是**，S1a 是纯 extraction + prompt 改动，不动 consolidation。**若否**，加 minimal 支持（让 `_fact_effective_time` 在 `valid_from` 非空时返回 `(valid_from, valid_until or +∞)` 开区间）—— 但**不碰 `multi_valued` 短路与 `0.7` 阈值**。

### Step 2: 设计 fact_slot schema（`src/evoeventmem/extraction.py` `_EventDraft`）

在 `_EventDraft`（或对应 dataclass）加四个字段：

- `fact_slot: str | None = None`（如 `"user_degree"`, `"user_job"`, `"user_location"`）—— LongMemEval §5.3 "extracted user facts" 直接对应。命名约定：snake_case；`user_*` 前缀用于用户属性，其他实体按 `entity_<name>` 前缀。
- `fact_value: str | None = None`（结构化值，便于 `_same_fact_value` 比较——审计 N1 指出 `_contradiction_score` 有第四道 gate `not _same_fact_value`）。
- `valid_from: datetime | None = None`（ISO8601，开放区间起点——解决 R1b）。
- `valid_until: datetime | None = None`（ISO8601，开放区间终点，未结束用 `None`）。

**语义约定**（务必写进 schema docstring）：
- 状态变化类事实（"我换了工作"）→ extraction 应产**两条 event**：旧值的结束 event（带 `valid_until`）+ 新值的开始 event（带 `valid_from`）。两条 event 的 `fact_slot` 相同、`fact_value` 不同、`valid_until`/`valid_from` 在同一时间点对接。
- 非状态类事实（偏好、一次性事件）→ `valid_from = event_time`，`valid_until = None`。
- 当 event 描述状态变化但只产一条 event 时（extraction 简化）：新 event 的 `valid_from` = 变化时间；旧值不产独立 event（S2 经验上测量这种简化频率，不在 S1a 修）。

**类型校验**：用 UTC-aware datetime（AGENTS.md 要求 UTC-aware datetimes）；schema 层加 validator（`fact_slot` 非空时 `fact_value` 也应非空；`valid_until` 非空时 `valid_from` 也应非空）。

### Step 3: 改 extraction prompt（inline 在 `src/evoeventmem/extraction.py` 的 LLMEventExtractor）

- 升 `PROMPT_VERSION`：`event-extraction.v1` → `event-extraction.v2`（v2/v2.1 实验已回退，v2 名字可用）。
- 加 few-shot 示例：用户说"我之前学计算机，后来转了金融" → 两条 event：
  - event A: `fact_slot=user_degree`, `fact_value=计算机`, `valid_until=<转业时间>`, `event_time=<断言时间>`
  - event B: `fact_slot=user_degree`, `fact_value=金融`, `valid_from=<转业时间>`, `event_time=<断言时间>`
- 加约束（写进 system prompt 或 prompt 主体）：状态变化类事实必须产 `fact_slot` + `fact_value` + `valid_from` + `valid_until`；非状态类（偏好、一次性事件）可不产 `valid_until`，但必须产 `fact_slot` + `fact_value` + `valid_from`（= `event_time`）。
- 保留现有 evidence 引用约束（`evidence_refs` + `locator` 必须产；provenance chain 不能破）。

### Step 4: 接线 extraction 填新字段（`src/evoeventmem/extraction.py` `_build_memory`，约 :998-1003）

`_build_memory` 当前只写 3 个 metadata 字段。改成同时写新 4 字段：

```python
metadata["fact_slot"] = draft.fact_slot
metadata["fact_value"] = draft.fact_value
metadata["valid_from"] = draft.valid_from.isoformat() if draft.valid_from else None
metadata["valid_until"] = draft.valid_until.isoformat() if draft.valid_until else None
```

**若 Step 1 侦察发现 `_fact_effective_time` 不支持 `valid_from`**：在 `consolidation.py` 加 minimal 支持——当 `valid_from` 非空时返回 `(valid_from, valid_until or +∞)` 开区间，否则保留现有 `event_time` point-interval 行为。**只动 `_fact_effective_time` 这一处**；不碰 `multi_valued` 短路（:876）、不碰 `0.7` 阈值（:399）、不碰 `0.6 + ...` 公式（:886）。

### Step 5: 确认 consolidation 决策树不变（read-only）

- `_same_fact_slot`（`consolidation.py:943-946`）已要求 `metadata["fact_slot"]` 存在——S1a 后此 gate 在真实数据上第一次可被满足。
- `_contradiction_score`（`:876`）四重 gate 逻辑保留——`multi_valued` 短路、`_same_fact_value` 检查、interval overlap 都不动。
- `_fact_effective_time` 在 Step 4 已优先用 `valid_from`（若需要）——R1b 闭合。

### Step 6: fixture 回归（`tests/consolidation/test_etec.py`，1598 行）

```bash
uv run pytest tests/consolidation/test_etec.py -v
```

要求：SUPERSEDE 在 fixture 上仍 4/12 触发（4 个 case：`stress_newer_supersedes_older` / `stress_stale_incoming_historical` / `stress_conflicting_evidence` / `stress_cross_session_consolidation`）。如果从 4/12 变成 0/12 或其他数字，**回滚 Step 4 的 `_fact_effective_time` 改动并调试**——fixture 的 fact metadata 已显式带 `valid_from`，新逻辑不应破坏 fixture 行为。

### Step 7: provenance + retrieval 回归

```bash
uv run pytest tests/retrieval/test_qemr.py -v
```

要求全绿——`evidence_refs` + `raw_turn_id` + `locator` 链未破。

### Step 8: 类型 + lint + smoke

```bash
uv run mypy src
uv run ruff check .
uv run python -m evoeventmem.cli smoke
```

要求全绿。如果 mypy 红了（schema 类型校验失败），看下面"S1a 验收失败的 fallback"。

### Step 9: 全局一致性扫描

```bash
# 确认新字段在 extraction 落地（>=4 处命中）
grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py   # >=4

# 确认没碰 multi_valued 短路与 0.7 阈值（R3 不修）
git diff src/evoeventmem/consolidation.py | grep -E "multi_valued|0\.7|supersede_contradiction_min"
# 上面应该无输出，或仅有 _fact_effective_time 的 valid_from 优先改动

# 确认没碰 retrieval budget / weight profile / router
git diff src/evoeventmem/retrieval.py src/evoeventmem/router.py
# 上面应该无输出

# 确认 prompts/ 改动可 review（如果改了 prompts/ 目录）
git diff prompts/ 2>/dev/null

# 确认没跑过 5 题 / 50 题 / 500 题（不应有新 runs/ 产物）
git status --short runs/ 2>/dev/null | head
# 上面应该无输出（runs/ 是 gitignored）

# 确认 PROMPT_VERSION 升到 v2
grep -n "PROMPT_VERSION" src/evoeventmem/extraction.py
# LLMEventExtractor 的 PROMPT_VERSION 应为 event-extraction.v2
```

## 验收标准（全部勾选才算 S1a 完成）

- [ ] `grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py` ≥ 4（schema 落地）
- [ ] `uv run pytest tests/consolidation/test_etec.py` 全绿（fixture SUPERSEDE 仍 4/12，4 个 case 名不变）
- [ ] `uv run pytest tests/retrieval/test_qemr.py` 全绿（provenance chain 未破）
- [ ] `uv run mypy src` 全绿（schema 类型校验通过）
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"
- [ ] `git diff src/evoeventmem/consolidation.py` 不含 `multi_valued` / `0.7` / `supersede_contradiction_min` 改动（R3 不修；仅允许 `_fact_effective_time` 的 `valid_from` 优先这一处）
- [ ] `git diff src/evoeventmem/retrieval.py src/evoeventmem/router.py` 为空（不碰 retrieval/router）
- [ ] `git status --short runs/` 无新产物（S1a 不消耗 LLM 配额）
- [ ] LLMEventExtractor 的 `PROMPT_VERSION` 升到 `event-extraction.v2`

## 验证命令（spec 复制）

```bash
grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py   # >=4
uv run pytest tests/consolidation tests/retrieval -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## S1a 验收失败的 fallback

**如果新 schema 让 `mypy` 或 `test_etec.py` 失败且无法在窗口内修复** → S1a 标 CONDITIONAL FAIL：

1. `git checkout` 回滚 schema 改动（保留侦察笔记）。
2. 把失败原因写进 `docs/STAGE1a_REVIEW.md`（含 mypy 错误信息 / 失败的 test 名 / 调试尝试）。
3. S0 的 disclosure 已覆盖这种 null 情况——项目仍是 negative-result 待修根因状态，不破诚信。
4. **不通过硬调阈值让测试 pass**——这是 AGENTS.md 反 fishing 规则。
5. 询问用户是否进 S1b（若 fallback 路径不影响 S1b 的 smoke 测试）或回 S0 重新评估。

## 独立审查协议（S1a 完成后必须执行）

S1a 完成后，**派一个独立 subagent**（不审自己写的代码）执行以下检查，输出 `docs/STAGE1a_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **schema 落地验证**：`grep -n "fact_slot\|valid_from\|valid_until\|fact_value" src/evoeventmem/extraction.py` ≥ 4 处，且每处是真实字段定义/赋值（不是注释或字符串）。
3. **fixture 行为不变**：`tests/consolidation/test_etec.py` 全绿且 SUPERSEDE 4/12 case 名不变（`stress_newer_supersedes_older` / `stress_stale_incoming_historical` / `stress_conflicting_evidence` / `stress_cross_session_consolidation`）。
4. **provenance 未破**：`tests/retrieval/test_qemr.py` 全绿；`evidence_refs` + `raw_turn_id` + `locator` 链抽查 3 处未动。
5. **R3 未被碰**：`git diff src/evoeventmem/consolidation.py` 不含 `multi_valued` / `0.7` / `supersede_contradiction_min` 改动；若 `_fact_effective_time` 被改，确认改动仅是 `valid_from` 优先分支，不影响 multi_valued 短路。
6. **scope 边界守住**：`git diff --stat` 仅触及 `src/evoeventmem/extraction.py`、`src/evoeventmem/consolidation.py`（仅 `_fact_effective_time` 一处，若需要）；不碰 `src/evoeventmem/retrieval.py`、`src/evoeventmem/router.py`、`benchmarks/`、`tests/mechanism/`、`configs/`。
7. **未跑新 run**：`git status runs/` 无新产物（S1a 不消耗 LLM 配额）。
8. **未引入新 overclaim**：`grep -r "显著提升\|significant improvement\|outperform\|SUPERSEDE 可达\|SUPERSEDE > 0\|supersede reachable" docs/ src/` —— S1a 不应声称 SUPERSEDE > 0（S2 才测）；只能声称"逻辑上可达"或"schema 落地"。
9. **git 状态**：除 `runs/` 外工作区干净或变更可解释；HEAD 未被 S1a 推进（不擅自 commit）。
10. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；UTC-aware datetime；小纯函数 + ports。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S1a 修复；CONDITIONAL PASS 可进 S1b 但标注未决项。审查通过后才能进 S1b。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建的文件 + 每个文件的变更摘要（1-2 行）。
2. **验收标准勾选**：10 条 acceptance criteria 逐条 ✅/❌ + 验证命令输出。
3. **独立审查结果**：`docs/STAGE1a_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现。
4. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出。
5. **异常/风险**（如有）：
   - schema 类型校验失败 → 走 fallback
   - fixture 行为变化（4/12 不再触发）→ 调试或回滚
   - v2/v2.1 失败实验的根因重现（R3 阻塞 SUPERSEDE）→ 写进 STAGE1a_REVIEW.md（**这是预期内**，不是 S1a 失败）
   - `_fact_effective_time` 需要 consolidation 侧改动 → 说明改动范围
6. **commit 决策**：**不擅自 commit**——报告完成后询问用户是否 `git add -A && git commit && git push`。commit message 模板：`feat(s1a): ETEC R1/R1b schema — fact_slot/valid_from/valid_until/fact_value in extraction + prompt v2`。

## 不做什么（防止 scope creep）

- 不开始 S1b/S2/S3/S4/S5（S1a 完成并 commit + 审查通过后才允许）。
- 不修 R3（`multi_valued` 过打）——S1a 的 scope 边界，AGENTS.md 反 fishing 规则。
- 不调 `supersede_contradiction_min=0.7` 阈值——反 fishing。
- 不跑 5 题 / 50 题 / 500 题 smoke——S1b/S2 的事，S1a 不消耗 LLM 配额。
- 不动 retrieval/router/benchmarks——S1a 只动 extraction + prompt（+ consolidation 的 `_fact_effective_time` 一处，若需要）。
- 不擅自 commit（询问用户）。
- 不直接 `git apply` v2/v2.1 diff——参考其设计，但 v2/v2.1 还动了超出 S1a scope 的东西；且 R3 仍未闭合导致 SUPERSEDE=0 是符合 S1a 预期的，**不需要因 SUPERSEDE=0 回退 S1a**。
- 不声称 SUPERSEDE > 0——S1a 只保证"逻辑上可达"，不保证"经验上触发"。

## 故障排查

| 问题 | 解决 |
|---|---|
| `grep -cE` < 4 | 检查 `_EventDraft` 4 字段是否都加、`_build_memory` 是否都赋值 |
| `test_etec.py` 红（4/12 → 0/12） | 回滚 Step 4 的 `_fact_effective_time` 改动，检查 `valid_from` 优先逻辑是否破坏了 fixture 的 point-interval 重叠；fixture 的 `valid_from` 已显式设置，新逻辑应与之兼容 |
| `test_qemr.py` 红 | 检查 `evidence_refs` 是否被新字段意外覆盖；provenance chain 不能破 |
| mypy 红 | 检查 datetime 类型是否 UTC-aware；`str \| None` 联合类型是否完整；validator 是否正确签名 |
| prompts 文件找不到 | extraction prompt 是 **inline 在 `src/evoeventmem/extraction.py`** 的 LLMEventExtractor（`PROMPT_VERSION` 在 :641），不在 `prompts/` 目录 |
| v2/v2.1 实验根因重现（R3 阻塞） | 这是**预期内**——S1a 后 SUPERSEDE 可能仍 0（R3 未修）；写进 `STAGE1a_REVIEW.md` 的 findings，不视为 S1a 失败 |
| `_fact_effective_time` 不支持 valid_from | 加 minimal 支持（开区间 `(valid_from, valid_until or +∞)`），不碰 multi_valued 短路与 0.7 阈值 |
| 独立审查 FAIL | 按审查指出的具体问题修复，再跑一次审查 |

## 预计时间

- 2-3 天，单窗口可完成（schema + prompt + 回归）。
- Step 1（侦察）30 分钟。
- Step 2-3（schema + prompt）1 天。
- Step 4（接线 `_build_memory` + 可能的 `_fact_effective_time`）半天。
- Step 6-8（回归 + 类型 + lint）半天到 1 天。
- 独立审查 1-2 小时。

## 文献依据

- **LongMemEval §5.3** (arXiv:2410.10813, ICLR 2025)：reported positive recall (+9.4% recall@k) and QA (+5.4% accuracy) gains from "fact-augmented key expansion" —— **注：具体百分比来自论文正文 §5.3，独立审查未从摘要验证，按定性"reported positive gains"采纳**。`fact_slot` 就是 extracted user fact。
- **LongMemEval §5.4**: "explicitly associate timestamps with facts" —— `valid_from`/`valid_until` 就是 timestamp-fact association（同上，定性接纳）。
