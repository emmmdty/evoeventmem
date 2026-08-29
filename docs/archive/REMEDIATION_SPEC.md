# EvoEventMem 整改 Spec（分阶段，每阶段一个执行窗口）v1.1

> **目的**：基于 2026-08-18 验收审计（详见 `docs/9of10_AUDIT.md` —— 自评 9/10 已降级为审计 8/10）的发现，把"工程合格但研究 thesis 被自有数据证伪"的项目，按"先止血、再修根因、最后定稿"三档分阶段整改。每个阶段是一个自包含任务，可在单个 chat 窗口内按 `AGENTS.md` 任务协议执行完毕。
>
> **整改后目标定位**：从"声称 ETEC+QEMR 提升准确率"改为"严谨诊断证据约束记忆系统的可达性与失效模式 + 可审计记忆基础设施"。这是数据真正支持的定位，且与 MemTrace (arXiv:2606.17328) 的核心发现一致——"long-term memory 的瓶颈是 evidence use，不是 retrieval"。
>
> **不做什么**：不补 honesty 脚注（已失败）；不在 ETEC 结构性不可达修复前跑 500-run；不再换模型；不擅自 commit（每阶段结束询问用户）；**不修 R3（`multi_valued` 过打）—— 审计 `9of10_AUDIT.md:340` 将其归类为"borderline 调参凑数"，受 AGENTS.md 反 fishing 规则约束，S1 只修 R1/R1b**。
>
> **v1.1 修订**（基于 `docs/REMEDIATION_SPEC_REVIEW.md` CONDITIONAL PASS）：降级 S1 overclaim（B1）；新增 M2 stale-judge 子阶段（B2）；修正 S4↔S2 依赖（B3）；补 replay/online、6m NA、judge bias 四个审计 gap（B4）；S1/S4 拆分（N5/N6）；新增 S5 分支 D（infra 失败，N7）；明确禁止 v2 vs deepseek 跨模型对比（N8）。详见末尾"修订历史"。

## 关键证据回顾（整改依据）

| 证据 | 数据 | 来源 |
|---|---|---|
| flagship `full` 在 50 题 MiMo run 上 EM=0.46，最差 | `runs/publication/m13-longmemeval-test50-mimo/summary.json` | 未被任何文档披露（S0 修复） |
| `full` 在 LoCoMo n=1986 上 EM=0.0634 < vector_rag 0.0861, p=0.000 | `runs/main/report/report.md` C01 | README 标题只提 token 节省 |
| 拆 ETEC（full→event_no_etec）+8 EM；拆 QEMR（full→etec）+6 EM | test50-mimo summary | 两贡献各自有害 |
| ETEC SUPERSEDE 真实数据 0/8 测量 + 0/24 外推 = 0/32 | `9of10_AUDIT.md:89,250`（Q1 修正"0/32"conflation） | 结构性不可达（R1/R1b/R3） |
| R1: extraction 不产 `fact_slot`；R1b: 只写 `event_time` 点不写 `valid_from`；R3: LLM 过打 `multi_valued`（占 62% blocker） | `src/evoeventmem/extraction.py`(0 grep 命中), `consolidation.py:876`(四重 gate: `not multi_valued` + `_same_fact_slot` + `not _same_fact_value` + `_intervals_overlap`) | S1a 修 R1/R1b；R3 不修（AGENTS.md 反 fishing） |
| QEMR 在每个 intent 都输给 FIXED_VECTOR | LoCoMo report §4 | 需诊断 router/weights/temporal 三因 |
| LongMemEval §5.3: fact-augmented keys +9.4% recall, +5.4% QA | arXiv:2410.10813 (ICLR 2025) | 直接支持 ETEC 修复方向 |
| LongMemEval §5.4: time-aware query expansion +6.8%~11.3% temporal | 同上 | 直接支持 QEMR 修复方向 |
| MemTrace: evidence 10x retrievable than missing | arXiv:2606.17328 | 支持 pivot 到 auditability |
| Mem0 graph memory 仅 +2% over base | arXiv:2504.19413 | 结构化记忆不一定提升（与本项目一致） |

## 文献依据

- **LongMemEval** (Wu et al., ICLR 2025, arXiv:2410.10813) — benchmark 本体 + 三大设计建议（round granularity、fact-augmented keys、time-aware query expansion）
- **Mem0** (arXiv:2504.19413) — 主竞品，graph memory 仅 +2%，警示结构化收益有限
- **LOCOMO** (arXiv:2402.17753) — legacy benchmark，本项目 M14 run 用
- **MemGPT** (arXiv:2310.08560) — OS 启发的记忆层级
- **MemTrace** (arXiv:2606.17328) — "evidence use > retrieval" —— pivot 到 auditability 的核心依据
- **TMA-NM / Memory Poisoning** (arXiv:2606.24322) — provenance/authority 角度
- **Filesystem-Based Memory** (arXiv:2607.26637) — "organization buys search economy, not better answers" —— 与本项目负面结果一致
- **CraniMem** (arXiv:2603.15642) — scheduled consolidation + utility pruning，ETEC 修复的对照设计

---

## Stage 0：诚信止血（0 代码，1 窗口）

**目标**：清零"系统性隐瞒"风险——把被现场翻 summary.json 就翻车的最致命问题先解决。不解决 S0 就推进后续阶段 = 在沙地上盖楼。

**为什么必须先做**：test50-mimo（n=50, 当前模型, FINALIZED 在 HEAD `e585d7e`）是项目最大的 finalized run，且和 9of10 验收文档同一天生成，却在所有叙事文档中缺席。这是面试官 / 审稿人一定会发现的反证。

**前置**：无。

### 步骤

