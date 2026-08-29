# Stage 0 执行提示词：诚信止血（S0，整改 spec v1.1）

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `b2d8942`）刚完成 50-run 验收 + 整改 spec v1.1（`docs/REMEDIATION_SPEC.md`，独立审查 PASS）。验收发现：项目工程合格但研究 thesis 被自有数据证伪——flagship `full` (ETEC+QEMR) 在 50 题 MiMo run 上 EM=0.46，是所有记忆方法里最差（`vector_rag`=0.56），且最大 finalized run `test50-mimo` 在所有叙事文档里**完全缺席**。

整改 spec 把 S0（诚信止血）定义为所有后续阶段的前置：不解决"系统性隐瞒"就推进代码修复 = 在沙地上盖楼。S0 是**纯文档改动，0 代码**，单窗口可完成。

### 已完成的前置工作

- `docs/REMEDIATION_SPEC.md` v1.1（6 阶段整改主 spec，独立审查 PASS）
- `docs/REMEDIATION_SPEC_REVIEW.md`（v1.0 审查，CONDITIONAL PASS，4 blocking + 10 non-blocking）
- `docs/REMEDIATION_SPEC_REVIEW_V1_1.md`（v1.1 增量审查，PASS）
- 50-run 已 FINALIZED 在 `runs/publication/m13-longmemeval-test50-mimo/`（commit `e585d7e`）
- 9of10 验收文档存在但需降级为 8of10

### 关键约束（违反即 spec 失败）

- **只做 S0，不开始 S1a/S1b/S2/S3/S4/S5**——S0 完成并 commit 后才允许进入下一阶段。
- **只改文档，不改 `src/` 代码**——S0 是诚信止血，零代码改动。
- **不擅自 commit**——完成后报告变更清单，询问用户是否 commit + push。
- **不修 R3（`multi_valued` 过打）**——这是 S1a 的 scope 边界，S0 不碰。
- **不调阈值、不调 budget、不调 prompt**——S0 不动任何算法配置。
- **不跑 500-run，不跑新 benchmark**——S0 只披露已有结果。
- **不破坏既有契约**：`pytest tests/mechanism -q` + `ruff check .` + `mypy src` + `evoeventmem.cli smoke` 必须全绿（S0 改文档不应触发代码退化，但作为 sanity check 必跑）。

## 执行步骤

### Step 1: 环境检查

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -3   # 确认 HEAD 是 b2d8942 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）
# 确认整改 spec 存在
test -f docs/REMEDIATION_SPEC.md && echo "spec OK"
test -f docs/9of10_AUDIT.md && echo "audit OK"
test -f docs/9of10_ACCEPTANCE.md && echo "acceptance OK"
# 确认 test50-mimo 数据源存在
test -f runs/publication/m13-longmemeval-test50-mimo/summary.json && echo "test50 summary OK"
test -f runs/main/report/report.md && echo "LoCoMo report OK"
```

### Step 2: 披露 test50-mimo（最关键）

`test50-mimo`（n=50, mimo-v2.5, FINALIZED 2026-08-18, git `e585d7e`）是项目最大的 finalized LongMemEval run，且和 9of10 验收文档同一天生成，但在所有叙事文档中缺席。这是面试官 / 审稿人一定会发现的反证——主动披露比被翻出来好 10 倍。

**在以下 4 个文档各加一节 `## test50-mimo (n=50, mimo-v2.5, 2026-08-18)`**，附完整指标表（数字必须与 `runs/publication/m13-longmemeval-test50-mimo/summary.json` 一致——执行前用以下命令核对）：

