# S8: 代码工程质量与方法论修正 → 分层小样本预验证（500 前置门）

## 背景

S0→S7 闭环后，整改定稿（`docs/REMEDIATION_FINAL_REPORT.md`）声称"分支 C 中间路线"——ETEC SUPERSEDE 可达但不足以提升整体 EM。但 S3 §1 与 Limitation §1 已经诚实承认：**v2 的 n=50 切片是退化的统计样本**，全部来自 `single-session-user`（仅占 500 题真实分布的 14.0%）。

LongMemEval-S 500 题真实分布（`data/raw/longmemeval/longmemeval_s_cleaned.json`）：

| question_type | 500 题数 | 占比 | v2 切片 | ETEC 主场？ |
|---|---|---|---|---|
| multi-session | 133 | 26.6% | 0 | 部分（跨 session 答案） |
| temporal-reasoning | 133 | 26.6% | 0 | ✅ ETEC SUPERSEDE 主战场 |
| knowledge-update | 78 | 15.6% | 0 | ✅ ETEC SUPERSEDE 主战场 |
| single-session-user | 70 | 14.0% | 50 | ❌ 无时态答案可改 |
| single-session-assistant | 56 | 11.2% | 0 | ❌ |
| single-session-preference | 30 | 6.0% | 0 | ❌ |

**ETEC 的 SUPERSEDE 设计目标就是处理 temporal-reasoning + knowledge-update（211/500 = 42.2%），但 v2 在这两类上 0 测试**。S3 §4 M2 judge 在 single-session-user 上 74% tie 的"operating surface 窄"结论，**不能外推到 ETEC 主场**。

直接跑 500 题（`configs/longmemeval/main500.toml`）以"一致性验证"为名义（per `docs/METHODOLOGY_CHANGE.md`），但 `METHODOLOGY_CHANGE.md` 自己已承认 500 题的 MDE ±0.018–0.039 > observed 0.005–0.014，**"无显著性是预期结果"**。500 跑只是把 CI 缩窄、不改变方向；若 ETEC 主场方向未知，500 跑只是同分布放大、**纯浪费网关配额与 mimo-v2.5 token**（6 方法 × 500 题 × 4096 预算 ≈ 12M tokens）。

**S8 使命**：**n=100 分层小样本（与 500 同分布）即项目最终验证**——per-category 点估计无偏，足以定方法在主场的方向；500 题降级为 optional future-work（显著性确认，非项目主张所必需）。前置：修复阻碍 ETEC 在主场被公平测试的代码工程质量问题与方法论缺陷。

## 前置状态（新窗口必读，勿凭记忆）

### 工程质量缺陷（阻碍分层验证）

1. **Router 误路由**（S3 §1 已诊断，未修复）——`src/evoeventmem/router.py` 的 `_FACT_RE`/`_STRONG_FACT_RE`/`_KNOWLEDGE_UPDATE_RE`（commit `0ebbea1` + `21899e4` 已加，但**未覆盖 temporal-reasoning 与 multi-session 模式**）。500 题 router 准确率 38%（190/500）< 80% N9 阈值。temporal-reasoning 类样本会被误判到 HYBRID/SEMANTIC，权重错配导致 `full` 在主场被人为压低。
2. **IPv4 诊断 shim**（S3 STAGE3 review 标注的 unresolved risk）——`benchmarks/mechanism/router_diagnosis.py` 或邻近模块存在 monkeypatch，掩盖真实网络路径，须移除。
3. **Replay/online 不一致**——2/50 样本 ADD↔MERGE 重分类（`tests/mechanism/test_replay.py` 已知 limitation）。在分层小样本上若仍存在，会污染 SUPERSEDE 计数与配对显著性检验。
4. **大文件可读性**（非阻塞但影响审查可信度）——`src/evoeventmem/extraction.py` 62KB / `retrieval.py` 58KB；面试官若 grep 难定位。
5. **本地 38 commits ahead of origin/main**——非本任务范围（用户已决定不推 GitHub），但写文档时引用 commit 仍以本地 SHA 为准。

### 方法论缺陷（阻碍分层验证）