1. **披露 test50-mimo**——在 `README.md`、`docs/EVALUATION.md`、`docs/STRONG_RESULTS_SMALL_SAMPLE.md`、`9of10_ACCEPTANCE.md` 各加一节 `## test50-mimo (n=50, mimo-v2.5, 2026-08-18)`，附完整指标表：

   ```
   | method         | EM    | token_f1 | tok/q   | p50 write ms |
   |----------------|-------|----------+---------+-------------|
   | no_memory      | 0.00  | 0.005    | 10.6    | -           |
   | full_context   | 0.00  | 0.011    | 4094.9  | -           |
   | vector_rag     | 0.56  | 0.810    | 4072.5  | 45          |
   | event_no_etec  | 0.54  | 0.726    | 4082.7  | 36          |
   | etec           | 0.52  | 0.706    | 4083.0  | 130,185     |
   | full (flagship)| 0.46  | 0.687    | 4080.9  | 130,185     |
   ```
   主动写明："`full` 是最差的记忆方法；拆 ETEC +8 EM，拆 QEMR +6 EM；ETEC write p50=130s 是 consolidation 开销。"

2. **撤自评 9/10**——`9of10_ACCEPTANCE.md` 重命名为 `8of10_AUDIT.md`；删除 Part 6 自续 8→9 段或改为"作者注：不改变审计 8/10 结论"；标题改 `8/10`。

3. **改 headline baseline**——`README.md`、`docs/RESUME_NARRATIVE.md`、`docs/INTERVIEW_KIT.md` 标题里的 "96.5% 节省" 全部改成 vs `vector_rag` 的真实数字：`full` 比 `vector_rag` 贵 41% 且 EM 更低。删除所有 vs `full_context` 的标题对比（保留在正文脚注里作为"trivial baseline 参考"）。

4. **把 LoCoMo `full`=0.0634 vs `vector_rag`=0.0861 (p=0.000)** 加进 `README.md` 的 results 表。

5. **修文档自相矛盾**：
   - M17 三处对齐（README ❌ / TASKS.md DONE / 代码实际存在）→ 统一为 "M17 implemented, not deployed"。
   - `TASKS.md` 补 O09 条目。
   - 删 `9of10_ACCEPTANCE.md` 里"产物 untracked 待 commit"的过期声明（git 已 clean）。
   - `INTERVIEW_KIT.md §1` "validated end-to-end" 改成 "evaluated end-to-end with null/negative result on flagship config"。
   - **6m run ETEC NA 声明（B4 / Gap 3）**：在 `docs/EVALUATION.md` 加 note："6m run 的 `ingestion.etec.actions` 为 NA（legacy field contract，未持久化 samples dir；deepseek-v4-flash 已停服，run 不可复现）。"

6. **写一份 `docs/NEGATIVE_RESULT_DISCLOSURE.md`**（200 字内）：声明"ETEC+QEMR 在 LongMemEval/LoCoMo 上都没提升准确率；整改 spec 见 `REMEDIATION_SPEC.md`；当前状态为 negative-result 待修根因"。

### 验收标准

- [ ] `grep -rl "test50\|m13-longmemeval-test50" docs/ README.md` 至少返回 5 个文件
- [ ] `grep -r "9of10\|9/10" docs/ README.md` 无残留（除历史引用外）
- [ ] `grep -rn "96.5%.*节省\|96.5% savings" docs/ README.md` 每条命中都附带 "vs full_context (trivial)" 注释
- [ ] `ls docs/8of10_AUDIT.md` 存在；`ls docs/9of10_ACCEPTANCE.md` 不存在
- [ ] `docs/NEGATIVE_RESULT_DISCLOSURE.md` 存在且 ≤200 字
- [ ] TASKS.md 含 O09 条目
- [ ] README results 表含 `full` 行且 EM=0.0634

### 验证命令

```bash
grep -rl "test50\|m13-longmemeval-test50" docs/ README.md | wc -l   # >=5
test ! -f docs/9of10_ACCEPTANCE.md && echo "renamed OK"
test -f docs/8of10_AUDIT.md && echo "new name OK"
wc -w docs/NEGATIVE_RESULT_DISCLOSURE.md   # <=200
grep -c "O09" TASKS.md   # >=1
uv run pytest tests/mechanism -q   # 不退化
uv run ruff check .
uv run mypy src
```

### 风险

- 文档大改可能引入新错字或链接断裂 → 用 `grep -r "9of10\|9/10"` 全局扫描确保一致。
- 删 9/10 可能影响简历叙事 → 这是必要的止血，否则面试现场翻车代价更大。

### 估时

- 0.5-1 天，单窗口可完成。

---

## Stage 1a：修 ETEC 第一道闸门（R1 fact_slot + R1b valid_from，schema + prompt）

**目标**：让 ETEC SUPERSEDE 分支的**第一道闸门 `_same_fact_slot` 在真实 LongMemEval 数据上第一次变得可满足**，并让 `_fact_effective_time` 拿到 `valid_from` 而非回落到粗粒度 `event_time`。这是 LongMemEval §5.3 "fact-augmented keys" 建议的直接落地。

**scope 边界（明确声明，不藏着）**：S1a **只修 R1/R1b，不修 R3**。审计 `9of10_AUDIT.md:340` 把 R3（`multi_valued` 过打，占 SUPERSEDE-blocking 候选的 18/29=62%）归类为"borderline 调参凑数"，受 AGENTS.md 反 fishing 规则约束。**因此 S1a 后 SUPERSEDE 仍可能 = 0**——LLM 可能继续把单值 fact 过打成 `multi_valued=True`。S2 才是经验性测量 SUPERSEDE 实际触发数的阶段；S1a 只保证"如果 LLM 没过打 multi_valued，逻辑上可达"。