```bash
uv run python -c "
import json
from pathlib import Path
s = json.loads(Path('runs/publication/m13-longmemeval-test50-mimo/summary.json').read_text())
print('git_commit:', s['git_commit'], 'git_dirty:', s['git_dirty'])
print('reader:', s['reader_model'], 'extractor:', s['extractor_model'])
print()
print(f'{\"method\":15s} {\"EM\":>6s} {\"tokF1\":>6s} {\"evRec\":>6s} {\"tok/q\":>8s} {\"p50_q_ms\":>12s} {\"p50_w_ms\":>12s}')
for m in ['no_memory','full_context','vector_rag','event_no_etec','etec','full']:
    d = s['methods'].get(m, {})
    em = d.get('exact_match'); tf = d.get('token_f1')
    cats = d.get('categories', {})
    er = None
    if cats:
        for cv in cats.values(): er = cv.get('evidence_recall'); break
    if er is None: er = d.get('evidence_recall')
    eff = d.get('efficiency', {})
    tpq = eff.get('tokens_per_query'); p50q = eff.get('p50_search_latency_ms'); p50w = eff.get('p50_write_latency_ms')
    def f(x, w=6): return f'{x:>{w}.4f}' if isinstance(x,(int,float)) else f'{\"-\":>{w}s}'
    def fw(x, w=12): return f'{x:>{w}.1f}' if isinstance(x,(int,float)) else f'{\"-\":>{w}s}'
    print(f'{m:15s} {f(em)} {f(tf)} {f(er)} {f(tpq,8)} {fw(p50q)} {fw(p50w)}')
"
```

预期输出（数字必须匹配）：

| method         | EM    | token_f1 | evidence_recall | tokens/query | p50 search ms | p50 write ms |
|----------------|-------|----------|------------------|--------------|---------------|--------------|
| no_memory      | 0.00  | 0.0050   | 0.0000           | 10.56        | 0.0           | -            |
| full_context   | 0.00  | 0.0107   | 0.0000           | 4094.86      | 3.4           | -            |
| vector_rag     | 0.56  | 0.8105   | 1.0000           | 4072.50      | 437,556.8     | 45.1         |
| event_no_etec  | 0.54  | 0.7264   | 0.9800           | 4082.66      | 2,386.8       | 36.2         |
| etec           | 0.52  | 0.7060   | 0.9800           | 4083.00      | 2,340.3       | 130,185.2    |
| full (flagship)| 0.46  | 0.6869   | 0.9800           | 4080.92      | 2,339.1       | 130,185.2    |

**需修改的 4 个文档**：

1. **`README.md`**：在 results 表后加 `## test50-mimo (n=50, mimo-v2.5)` 节，附上表 + 一段诚实解读："`full` (ETEC+QEMR flagship) 是最差的记忆方法 (EM=0.46)，比 `vector_rag` (0.56) 低 10 个点；拆掉 ETEC（full→event_no_etec）反而 +8 EM，拆掉 QEMR（full→etec）反而 +6 EM——两贡献各自有害。ETEC write p50=130s 是 consolidation 开销。整改方案见 `docs/REMEDIATION_SPEC.md`。"

2. **`docs/EVALUATION.md`**：在 24-sample 结果节后加 `## test50-mimo (n=50, mimo-v2.5, 2026-08-18)` 节，附上表 + 同样的诚实解读 + 加注"v1 vs v2 跨模型对比禁止（24 题用 deepseek-v4-flash，50 题用 mimo-v2.5；AGENTS.md 禁止不等模型下 benchmark 对比）"。

3. **`docs/STRONG_RESULTS_SMALL_SAMPLE.md`**：在标题节后加 `## test50-mimo 补遗 (n=50, mimo-v2.5, 2026-08-18)` 节，附上表 + 注明"本 run 在 v1.0 spec 中被遗漏，v1.1 spec S0 步骤 2 补披露"。

4. **`docs/9of10_ACCEPTANCE.md`**（即将重命名，见 Step 3）：加 `## test50-mimo 补披露` 节，附上表 + 注明"该 run 在 9of10 验收文档中遗漏，独立审计 `9of10_AUDIT.md` Part 5 已列为诚实 gap；S0 步骤 2 补披露"。

### Step 3: 撤自评 9/10（保留审计的 8/10）

`docs/9of10_AUDIT.md` Part 5 独立审计给出 **8/10**（不是 9/10），列出 5 个诚实 gap（Q3 "96.5% 节省"被标"**不诚实**"；consistency.json 实际只有 1 行结构化但报告呈现 4 行表；fixture 不等于真实数据价值等）。同一文件的 "Part 6 continuation"（**自续**）把 8 抬到 9，这是 self-awarded 升分。

**操作**：