1. **采样偏差**——`benchmarks/longmemeval/run.py` 的 `--sample-ids` 接受任意 ID 列表，**无分层采样函数**。v2 的 50 题清单是手工挑的全 single-session-user，不是统计分层。
2. **无预注册 MDE**——`docs/METHODOLOGY_CHANGE.md` 把 500 题降级为"稳定性检查"时引用了 MDE ±0.018–0.039，但**未对分层小样本预注册决策规则**（什么样的结果可以把项目主张定位为 C+/C/D）。
3. **M2 judge 设计混淆**——S3 §4 的 minimax-m3 judge 在 31 个差异预测样本上跑，其中 74% 是 single-session-user（correctness 混淆 staleness）。**judge 应只在 temporal-salient 子集上跑**（temporal-reasoning + knowledge-update + multi-session 中预测不同的样本）。
4. **anti-fishing 边界模糊**——S2/S3/S5 不调 sentinel rate 与 router 规则，是 AGENTS.md "禁止 p-hacking"的执行；但**修 router 规则 / 修 extraction prompt 是 S5 之前就列为 future-work 的允许项**，须在 prompt 里明确边界，避免实施者误以为禁止修。

### 资源约束

- 网关配额：mimo-v2.5 reader/extractor，已稳定（S7 跑完 50 题）；ARK embedding server（qwen3-embedding-0.6b）在 `gpu-5090:11436`，SSH 隧道需 Step 0d 重连。
- judge 模型：minimax-m3 via ARK API（S3 §4 已用，31 cached calls 路径在 `<source-run>/m2_judge_cache/`）。
- 预算上限：reader/extractor 同 4096 token，embedding 同 qwen3-embedding-0.6b（AGENTS.md "禁止不等模型下 benchmark 对比"）。
- 数据集：`data/raw/longmemeval/longmemeval_s_cleaned.json` 已落地（gitignored）。

## 执行步骤

### Step 0: 验证前置状态 + 树立基线

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter

# 0a. 干净基线：所有验证绿
uv sync --extra dev
uv run ruff check . && uv run mypy src && uv run pytest -q
uv run python -m evoeventmem.cli smoke

# 0b. 确认 v2 baseline 数字与 REMEDIATION_FINAL_REPORT §2 一致
python3 -c "
import json
v2 = json.load(open('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json'))
print('v2 sample_validation:', v2['sample_validation'])
"

# 0c. 重连 embedding server（若已断）
curl -s --connect-timeout 5 http://127.0.0.1:11436/v1/embeddings -X POST \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-embedding-0.6b","input":"test"}' \
  | python3 -c "import sys,json; print('dim:', len(json.load(sys.stdin)['data'][0]['embedding']))"