**为什么是这一阶段**：当前所有"ETEC 无效"结论都建立在 SUPERSEDE 永远走不到的前提上——`consolidation.py:876` 的 `_contradiction_score` 在 `multi_valued` / `_same_fact_slot` / `_same_fact_value` / 区间重叠四重 gate 全满足才非零，真实数据上四者永不同时满足。审计标"fixture 证明逻辑，不证明真实数据价值"。不修这个，S2 重跑只是更精确的 null。

**前置**：S0 完成（诚信披露先于代码修复，避免"边修边隐瞒"）。

### 步骤

1. **设计 fact_slot schema**（`src/evoeventmem/extraction.py` + `prompts/event_extraction*.md`，需先 `ls prompts/` 确认目标文件名）：
   - 每条 event 必须产 `fact_slot: str`（如 `"user_degree"`, `"user_job"`, `"user_location"`）——LongMemEval §5.3 的 "extracted user facts" 直接对应。
   - 每条 event 必须产 `valid_from: ISO8601 | null` 和 `valid_until: ISO8601 | null`（开放区间用 null）——解决 R1b。
   - 当 event 描述状态变化（"我换了工作"）→ `valid_until` 设为旧值结束时间，新 event 的 `valid_from` 设为变化时间。
   - schema 加 `fact_value: str`（结构化值，便于 `_same_fact_value` 比较——审计 N1 指出 `_contradiction_score` 还有第四道 gate `not _same_fact_value`）。

2. **改 extraction prompt**（`prompts/`，先 `grep -rn "event-extraction" prompts/` 定位）：
   - 加 few-shot 示例：明确展示"用户说'我之前学计算机，后来转了金融'"→ 两条 event，同 `fact_slot=user_degree`，旧值 valid_until=转业时间，新值 valid_from=同时间，`fact_value` 分别为"计算机"和"金融"。
   - 加约束："状态变化类事实必须产 fact_slot + valid_from + valid_until；非状态类（偏好、一次性事件）可不产 valid_until。"
   - 保留现有 evidence 引用约束（provenance chain 不能破）。

3. **改 `_same_fact_slot` / `_contradiction_score`**（`src/evoeventmem/consolidation.py:869-886`）：
   - 现逻辑保留，但确认新 schema 让 `fact_slot` 真的被填。
   - 阈值 `supersede_contradiction_min=0.7` 暂不调（避免"调阈值刷分"嫌疑）。

4. **fixture 回归**：`tests/consolidation/test_etec.py`（1598 行）跑通——保证 SUPERSEDE 在 fixture 上仍 4/12 触发（逻辑没回归）。

### 验收标准

- [ ] `grep -n "fact_slot\|valid_from\|valid_until\|fact_value" src/evoeventmem/extraction.py` ≥4 处命中（schema 落地）
- [ ] `tests/consolidation/test_etec.py` 全绿（fixture SUPERSEDE 仍 4/12）
- [ ] `tests/retrieval/test_qemr.py` 全绿（provenance chain 未破）
- [ ] `uv run mypy src` 全绿（schema 类型校验通过）

### 验证命令