1. **重命名文件**：`git mv docs/9of10_AUDIT.md docs/8of10_AUDIT.md`（保留 git 历史）。
2. **`git mv docs/9of10_ACCEPTANCE.md docs/8of10_ACCEPTANCE.md`**。
3. **处理 Part 6 自续**：在 `8of10_AUDIT.md` 找到 "Part 6 continuation"（8→9 的自续段），在段首加注释："> **作者注（S0 整改）**：以下自续把审计 8/10 抬到 9/10，属 self-awarded 升分。整改 spec `docs/REMEDIATION_SPEC.md` 已决定保留审计的 8/10 结论，本段保留仅作历史记录，不改变审计结论。"
4. **标题改 8/10**：文件内所有 "9/10" 标题改成 "8/10（审计结论）"，9of10 字样改 8of10。
5. **全局引用更新**：`grep -rl "9of10\|9/10" docs/ README.md` 返回的每个文件，把对 `9of10_ACCEPTANCE.md` / `9of10_AUDIT.md` 的引用改成 `8of10_ACCEPTANCE.md` / `8of10_AUDIT.md`，把"9/10 验收"措辞改成"8/10 审计结论"。

### Step 4: 改 headline baseline（96.5% vs full_context → vs vector_rag）

当前 `docs/RESUME_NARRATIVE.md`（行 8, 11, 59, 107, 134）、`docs/STRONG_RESULTS_SMALL_SAMPLE.md`（行 43, 183）、`docs/9of10_ACCEPTANCE.md`（行 140）等把 "约省 96.5% 输入 token" 当 headline 卖点，但这是 vs `full_context`（trivial baseline，把全部历史塞进去）。公平 RAG baseline 是 `vector_rag`：`full`(200.3) 比 `vector_rag`(142.2) **贵 41% 且 EM 更低**。

**操作**：

1. **README.md**：标题里的"96.5% 节省"改成"vs vector_rag（公平 RAG 基线）：full 贵 41% 且 EM 更低"，把原"96.5% 节省"挪到正文脚注加注"(vs full_context trivial baseline)"。
2. **docs/RESUME_NARRATIVE.md**：行 8/11/59/107/134 的"96.5%"全部改写为"vs vector_rag（公平基线）：full 比 vector_rag 贵 41% 且 EM 更低；vs full_context trivial baseline 节省 96.5%（仅供参照）"。
3. **docs/STRONG_RESULTS_SMALL_SAMPLE.md**：行 43 已经诚实标注了"full 比 vector_rag 贵 41%"，但 headline 仍以"约省 96.5%"开头——把开头改成"vs vector_rag（公平基线）：full 贵 41% 且 EM 更低"，96.5% 挪到括号。
4. **docs/INTERVIEW_KIT.md**：检查并同样处理。
5. **保留在正文脚注**：所有"96.5%"不能删（是真实数字），但 headline 不再用它当卖点——必须主标 vs vector_rag 的真实对比。

### Step 5: 把 LoCoMo `full`=0.0634 vs `vector_rag`=0.0861 加进 README

`runs/main/report/report.md` 显示 LoCoMo n=1986 上 `full` EM=0.0634 < `vector_rag` 0.0861，C01: Δ +0.0227, 95% CI [+0.0141, +0.0312], p=0.000（full **显著更差**）。当前 README 完全没提 `full` 在 LoCoMo 上的准确率，只提 token 节省。

**操作**：在 `README.md` 的 results 表后加 `## LoCoMo (n=1986, legacy run)` 节，附下表：

| method         | exact_match | token_f1 | tokens/query |
|----------------|--------------|----------|--------------|
| full_context   | 0.0670       | 0.1507   | 4102.3       |
| vector_rag     | 0.0861       | 0.1873   | 142.2        |
| full (flagship)| 0.0634       | 0.1508   | 200.3        |

加注："C01: vector_rag vs full Δ +0.0227, 95% CI [+0.0141, +0.0312], p=0.000 — flagship `full` 显著劣于简单 vector_rag baseline。整改方案见 `docs/REMEDIATION_SPEC.md` S1a/S2/S3。"

### Step 6: 修文档自相矛盾

1. **M17 三处对齐**：`README.md` 行 153 "MCP 集成 ❌ 未实现（M17 TODO）" vs `TASKS.md` 行 26 "M17 DONE" vs `adapters/opencode/` 代码实际存在。统一为 "M17 implemented, not deployed"——README 改成 `| MCP 集成 | ⚠️ implemented, not deployed（`adapters/opencode/` 代码存在，未上线） |`，TASKS.md 保持 DONE 但加注 "(implemented, not deployed)"。