# 不通则：
ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -f -N -L 11436:127.0.0.1:11436 gpu-5090
```

### Step 1: Router 规则补强（Phase A — 代码质量，阻塞门）

读 `src/evoeventmem/router.py` 与 `tests/retrieval/test_qemr.py`、`tests/fixtures/router/m11_query_router_fixture.json`。**只加正则与对应单测，不重构 router 架构**（O01 learned router 是 optional，不在本任务）。

需要补的模式（基于 LongMemEval-S 真实样本的句式扫描）：

1. **temporal-reasoning 模式**：
   - "when did/didn't/was/wasn't"
   - "how long ago" / "how many days/weeks/months/years ago"
   - "what time" / "on what date" / "in which month"
   - "last time" / "first time" / "most recent" / "earliest"
   - "before" / "after" + 时间锚点
2. **knowledge-update 模式**（S5 已列但未实施）：
   - "used to" / "previously" / "now" / "currently" / "has changed" / "no longer" / "switched from"
3. **multi-session 模式**（识别跨会话聚合）：
   - "across (all )?sessions" / "between conversations" / "in total" / "across all" / "combined"
   - "how many (different|total)" + 名词

**实施约束**：
- 每条新正则配 ≥3 个单测用例（含正例、近义负例、跨类干扰例）。
- 修改 `_TEMPORAL_STRONG_RE` / `_FACT_RE` 等现有正则时，先跑 `tests/retrieval/test_qemr.py` 与 `tests/mechanism/test_router_diagnosis.py`，确保不退化。
- 修完用全 500 题 deterministic router-only（无 LLM）重测准确率（参考 S3 §1 的脚本路径 `benchmarks/mechanism/router_diagnosis.py`）。

**Phase A 验收**：

| # | 标准 | 验证 |
|---|---|---|
| A1 | 全 500 题 router 准确率 ≥ 60%（从 38% 提升） | `uv run python -m benchmarks.mechanism.router_diagnosis --full-500` |
| A2 | temporal-reasoning 类 ≥ 70%（从 ~? 提升，需先测基线） | 同上，按 question_type 分组输出 |
| A3 | knowledge-update 类 ≥ 70% | 同上 |
| A4 | multi-session 类 ≥ 60% | 同上 |
| A5 | `tests/retrieval/test_qemr.py` 与 `tests/mechanism/test_router_diagnosis.py` 全绿 | pytest 输出 |

若 A1 < 60%：**不进入 Step 2**，回到 Step 1 加更多模式或回到 S3 §1 的 root-cause 报告 `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/router_diagnosis_report.md` 看误判样本。若两轮后仍 < 60%：写失败报告，建议转向 O01 learned router（optional task）。

### Step 2: IPv4 shim 移除 + Replay 一致性修复（Phase A 续）

```bash
# 2a. 定位 IPv4 monkeypatch（grep 全仓）
grep -rn "monkeypatch\|ipv4\|IPv4\|127.0.0.1.*patch\|socket.*patch" src/ benchmarks/ tests/ 2>/dev/null
```

读 `tests/mechanism/test_router_diagnosis.py` 与 `benchmarks/mechanism/router_diagnosis.py`、`benchmarks/mechanism/s2_diagnostics.py`。**移除所有诊断期临时 patch**，保留正式路径。

```bash
# 2b. Replay 一致性测试强化
uv run pytest tests/mechanism/test_replay.py -v
```

若 2/50 → 0/N 在分层样本上仍重分类，**找根因**（dict 顺序 / sort stability / 浮点比较）。

**Phase A 续验收**：

| # | 标准 | 验证 |
|---|---|---|
| A6 | grep `monkeypatch\|ipv4` 在 src/ 与 benchmarks/ 返回 0 行 | `grep -rn` 输出 |
| A7 | replay/online ADD↔MERGE 重分类率 = 0%（在新分层样本上） | replay 测试输出 |

### Step 3: 分层采样函数 + 预注册决策规则（Phase B — 方法论）

#### 3a. 实现分层采样

新建 `benchmarks/longmemeval/stratified_sample.py`（或在 `run.py` 内加 `--stratified-sample N` flag）。算法：

```python
def stratified_sample(n: int, seed: int = 42) -> list[str]:
    """
    按 500 题真实 question_type 分布比例分配 N 题。
    使用 largest remainder method 保证整数和 = N。
    """
    # 500 分布: multi-session 26.6%, temporal-reasoning 26.6%, knowledge-update 15.6%,
    # single-session-user 14.0%, single-session-assistant 11.2%, single-session-preference 6.0%
```

输出 ID 列表 + JSON manifest（含分层数、随机种子、source 500 hash）。**Manifest 提交进 git（不含题目内容，只含 ID 与分配）**——manifest 是预注册样本设计的证据。

#### 3b. 预注册决策规则

新建 `docs/S8-PREREGISTRATION.md`，明确：

| 结果条件 | 项目主张定位 |
|---|---|
| `full` > `vector_rag` 在 temporal-reasoning + knowledge-update 合并子集上 Δ ≥ +0.05 EM | **方法在主场方向正确**（branch C→C+）：项目主张升级为"ETEC 在主场小样本上方向正确、效应量达 X"；500 题 optional future-work 仅做显著性确认 |
| `full` ≈ `vector_rag`（\|Δ\| < 0.05）在合并子集上 | **方法在主场中性**（branch C 保持）：维持 v2 "可达但不足以提升" 定位；继续找其他 lever（embedding swap / sentinel prompt 调优）作为 future-work |
| `full` < `vector_rag` 在合并子集上 Δ ≤ −0.05 | **方法在主场也失效**（branch C→D 负结果）：项目主张改写为分支 D 负结果论文；ETEC 不再声称"可达"作为正面贡献 |
| Router 在合并子集准确率 < 70%（即使 Step 1 全局 ≥ 60%） | **Block**：路由本身不可信，先回 Step 1 修 router；不进入 Phase D |

**MDE 与统计功效**（n=100 分层样本即项目最终样本，α=0.05 双侧、power=0.8）：
- 合并子集 ~42 题（temporal-reasoning 27 + knowledge-update 16）：paired proportion MDE ≈ ±0.21。
- 整体 n=100：paired proportion MDE ≈ ±0.14。
- **决策不要求显著性，要求方向 + 效应量**——这是 `docs/METHODOLOGY_CHANGE.md` 已确立的小样本 pre-registered effect direction 合规框架。500 题即使跑也只是把 MDE 缩到 ±0.06–0.10，**仍不足以达到 LongMemEval 论文级别的显著性门槛**，所以 100 题足够定项目主张。

#### 3c. M2 judge 重设计

修改 `benchmarks/mechanism/m2_stale_judge.py`：

- judge 仅在 `temporal-reasoning + knowledge-update + multi-session` 子集的"预测不同"样本上跑。
- 不在 single-session-* 上跑（避免 correctness/staleness 混淆）。
- prompt 显式给 judge 看 gold answer 的"当前值 vs 旧值"对照（若 raw turns 里有 time-stamped 值变化）。

### Step 4: 分层 dry run（Phase C — 验证采样与 router，无 LLM）

```bash
# 生成 n=100 分层样本 manifest
uv run python -m benchmarks.longmemeval.stratified_sample --n 100 --seed 42 \
  --output configs/longmemeval/stratified100.toml.inc