```bash
grep -cE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py   # >=4
uv run pytest tests/consolidation tests/retrieval -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

### S1a 验收失败的 fallback

**如果新 schema 让 `mypy` 或 `test_etec.py` 失败且无法在窗口内修复** → S1a 标 CONDITIONAL FAIL，回滚 schema 改动（`git checkout`），把失败原因写进 `docs/STAGE1a_REVIEW.md`，进 S0 的 disclosure 已经覆盖这种 null 情况。**不要为了通过验收硬调阈值**——这是 AGENTS.md 反 fishing 规则。

### 估时

- 2-3 天，单窗口可完成（schema + prompt + 回归）。

### 文献依据

- LongMemEval §5.3 (arXiv:2410.10813, ICLR 2025)：reported positive recall (+9.4% recall@k) and QA (+5.4% accuracy) gains from "fact-augmented key expansion" —— **注：具体百分比来自论文正文 §5.3，独立审查未从摘要验证，按定性"reported positive gains"采纳**。
- LongMemEval §5.4: "explicitly associate timestamps with facts" —— valid_from/valid_until 就是 timestamp-fact association。

---

## Stage 1b：ETEC 真实数据可达性 smoke + 单元测试

**目标**：在 S1a 的 schema 改动上，写**真实数据可达性测试**并跑 5 题 smoke 确认 LLM 真的产了新字段。

**前置**：S1a 完成（schema + prompt + fixture 回归通过）。

### 步骤

1. **5 题 smoke extraction**：固定取 LongMemEval 前 5 题（`e47becba 118b2229 51a45a95 58bf7951 1e043500`）跑 extraction（不跑 reader），dump `extraction_snapshot.json`。
2. **可达性测试 `tests/consolidation/test_etec_real_data_reachability.py`**：
   - 用上述 5 题的 extraction_snapshot 作为输入。
   - 断言"至少一对 event 命中 `multi_valued=False` + `_same_fact_slot=True` + `not _same_fact_value` + `_intervals_overlap=True` 的四重 gate"。
   - **fallback（B1 修复）**：如果 LLM 仍过打 `multi_valued=True`（R3 未修），该断言可能不满足。此时测试改成 **xfail 标记** + 打印"R3 阻塞：5 题中 N 对 event 满足前三 gate 但被 multi_valued=True 屏蔽"，记录 R3 命中率作为 S2 的输入。**不通过硬调阈值让测试 pass**。
3. **fact_slot 非空率统计**：写 `benchmarks/mechanism/extraction_smoke.py`，对 5 题 snapshot 统计 `fact_slot` 非空率、`valid_from` 非空率、配对 `valid_until` 出现率。

### 验收标准

- [ ] `tests/consolidation/test_etec_real_data_reachability.py` 存在，要么 pass（四重 gate 命中），要么 xfail 并打印 R3 阻塞统计
- [ ] `benchmarks/mechanism/extraction_smoke.py` 存在并跑通，输出 fact_slot 非空率
- [ ] 5 题 snapshot 的 `fact_slot` 非空率 ≥ 50%（容许非状态类事实不产，门槛低于 S0 v1.0 的 80%——独立审查 N4 指出 80% 无依据）

### 验证命令

```bash
# 跑 5 题 extraction smoke（不跑 reader，节省时间）
uv run python -c "
from pathlib import Path
import json
# 抽 5 题 question_id
data = json.loads(Path('data/raw/longmemeval/longmemeval_s_cleaned.json').read_bytes())
ids = [r['question_id'] for r in data[:5]]
print(' '.join(ids))
"
# 用上述 IDs 跑 extraction-only（参照 benchmarks/longmemeval/run.py 的 _process_sample 但只到 snapshot）
uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v
uv run python -m benchmarks.mechanism.extraction_smoke
```

### 估时

- 1-2 天。

### 风险

- **LLM 不稳定产 fact_slot**：MiMo 是推理模型，prompt 改了 LLM 可能仍不稳定产 fact_slot。缓解：schema 层加 required 校验，extraction 失败重试。
- **valid_from 时间戳缺失**：LongMemEval 的 session 时间戳是 `haystack_dates`，需确认 extraction 能正确把 session 时间传给 event。可能要改 `benchmarks/longmemeval/run.py` 的 corpus 构建。
- **R3 阻塞**：5 题可能全被 `multi_valued=True` 屏蔽，可达性测试 xfail。这是**真实结果，不是失败**——S2 用 50 题统计 R3 阻塞率，决定是否 pivot。

### 文献依据

- LongMemEval §5.3 (arXiv:2410.10813)：reported positive recall and QA gains from fact-augmented key expansion（具体百分比来自论文正文 §5.3，未从摘要独立验证，按定性 reported positive gains 接纳）。fact_slot 就是 extracted user fact。
- LongMemEval §5.4: "explicitly associate timestamps with facts" —— valid_from/valid_until 就是 timestamp-fact association（同上，定性接纳）。

---

## Stage 2：50 题重跑 + ETEC 可达性诊断（2 天）

**目标**：用 S1a 改过的 extraction 重跑 50 题，**第一次经验性测量 ETEC 在真实数据上的真实可达性**。这是决定 thesis 翻盘还是 pivot 的关键证据。

**前置**：S1a + S1b 完成；**S4b（vector_rag 延迟修复）必须先于 S2**（见 S4 拆分 + 依赖图修正），否则 S2 的 vector_rag 延迟数据与 v1 不可比，S5 的 v1-vs-v2 对比无效。

### 步骤

1. **新 run dir**：`runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`，clean git（S0 已 commit），`--run-dir` 新建 manifest，10 批并行（参照 `scripts/run50-parallel.sh`，但每批用独立 `--run-dir` 避免 manifest drift，详见 2026-08-18 执行记录）。
2. **不复用旧 cache**：extraction schema 变了，旧 chat cache 全失效。embedding cache 可复用（embedding 不变）。
3. **跑完合并 + finalize**：参照本次执行的 sub-run + merge 方案。
4. **诊断 ETEC 可达性**：
   - 统计 50 题的 ETEC actions 分布（ADD/MERGE/SUPERSEDE/REJECT）。
   - **关键问题：SUPERSEDE 从 0 变成多少？** 如果仍 0 → 进一步统计 R3 阻塞率（多少对被 `multi_valued=True` 屏蔽）。如果 R3 阻塞率 > 50%，pivot 到 negative-result 论文（S5 路径 A）且**不修 R3**（AGENTS.md 反 fishing）。如果 SUPERSEDE > 0 → 进 S3 测是否提升准确率，并触发 S3.4 的 M2 stale-judge（B2）。
   - 统计 `fact_slot` 非空率、`valid_from` 非空率、配对 `valid_until` 出现率（验证 S1a 是否真生效）。
5. **replay/online 一致性复核（B4 修复）**：在 v2 run 上重跑 `benchmarks/mechanism/replay.py`，对比 online `ingestion.etec.actions` 与 replay 重建值。审计 `9of10_AUDIT.md:74-78` 记录 v1 有 `4dfccbf8` online ADD 223/MERGE 1 vs replay ADD 210/MERGE 14 的发散（`replay.py:130-134` 的 `LinkCandidateGenerator` cache-miss）。v2 如仍发散，**记录为已知 limitation，不静默修复**——这是 auditability 角度的真实证据。
6. **准确率对比**：v2 run 的 `full` EM vs 本次（v1）的 0.46，vs `vector_rag`。**不要预先声明期望**——预注册的 negative-result 框架（`METHODOLOGY_CHANGE.md`）要求不 bias 结果解读。**禁止 v2 与 24 题 deepseek-v4-flash run 跨模型对比**（N8——AGENTS.md 禁止不等模型下的 benchmark 对比）。

### 验收标准

- [ ] `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json` 存在
- [ ] 50/50 samples，retrieval.jsonl 行数 = 50×4 = 200
- [ ] ETEC actions 报告生成（含 SUPERSEDE 数）
- [ ] `fact_slot` 非空率 ≥ 50%（与 S1b 一致；非硬 gate，仅作为"S1a 是否生效"的诊断信号——若低于 50%，回 S1a 修 prompt）
- [ ] `valid_from` 非空率 ≥ 50%（状态变化类事实应都产）
- [ ] v1 vs v2 `full` EM 对比表写入 `docs/EVALUATION.md`

### 验证命令

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
"
```