2. **TASKS.md 补 O09**：当前 TASKS.md 只列 O01-O08（行 36-37），但 `tasks/optional/O09_mechanism_evaluation.md` 实际存在且被执行（整个 9of10/8of10 effort）。在 TASKS.md 的 optional 表里加一行 `| O09 | [Mechanism evaluation](tasks/optional/O09_mechanism_evaluation.md) | DONE |`。

3. **删"产物 untracked 待 commit"过期声明**：`grep -rn "untracked\|待.*commit\|本周期不擅自提交" docs/9of10_ACCEPTANCE.md docs/9of10_AUDIT.md` 命中的地方——git 已 clean，全部 commit 了。改成"产物已 commit（`b2d8942` 及前序）"。

4. **INTERVIEW_KIT §1 "validated end-to-end"**（行 15）：改成 "evaluated end-to-end with null/negative result on flagship config——`full` 在 LongMemEval 50 题上 EM=0.46 最差，在 LoCoMo 1986 题上显著劣于 vector_rag（p=0.000）；机制级证据（provenance 100%、failure attribution 33/33）是真实贡献，但准确率 thesis 不被数据支持"。

5. **6m run ETEC NA 声明（spec B4 / Gap 3）**：在 `docs/EVALUATION.md` 加 note："6m run 的 `ingestion.etec.actions` 为 NA（legacy field contract，未持久化 samples dir；deepseek-v4-flash 已停服，run 不可复现）。"

### Step 7: 写 `docs/NEGATIVE_RESULT_DISCLOSURE.md`（≤200 字）

新建文件，内容模板（可调整措辞，但必须 ≤200 字 / 词）：

```
# Negative Result Disclosure

EvoEventMem 的两个研究贡献——ETEC（证据约束 consolidation）和 QEMR（查询自适应检索）——在 LongMemEval (n=50, mimo-v2.5) 和 LoCoMo (n=1986) 上都没提升准确率。flagship `full` (ETEC+QEMR) 在 50 题上 EM=0.46，是所有记忆方法里最差（vector_rag=0.56）；拆掉 ETEC 或 QEMR 各自都提升准确率。ETEC 的 SUPERSEDE 在真实数据上结构性不可达（extraction 不产 fact_slot/valid_from，0/8 测量 + 0/24 外推）。

整改 spec（`docs/REMEDIATION_SPEC.md` v1.1，独立审查 PASS）规划 6 阶段修复：S0 诚信披露 → S1a/S1b 修 ETEC 第一道闸门 → S2 重跑诊断 → S3 QEMR 失效根因 → S4 可复现性 → S5 定稿。当前状态：S0 执行中。正面贡献保留：100% provenance coverage、33/33 failure attribution、不可篡改 FINALIZED.json——记忆系统需要可审计，不只是准确。
```

### Step 8: 全局一致性扫描

```bash
# 残留 9of10 / 9/10 引用（除历史记录外应全部改 8of10）
grep -rn "9of10\|9/10" docs/ README.md | grep -v "REMEDIATION_SPEC" | head

# 96.5% headline 检查（每条命中都应附 "vs full_context (trivial)" 注释）
grep -rn "96.5%" docs/ README.md | head

# test50 出现在 >=5 个文档
grep -rl "test50\|m13-longmemeval-test50" docs/ README.md | wc -l   # >=5

# NEGATIVE_RESULT_DISCLOSURE 字数
wc -w docs/NEGATIVE_RESULT_DISCLOSURE.md   # <=200

# TASKS.md 含 O09
grep -c "O09" TASKS.md   # >=1

# 9of10 文件应不存在，8of10 应存在
test ! -f docs/9of10_ACCEPTANCE.md && echo "9of10_ACCEPTANCE renamed OK"
test ! -f docs/9of10_AUDIT.md && echo "9of10_AUDIT renamed OK"
test -f docs/8of10_ACCEPTANCE.md && echo "8of10_ACCEPTANCE OK"
test -f docs/8of10_AUDIT.md && echo "8of10_AUDIT OK"
```

### Step 9: 代码 sanity check（不应退化，但必跑）