# 只跑 router 决策（不跑 extraction/reader），看 per-category 路由准确率
uv run python -m benchmarks.mechanism.router_diagnosis \
  --sample-ids-file configs/longmemeval/stratified100.toml.inc \
  --output runs/diagnostic/s8-stratified-router-diagnosis.json
```

**Phase C 验收**：

| # | 标准 | 验证 |
|---|---|---|
| C1 | stratified100 manifest 存在，分配整数和 = 100 | cat manifest |
| C2 | 分层样本在 6 个 question_type 上分布与 500 比例 ±2 题 | per-category count |
| C3 | Router 在分层样本上整体准确率 ≥ 60% | diagnosis JSON |
| C4 | Router 在 temporal-reasoning + knowledge-update 合并上 ≥ 70% | per-category breakdown |
| C5 | 全部用例无 monkeypatch warning | grep 输出 |

若 C4 < 70%：回 Step 1 加模式；若两轮仍 < 70%：写失败报告，**不进入 Step 5**。

### Step 5: 分层 live run（Phase D — 项目最终验证，需用户审批后启动）

**此 Step 在 Phase C 全绿后，向用户出示审批包**：

- 命令：`uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/test50-mimo.toml --run-dir runs/publication/s8-stratified100 --sample-ids-file configs/longmemeval/stratified100.toml.inc`
- 预计时长：6 方法 × 100 题 × mimo-v2.5 ≈ 4–6 小时（视网关配额）
- 预计 token 成本：reader+extractor 共约 2.4M tokens（6 方法 × 100 题 × 4096 预算），按 mimo-v2.5 价格估算（写进审批包）
- 配置：复用 `configs/longmemeval/test50-mimo.toml`（同 mimo-v2.5、同 4096 预算、同 qwen3-embedding-0.6b——AGENTS.md N8 公平性硬约束）
- **此 run 即项目最终 benchmark**——不再有"500 题主跑"步骤；500 题降级为 optional future-work（见 §关键约束）

**审批通过后**：

```bash
set -a; source .env 2>/dev/null; set +a
PYTHONUNBUFFERED=1 nohup uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/test50-mimo.toml \
  --run-dir runs/publication/s8-stratified100 \
  --sample-ids-file configs/longmemeval/stratified100.toml.inc \
  > runs/publication/s8-stratified100.log 2>&1 &