### 风险

- **MiMo 网关 429/403**：上次 50 run 被"Remote end closed"打掉一次。缓解：每批独立 run dir，失败批次用 `--resume-dir` 重跑。
- **embedding tunnel 串行**：vector_rag 上次 p50=437s。缓解：S4 修了再跑，或这次并行度降到 5。
- **SUPERSEDE 仍 0**：这是真实可能——extraction 产了 fact_slot 但 LLM 仍过打 multi_valued（R3 未修）。这种情况下 ETEC 在真实数据上确实无 surface，必须 pivot。

### 估时

- 1.5 天跑 run + 0.5 天诊断。

---

## Stage 3：QEMR 失效根因诊断 + 条件性 M2 stale-judge（3-5 天）

**目标**：诊断 QEMR 在每个 intent 都输给 FIXED_VECTOR 的三个可能根因（router 误分类 / weight profile 过拟 / temporal source 有害），按成本递增顺序排查。**若 S2 测出 SUPERSEDE > 0，本阶段还要跑 M2 stale-memory judge**（B2 修复——审计 `9of10_AUDIT.md:47-54` 的"结构性 null"防御在 SUPERSEDE>0 时立即失效）。

**前置**：S2 完成（用 v2 run 的数据诊断；如 S2 SUPERSEDE=0 决定 pivot，S3 仍要做——auditability 论文也要解释 QEMR 为何失败）。

### 步骤

1. **Router 准确率诊断**（最便宜；N9 修复——只产 confusion matrix，不改规则）：
   - `benchmarks/mechanism/router_diagnosis.py`：对 50 题的 query，用 gold intent label（LongMemEval 的 question_type 字段）vs `router.py` 的预测 intent，算 confusion matrix。
   - **scope 边界**：本步只**产出 confusion matrix 并写进诊断报告**。如果 router accuracy < 80%，**不**在本阶段改 `router.py` 规则——router 修改是独立任务，需先看 confusion 模式再决定改哪条 `_RELATIVE_RE`。S3.1 的产出是"confusion matrix + 一份 router 修改建议"，修改本身留到 S3 后的独立小任务。

2. **Weight profile 消融**（中等）：
   - 在 `retrieval.py` 加 ablation：`qemr_no_temporal`（temporal source 权重设 0）、`qemr_no_graph`、`qemr_uniform`（所有 source 等权）。
   - 在 50 题 v2 run 上跑这些 ablation（reader call 共享 cache，只改 retrieval weight）。
   - 对比 LoCoMo §9 的发现：`no_temporal`(0.3654) > `qemr`(0.3000) → 在 LongMemEval 上是否也成立？

3. **Embedding 模型对照**（最贵，谨慎）：
   - 用 `bge-large-en-v1.5` 或 `e5-large-v2` 重跑 50 题（只 vector_rag + full 两方法）。
   - 如果换 embedding 后 `full` 翻盘 → embedding 质量是瓶颈，QEMR 本身没问题。
   - 如果换 embedding 后 `full` 仍输 → QEMR 设计本身有问题，需要重新设计 weight profile 或简化成 FIXED_VECTOR。

4. **M2 stale-memory judge（条件性，B2 修复）**：
   - **前置条件**：仅当 S2 测出 SUPERSEDE > 0 时执行。若 SUPERSEDE=0，跳过本步——审计的结构性 null 防御仍成立。
   - 跑 stale-judge 评测 v2 run 的 `full` vs `event_no_etec`：抽取 `full` 命中但 `event_no_etec` 未命中的 sample（SUPERSEDE 触发的，理论上旧值应被替换），让 judge 模型判定 `full` 的答案是否更新、`event_no_etec` 是否给了 stale 值。
   - **judge 模型 ≠ reader 模型**（B4 / N8——AGENTS.md 要求 documented judge model，且审计 `9of10_ACCEPTANCE.md:248` 标记 deepseek 同源 bias 风险）：reader 是 mimo-v2.5，judge 用 `minimax-m3` 或其他不同族模型；若 quota 不允许，**显式声明 M2 未跑 + 注明"auditability thesis 在 SUPERSEDE>0 时缺 M2 支撑，是 known weakness"**，不静默跳过。

5. **写诊断报告** `docs/QEMR_FAILURE_DIAGNOSIS.md`：明确 QEMR 失败的根因（router / weights / embedding / temporal source）+ M2 结果或未跑声明，并给出修复建议或 pivot 建议。

### 验收标准

- [ ] `benchmarks/mechanism/router_diagnosis.py` 存在且跑通，产出 confusion matrix
- [ ] router confusion matrix + 修改建议（非修改本身）写入 `docs/QEMR_FAILURE_DIAGNOSIS.md`
- [ ] 至少 2 个 weight ablation 跑完（no_temporal, uniform）
- [ ] embedding 对照实验完成或显式声明"因成本跳过，留 S5 决定"
- [ ] M2 子阶段：要么跑完且 judge 模型 ≠ reader 模型，要么显式声明"未跑 + SUPERSEDE>0 下的 auditability weakness"
- [ ] 诊断报告含明确根因结论（注：此项是 human-judgment process item，不列入"验证命令"——N10）

### 验证命令

```bash
uv run python -m benchmarks.mechanism.router_diagnosis --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot
uv run pytest tests/mechanism -q
uv run ruff check .
uv run mypy src
```

### 风险

- **ablation 跑次多**：5 个 weight 配置 × 50 题 = 250 reader call，但 cache 命中后只算 retrieval 差异。控制成本在 1 天内。
- **embedding 重跑贵**：要重新 embedding 所有 chunk。先确认 S3.1/S3.2 不够再启动。

### 估时