```bash
uv run pytest tests/mechanism -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

四个命令必须全绿。S0 只改文档，如果这里红了，说明改动误触代码（回滚最近改动）。

## 验收标准（全部勾选才算 S0 完成）

- [ ] `grep -rl "test50\|m13-longmemeval-test50" docs/ README.md | wc -l` ≥ 5
- [ ] `test ! -f docs/9of10_ACCEPTANCE.md && test ! -f docs/9of10_AUDIT.md`（重命名生效）
- [ ] `test -f docs/8of10_ACCEPTANCE.md && test -f docs/8of10_AUDIT.md`
- [ ] `grep -rn "96.5%" docs/ README.md` 每条命中都附带 "vs full_context" 或 "trivial" 注释
- [ ] `wc -w docs/NEGATIVE_RESULT_DISCLOSURE.md` ≤ 200
- [ ] `grep -c "O09" TASKS.md` ≥ 1
- [ ] README 含 LoCoMo `full` EM=0.0634 行
- [ ] INTERVIEW_KIT §1 不再有 "validated end-to-end"（改成 "evaluated with null/negative result"）
- [ ] `uv run pytest tests/mechanism -q` 全绿
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run mypy src` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"

## 独立审查协议（S0 完成后必须执行）

S0 完成后，**派一个独立 subagent**（不审自己写的文档）执行以下检查，输出 `docs/STAGE0_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **数字一致性**：文档里的 test50-mimo 数字与 `runs/publication/m13-longmemeval-test50-mimo/summary.json` 实际数字一致（抽样 3 条核对）。
3. **未引入新的 overclaim**：`grep -r "显著提升\|significant improvement\|outperform" docs/ README.md` —— 任何新增的强 claim 必须有 p-value + CI 支撑。
4. **9of10 残留**：`grep -rl "9of10\|9/10" docs/ README.md` 除历史记录外应全部改 8of10。
5. **96.5% headline**：不再有未加注的 96.5% headline 卖点。
6. **git 状态**：除 `runs/`（gitignored）外，工作区变更可解释。
7. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；不擅自 commit。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S0 修复；CONDITIONAL PASS 可进 S1a 但标注未决项。

审查通过后才能进 S1a。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建/重命名的文件 + 每个文件的变更摘要（1-2 行）。
2. **验收标准勾选**：12 条 acceptance criteria 逐条 ✅/❌ + 验证命令输出。
3. **独立审查结果**：`docs/STAGE0_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现。
4. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出。
5. **异常/风险**（如有）：例如某个文档链接断裂、某个数字与 summary.json 不一致等。
6. **commit 决策**：**不擅自 commit**——报告完成后询问用户是否 `git add -A && git commit && git push`。commit message 模板：`docs(s0): honesty disclosure — test50-mimo, drop 9/10→8/10, fix baseline framing`。

## 不做什么（防止 scope creep）

- 不开始 S1a/S1b/S2/S3/S4/S5（S0 完成并 commit + 审查通过后才允许）。
- 不改 `src/` 代码（S0 是纯文档改动）。
- 不修 R3（`multi_valued` 过打）——S1a 的 scope。
- 不调阈值、不调 budget、不调 prompt。
- 不跑 500-run，不跑新 benchmark。
- 不擅自 commit（询问用户）。
- 不删 96.5% 数字（是真实的，只是不当 headline 卖点）。
- 不删 9of10 文件内容（保留历史，只重命名 + 加注释）。

## 故障排查

| 问题 | 解决 |
|---|---|
| `grep -rl "test50"` 返回 <5 | 检查 Step 2 的 4 个文档是否都加了节，README 也应包含 |
| 8of10 重命名后引用断裂 | `grep -rl "9of10"` 全局扫描，更新所有引用 |
| 96.5% 改不动（怕破坏原意） | 不删数字，只在 headline 处加 "vs vector_rag: full 贵 41% 且 EM 更低"，96.5% 挪括号 |
| pytest 红了 | S0 不应触代码，回滚最近的非文档改动 |
| NEGATIVE_RESULT_DISCLOSURE >200 字 | 删冗余，保留核心数字 + spec 引用 |
| 独立审查 FAIL | 按审查指出的具体问题修复，再跑一次审查 |

## 预计时间

- 0.5-1 天，单窗口可完成。
- Step 2（披露 test50-mimo）最耗时——要改 4 个文档，每个加一节 + 表 + 解读。
- Step 3（撤 9/10）次之——重命名 + 全局引用更新。
- 其他步骤各 10-20 分钟。