```

断点续跑、失败样本如实记录（参考 S7 Step 1 的失败处理协议）。

**Phase D 验收**：

| # | 标准 | 验证 |
|---|---|---|
| D1 | ≥95/100 样本完成，sample_validation.valid=true | summary.json |
| D2 | 6 方法 × 6 类别 EM 矩阵（含 token_f1、evidence_f1、tokens/query、p50/p95 latency） | 新分析脚本输出 |
| D3 | `full` vs `vector_rag` 配对置换检验（per-category + overall） | `runs/analysis/s8-stratified100-stats.json` |
| D4 | M2 judge 在 temporal-reasoning + knowledge-update + multi-session 差异预测样本上跑完 | m2_judge_cache/ 计数 |
| D5 | extraction_snapshot.json、consolidation.jsonl、retrieval.jsonl、evidence.jsonl 全部 hash 锁定 | FINALIZED.json |
| D6 | FINALIZED.json 存在、artifact_class=PUBLICATION | runs/publication/s8-stratified100/finalized/FINALIZED.json |

### Step 6: 最终验证报告（Phase E — 反伪造机制）

新建 `docs/S8-STRATIFIED_VALIDATION_REPORT.md`，结构：

1. **执行摘要**：分层样本组成 + per-category EM 主表 + 项目主张定位（C+/C/D）
2. **Router 修复对比**：S3 §1 38% → S8 修复后 X%（per-category）
3. **per-category EM 矩阵**：6 方法 × 6 类别，每格标 N、EM、95% CI、source 路径
4. **配对置换检验**：`full` vs `vector_rag` 在合并子集（temporal-reasoning + knowledge-update）上的 p 值与效应方向
5. **M2 judge 新结果**：在 temporal-salient 子集上的 stale/fresh/tie 分布（对照 S3 §4 的 74% tie）
6. **项目主张定位**：按 `docs/S8-PREREGISTRATION.md` 表格定位结论（C+ / C / D）；引用 §3b 的决策规则
7. **限制**：(a) n=100 仍欠功效（MDE ±0.14 整体 / ±0.21 合并子集）；(b) mimo-v2.5 单 reader；(c) 单次跑无 run-to-run variance；(d) 未做 embedding 消融（S3 §3 deferred）；(e) anti-fishing 边界已在 §3c 澄清，但 router 修复属允许的 future-work 实施
8. **Optional future-work（不阻塞项目主张）**：(a) 500 题稳定性确认（MDE 缩窄到 ±0.06–0.10，**非项目主张所必需**——`docs/METHODOLOGY_CHANGE.md` 已自承 500 题期望无显著性）；(b) embedding swap（bge/e5 vs qwen3）；(c) sentinel rate prompt 调优（突破 20% ceiling）；(d) O01 learned router
9. **诚实红线**：C+ 结论下不声称"ETEC 有效"——只说"在主场小样本上方向正确、效应量达 X、显著性未达（n=100 不足）"；C/D 结论照实写负结果/中性。

### Step 7: 独立审查

独立 subagent 复审 S8 全部产物，硬性规则（与 S7 一致）：

> 每个论点必须引用仓库内工件路径作为证据；无法给出工件路径的论点视为无效，审查作废重来。

输出 `docs/STAGE8_REVIEW.md`，包括：

- A1–A7、C1–C5、D1–D6 各项独立复核结果（不信任 commit 信息，重跑验证命令）
- 决策规则引用是否一致（对照 `docs/S8-PREREGISTRATION.md`）
- 是否存在 cross-model 对比（禁止——AGENTS.md N8）
- 是否存在数字无工件来源（抽查 5 个数字）
- 是否存在 silent fallback（retrieval 退到 vector 无 observable 标记——禁止）

## 验收标准（全部满足才算完成）

| # | 标准 | 验证 |
|---|---|---|
| V1 | Router 全 500 题准确率 ≥ 60%，temporal-reasoning + knowledge-update ≥ 70% | router_diagnosis 输出 |
| V2 | grep `monkeypatch\|ipv4` 在 src/ 与 benchmarks/ 返回 0 | grep 输出 |
| V3 | replay/online 重分类率 = 0% 在新样本上 | test_replay |
| V4 | stratified100 manifest 存在且分配正确 | cat manifest |
| V5 | `docs/S8-PREREGISTRATION.md` 存在，C+/C/D 决策规则表完整 | 文档审查 |
| V6 | Phase D 全绿（D1–D6） | runs/publication/s8-stratified100/ artifacts |
| V7 | `docs/S8-STRATIFIED_VALIDATION_REPORT.md` 存在，§1–§9 完整，定位结论（C+/C/D）已写入 §6 | 文档审查 |
| V8 | `docs/STAGE8_REVIEW.md` 独立审查 PASS 或 CONDITIONAL PASS | 文档审查 |
| V9 | ruff/mypy/pytest/smoke 全绿 | 命令输出 |
| V10 | 无 cross-model 对比、无无工件数字、无 silent fallback | 抽查 |

## 关键约束

- **不声称"ETEC 有效"或"QEMR 有效"**：分支 C 的诚实红线不变；S8 是给"方法在主场是否有效"提供数据驱动结论，不是预先承诺翻盘。即便 Phase E 结论为 C+（主场方向正确），也只说"方向正确、效应量达 X、显著性未达（n=100 不足）"，**不声称"ETEC 有效"**。
- **不修 QEMR_WEIGHT_PROFILES**（S3 §2 已证权重 profile sound，不修）；**不修 ETEC 合并决策**（S3 §4 已证 retrieval 未忽略 SUPERSEDE，不修）；**只修 router 规则 + extraction prompt sentinel**（S5 明确列为 future-work）。
- **不实施 O01 learned router / O02–O08**（optional tasks 不在本任务）。
- **不实施 M18 release**（M18 是 S8 之后的独立任务，不在本窗口）。
- **不跑 500 题**（`configs/longmemeval/main500.toml` 入库但本任务不启动；`docs/METHODOLOGY_CHANGE.md` 已自承 500 题期望无显著性，跑 500 浪费 mimo-v2.5 token，**不阻塞项目主张**）。500 题降级为 S8 报告 §8 列出的 optional future-work。
- **不 push 到 GitHub**（用户已决定，38 commits 保持本地领先）。
- **不 commit datasets / secrets / model_cache / private traces**（.gitignore 已覆盖 runs/、data/raw/、.env）。
- **不跨模型对比**（v1 vs v2 都用 mimo-v2.5 + 同 4096 预算 + qwen3-embedding-0.6b；deepseek-v4-flash 24 题 run 仍禁止对比——AGENTS.md N8）。
- **失败样本如实报告**：分层样本上若某类失败率高，照实写报告，不得删除或换样本。
- **anti-fishing 边界**：修 router 正则 / 修 extraction prompt 是 S5 future-work 明确允许的；**禁止**在 S8 内根据 EM 结果反向调权重 profile 或 retrieval budget。
- **核心算法零 LLM**：ETEC/QEMR/retrieval/consolidation 是纯确定性代码，LLM 只在 extraction 与 reader 两点；任何"用 LLM 调 router 决策"的改动超出 S8 范围（属 O01）。

## 上下文引用

- 整改定稿：`docs/REMEDIATION_FINAL_REPORT.md`（分支 C thesis + §6 Limitations + §7 Future work）
- S3 根因诊断：`docs/QEMR_FAILURE_DIAGNOSIS.md`（§1 router 38% / §2 weights sound / §4 M2 74% tie）
- S2 v2 baseline：`runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json`（git `17b1014`，50 题全 single-session-user）
- S6 routerfix 已加正则：commit `0ebbea1`（`_FACT_RE` + `_STRONG_FACT_RE` + `_KNOWLEDGE_UPDATE_RE`）+ `21899e4`（factual-lookup + knowledge-update cue）
- S7 hardening：commit `662436b`（finalize guard + extraction_variance + benchmark_stats + CI/Docker/Makefile）
- 现有 router 测试：`tests/retrieval/test_qemr.py`、`tests/mechanism/test_router_diagnosis.py`、`tests/fixtures/router/m11_query_router_fixture.json`
- 500 题数据：`data/raw/longmemeval/longmemeval_s_cleaned.json`（500 题，6 类 question_type 分布见本文件 §背景表；用作分层采样的源总体，**本任务不直接跑 500 题**）
- 500 题配置（**不跑，已降级为 optional future-work**）：`configs/longmemeval/main500.toml`
- 服务器仓库（已迁）：`ssh gpu-5090:/mnt/aidata/tongjiakai/evoeventmem`（符号链接 `~/evoeventmem`）；含 21 个 FINALIZED 产物（test20 ablation family），与本地同源
- 本地状态清单：`runs/STATE_INVENTORY.md`（gitignored，三端 git + 产物对齐记录）
- AGENTS.md 代码评审规则：禁止不等模型下 benchmark 对比、禁止 silent fallback、禁止无工件数字

## 执行顺序总结

1. **Step 0** 干净基线 → 树立 v2 baseline 引用
2. **Step 1** Router 规则补强（Phase A 阻塞门，A1–A5）
3. **Step 2** IPv4 shim 移除 + Replay 一致性（Phase A 续，A6–A7）
4. **Step 3** 分层采样函数 + 预注册决策 + M2 judge 重设计（Phase B，B1–B3）
5. **Step 4** 分层 dry run（Phase C，C1–C5，无 LLM）
6. **Step 5** 分层 live run（Phase D，需用户审批，D1–D6，**项目最终 benchmark**）
7. **Step 6** 最终验证报告（Phase E，V7，按预注册规则定位 C+/C/D）
8. **Step 7** 独立审查（V8）

**禁止跳序**：Phase A 不全绿不进 B；B 不全不进 C；C 不全绿不进 D；D 不全不写报告；报告未独立审查不算完成。**500 题主跑不在本任务序列中**——它已降级为 Step 6 §8 列出的 optional future-work。