- 3 天（含 embedding 对照则 5 天）。

### 文献依据

- LongMemEval §5.4: time-aware query expansion +6.8%~11.3% temporal reasoning —— 如果 router 把 temporal 类 query 分错，QEMR 的 temporal weight 就完全错位。
- Filesystem-Based Memory (arXiv:2607.26637): "no agent converts organization into better answers" —— 警示 QEMR 的 query-adaptive 可能在 LongMemEval 上无 surface。

---

## Stage 4a：可复现性 config + docs（1 天，无代码）

**目标**：把"私有网关 + SSH tunnel + 未追踪 .env"的可复现性风险清零。让审稿人 / 面试官能拿到代码就跑起来。

**前置**：可与 S1/S2/S3 并行（纯文档 + config，无代码依赖）。

### 步骤

1. **`.env.example` 补全**：
   - 加 `OPENAI_BASE_URL`、`EEM_EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 字段（值留空，加注释说明用途）。
   - 加注释："生产配置用 opencode.ai 网关 + 本地 embedding；离线复现用 `deterministic_fake` provider"。

2. **`deterministic_fake` 离线模式**：
   - 确认 `_artifact_class`（`benchmarks/longmemeval/run.py:1237-1240`）的 `deterministic_fake` 分支可用。
   - 加 `configs/longmemeval/offline10.toml`：provider=deterministic_fake, sample_limit=10。
   - 跑通 → 文档里写"离线复现命令：`uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml`"。

3. **模型 pinning 文档**：
   - `configs/longmemeval/test50-mimo.toml` 的 `model_id = "mimo-v2.5"` 已 pin。
   - 文档加 note："24 题 finalized runs 用 deepseek-v4-flash（已停服，无法复跑，**禁止与 mimo-v2.5 run 跨模型对比**——AGENTS.md 不等模型下禁止 benchmark 对比）；50 题 run 用 mimo-v2.5。"

4. **6m run ETEC NA 声明（B4 / Gap 3 修复）**：在 `docs/EVALUATION.md` 加 note："6m run 的 `ingestion.etec.actions` 为 NA（legacy field contract，未持久化 samples dir；deepseek-v4-flash 已停服，run 不可复现）。"

5. **`.env` 不追踪验证**：`git ls-files .env` 必须空（已经如此）。

### 验收标准

- [ ] `grep -E "OPENAI_BASE_URL|EEM_EMBEDDING_BASE_URL" .env.example` 命中
- [ ] `configs/longmemeval/offline10.toml` 存在且 `uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml` 跑通
- [ ] `git ls-files .env` 输出空

### 验证命令

```bash
test -f configs/longmemeval/offline10.toml
uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/offline10.toml
git ls-files .env | wc -l   # 0
```

### 估时

- 1 天。

---

## Stage 4b：vector_rag 延迟代码修复（1 天，**必须先于 S2**）

**目标**：把 v1 run 里 vector_rag p50 search = 437,557ms (7.3 分钟) 的病态延迟修到 < 30s，否则 S2 的 v2 run 延迟数据与 v1 不可比，S5 的 v1-vs-v2 对比无效。

**前置**：无（可与 S1 并行，但**必须先于 S2 完成**——这是 B3 依赖修正）。

### 步骤

1. **定位瓶颈**：跑 1 题 vector_rag，用 `py-spy` 或 cProfile 抓 flamegraph。两个可能：
   - (a) `benchmarks/vector_baseline.py` 没用 `infra/async_embedding.py` 的 async batch 接口，串行 embedding。
   - (b) SSH tunnel 单 TCP 连接瓶颈，embedding server 端并发不够。
2. **修法 (a)**：改 `benchmarks/vector_baseline.py` 用 `async_embedding.batch_embed`，一次 batch 全部 chunk。
3. **修法 (b)**：本地起 embedding 服务（不走 tunnel），或 SSH tunnel 用 `ControlMaster auto` + 多连接。
4. **验收测**：跑 5 题 vector_rag，p50 search latency < 30s。

### 验收标准

- [ ] 5 题 vector_rag p50 search latency < 30,000 ms
- [ ] `benchmarks/vector_baseline.py` 或 `infra/async_embedding.py` 的改动通过 `tests/infra/` 现有测试
- [ ] provenance coverage 仍 100%（不能因批量化破坏 raw_turn_id 链）

### 验证命令

```bash
uv run python -c "
import json, statistics
from pathlib import Path
# 跑 5 题 vector_rag, 收集 search_latency_ms, 断言 p50 < 30000
"
uv run pytest tests/infra tests/retrieval -q
```

### 风险

- 批量化 embedding 可能改变 chunk 顺序导致 retrieval 结果微变——但 vector_rag 是 baseline，结果容差 ±1 EM。
- 如果 (b) 是主因，本地起 embedding 服务需要 GPU——如果拿不到，S2 用并行度=5 跑，并在 S5 显式声明"v2 vector_rag 延迟仍病态，与 v1 不可比"。

### 估时

- 1 天。

---

## Stage 5：定稿 + 论文 / 报告 draft（2 天）

**目标**：根据 S2/S3 的结果决定 thesis 走向，写定稿。

**前置**：S2、S3 完成。

### 分支决策

#### 分支 A：S2 SUPERSEDE 仍 = 0（或 R3 阻塞率 > 50%）（pivot 到 negative-result + auditability）

- thesis 改成："我们提出 ETEC（证据约束 consolidation）+ QEMR（query-adaptive retrieval），严谨评测显示两者在 LongMemEval/LoCoMo 上**结构性不可达 / 各自降低准确率**。我们诊断根因（extraction 不产 fact_slot → SUPERSEDE 不可达；QEMR weight profile 在每个 intent 上输给 fixed vector）。正面贡献：(1) 100% provenance coverage 基础设施，(2) 33/33 failure attribution，(3) 不可篡改 FINALIZED.json。"
- 投稿：workshop 类（NeurIPS W / ICML W 的 negative-results 或 reproducibility track）；或写成技术博客。
- 核心论点引用 MemTrace (arXiv:2606.17328): "evidence 10x retrievable than missing" → 本项目的 100% provenance 是真实贡献，准确率 null 是 honest finding。
- **M2 不需要跑**（SUPERSEDE=0 时结构性 null 防御成立）。

#### 分支 B：S2 SUPERSEDE > 0 且 `full` 翻盘（继续 positive thesis）

- thesis 保留："evidence-constrained consolidation 提升准确率"——但必须用 v2 run 的数据支持。
- 投稿：LongMemEval leaderboard + 一篇短文。
- 必须补跑 500-run（用修过的 extraction）确认效应稳定。
- **M2 必须跑**（B2——SUPERSEDE>0 时结构性 null 防御失效，M2 是 positive thesis 的必要证据；judge 模型 ≠ mimo-v2.5 reader）。

#### 分支 C：S2 SUPERSEDE > 0 但 `full` 仍输（中间路线）

- thesis："ETEC 的 SUPERSEDE 在真实数据上可达但**不足以提升整体准确率**——证据约束的 operating surface 在 LongMemEval 的 single-session-user 类上太窄。"
- pivot 到 auditability：用 SUPERSEDE > 0 证明逻辑可达，用准确率 null 证明真实场景 surface 窄。
- 这是最诚实的结果。
- **M2 必须跑**（同分支 B 的理由）。

#### 分支 D：S2 因 infra 失败未完成（N7 修复）

- 触发条件：S2 run 因 MiMo 429/403 或 embedding tunnel 失败无法在窗口内完成，且 `--resume-dir` 重试仍失败。
- thesis：回退到 S0 的 disclosure + auditability framing，**只用 v1 数据**（test50-mimo 50 题 + LoCoMo）。
- v2 重跑推迟到下个窗口。S5 写 `REMEDIATION_FINAL_REPORT.md` 时显式声明 "v2 未完成，本报告基于 v1 数据"。
- 不进入分支 B/C（无 v2 数据支持）。

### 步骤（任一分支）

1. 写 `docs/REMEDIATION_FINAL_REPORT.md`：含 v1 vs v2 对比表、ETEC 可达性诊断、QEMR 根因诊断、最终 thesis 定位。
2. 更新 `README.md`：用 v2 数据替换 v1 标题。
3. 更新 `docs/INTERVIEW_KIT.md`：把"validated end-to-end"改成符合最终 thesis 的诚实表述。
4. 更新 `docs/RESUME_NARRATIVE.md`：30 秒电梯陈述改成最终 thesis。
5. 如走分支 A：起草 `paper_draft.md`（negative-result short paper，4-6 页）。

### 验收标准

- [ ] `docs/REMEDIATION_FINAL_REPORT.md` 存在且含 v1 vs v2 对比
- [ ] README / INTERVIEW_KIT / RESUME_NARRATIVE 与最终 thesis 一致
- [ ] 独立审查通过（见下）

### 验证命令

```bash
test -f docs/REMEDIATION_FINAL_REPORT.md
grep -c "test50-mimo-v2" docs/REMEDIATION_FINAL_REPORT.md   # >=1
# 独立审查：见下方"独立审查协议"
```

### 估时

- 2 天（含论文 draft 则 4 天）。

---

## 独立审查协议（每阶段结束时执行）

每个阶段完成后，必须通过独立审查才能进入下一阶段。审查由独立 subagent 执行（不审自己写的代码），检查项：

1. **验收标准逐条勾选**：每条 ` acceptance criteria` 都有 `验证命令` 输出截图。
2. **未引入新的 overclaim**：`grep -r "显著提升\|significant improvement\|outperform" docs/` —— 任何新增的强 claim 必须有 p-value + CI 支撑。
3. **数据一致性**：文档里的数字与 `runs/.../summary.json` 实际数字一致（抽样 3 条核对）。
4. **git 状态**：除 `runs/`（gitignored）外，工作区干净或变更可解释。
5. **不破坏既有契约**：`pytest tests/mechanism -q` + `ruff check .` + `mypy src` + `evoeventmem.cli smoke` 全绿。
6. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；不擅自 commit。

审查输出 `docs/STAGE{N}_REVIEW.md`，含 PASS / CONDITIONAL PASS / FAIL 三档结论。FAIL 必须回到本阶段修复；CONDITIONAL PASS 可进下一阶段但标注未决项。

---

## 阶段依赖图（B3 修正：S4b 必须先于 S2）

```
S0 (止血) ──必须先于──> S1a (schema+prompt) ──> S1b (smoke+测试) ──> S2 (重跑诊断) ──┐
                                                                                     │
S4a (config/docs, 无代码) ──可与 S1/S2/S3 并行──────────────────────────────────────┤
                                                                                     │
S4b (vector_rag 延迟修复) ──必须先于 S2───┐                                        │
                                          │                                         │
                                          v                                         │
                                      S2 (v2 run) ──> S3 (QEMR+M2) ───────────────┤
                                                                                     │
                                                                S5 (定稿) <──────────┘
                                                                                     │
S2 结果 ──> SUPERSEDE=0 或 R3 阻塞>50%? ──YES─> S5 走分支 A (negative, M2 不跑)    │
              │                                                                     │
              NO                                                                    │
              ├─> S3 (QEMR + M2 必跑) ──────────────────────────────────────────────┤
              │                                                                     │
              └─> full 翻盘? ──YES─> S5 走分支 B (positive, M2 必跑, 补 500-run)   │
                              NO─> S5 走分支 C (中间, M2 必跑)                      │
                                                                                     │
S2 infra 失败? ──YES─> S5 走分支 D (回退 v1, 不进 B/C) ─────────────────────────────┘
```

---

## 总估时

| 阶段 | 估时 | 是否必须 |
|---|---|---|
| S0 | 0.5-1 天 | 必须 |
| S1a | 2-3 天 | 必须 |
| S1b | 1-2 天 | 必须 |
| S2 | 2 天（含 replay 复核） | 必须 |
| S3 | 3 天（含 M2 + embedding 对照 5 天） | 必须（至少 S3.1+S3.2+S3.4 or 声明 weakness） |
| S4a | 1 天 | 必须 |
| S4b | 1 天（**必须先于 S2**） | 必须 |
| S5 | 2 天（含 paper draft 4 天） | 必须 |
| **总计** | **12-15 天**（含可选实验 18-20 天） | |

注：S4b 与 S1a 可并行；S2 必须等 S1a+S1b+S4b 全部完成。

---

## 不做什么（防止 scope creep）

- 不重写 ETEC 算法本身（`_score_pair` 决策树保留）——S1a 只修 extraction schema 让第一道 gate 可满足。
- **不修 R3（`multi_valued` 过打）**——审计归类为 borderline 调参凑数，受 AGENTS.md 反 fishing 约束；S1a 明确声明此 scope 边界。
- 不重写 QEMR weight profiles——S3 先诊断（只产 confusion matrix + 修改建议）再决定。
- 不跑 500-run 直到 S2 结果出来且走分支 B。
- 不引入新 benchmark（不加 PersonalLMC、不加 MemoBench）——先把 LongMemEval/LoCoMo 跑诚实。
- 不加新 production feature（MCP dashboard、async API 等都不做）。
- 不擅自 commit——每阶段结束询问用户。
- **不做 v2 vs 24 题 deepseek 跨模型对比**（AGENTS.md 禁止不等模型下 benchmark 对比）；v1 vs v2 都是 mimo-v2.5，可对比。
- 不静默修 replay/online 发散——记录为 known limitation（auditability 角度的真实证据）。

---

## 引用文献清单（独立审查 N3：未标 ✓ 的为未独立验证的 arXiv ID，按 plausible 接纳；具体百分比若来自正文而非摘要，已在该处注明）

1. ✓ Wu et al. "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory." ICLR 2025. arXiv:2410.10813.（审查 webfetch 验证存在；§5.3/§5.4 具体百分比来自论文正文，未从摘要独立验证，spec 内已注明"按定性 reported positive gains 接纳"）
2. ✓ Chhikara et al. "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory." arXiv:2504.19413.（审查 webfetch 验证摘要"around 2% higher overall score"原句匹配）
3. Maharana et al. "Evaluating Very Long-Term Conversational Memory of LLM Agents." arXiv:2402.17753 (LOCOMO).（未独立验证，ID plausible）
4. Packer et al. "MemGPT: Towards LLMs as Operating Systems." arXiv:2310.08560.（未独立验证，ID plausible）
5. ✓ "MemTrace: Probing What Final Accuracy Misses in Long-Term Memory." arXiv:2606.17328.（审查 webfetch 验证摘要"evidence was retrievable 10 times more often than it was missing"原句匹配——这是 pivot 到 auditability 的核心依据）
6. "Securing LLM-Agent Long-Term Memory Against Poisoning." arXiv:2606.24322.（未独立验证，ID plausible）
7. "Filesystem-Based Memory for LLM Agents." arXiv:2607.26637.（未独立验证，ID plausible）
8. "CraniMem: Cranial Inspired Gated and Bounded Memory for Agentic Systems." arXiv:2603.15642.（未独立验证，ID plausible）

---

## 修订历史

- 2026-08-18 v1.0：初版，基于 2026-08-18 验收审计 + test50-mimo 实测发现。
- 2026-08-18 v1.1：基于 `docs/REMEDIATION_SPEC_REVIEW.md` CONDITIONAL PASS 修订：
  - **B1（S1 overclaim）**：降级 S1 标题为"让第一道闸门 `_same_fact_slot` 可满足"；明确声明不修 R3（AGENTS.md 反 fishing）；S1 拆成 S1a（schema+prompt）+ S1b（smoke+测试）；S1b 加 xfail fallback。
  - **B2（M2 未规划）**：S3 加 step 4 M2 stale-judge 子阶段，条件性（SUPERSEDE>0 时必跑，judge 模型 ≠ reader）；S5 分支 B/C 标"M2 必跑"。
  - **B3（S4↔S2 依赖矛盾）**：S4 拆成 S4a（config/docs 无代码）+ S4b（vector_rag 延迟代码修复）；依赖图改为 S4b 必须先于 S2。
  - **B4（四个审计 gap）**：S0 加 6m NA 声明；S2 加 replay/online 一致性复核（不静默修）；S3 加 judge 同源 bias 控制（B2 覆盖）；证据表"0/32"改为"0/8 测量 + 0/24 外推"。
  - **N1**：证据表加 `_contradiction_score` 第四道 gate `not _same_fact_value`。
  - **N3**：引用清单加 ✓ 标记 + 验证说明。
  - **N7**：S5 加分支 D（infra 失败回退）。
  - **N8**：S2 步骤 6 + S4a 步骤 3 明确禁止 v2 vs deepseek 跨模型对比。
  - **N9**：S3 step 1 scope 收窄为只产 confusion matrix + 修改建议，不本阶段改 router。
  - **N10**：S3 验收"明确根因结论"标注为 human-judgment process item，不列入"验证命令"。
