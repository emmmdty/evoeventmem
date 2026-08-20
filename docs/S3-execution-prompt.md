# Stage 3 执行提示词：QEMR 失效根因诊断 + M2 stale-judge（SUPERSEDE>0 后的中间路线）

## 背景

EvoEventMem 项目（`/home/tjk/myProjects/internship-projects/evoeventmem-starter`，git HEAD `17b1014`）刚完成 Stage 2：v3 prompt 在 50 题 mimo-v2.5 上重跑完成 + finalize + 独立审查 **CONDITIONAL PASS**（`docs/STAGE2_REVIEW.md`）。

S2 的关键诊断证据（写进 `docs/EVALUATION.md` §test50-mimo-v2-factslot + `docs/STAGE2_REVIEW.md`）：

- **ETEC SUPERSEDE 从 v1 的 0 变成 v2 的 109**（across 40/50 samples，第一次在真实数据上触发）—— `ADD=7188, MERGE=1770, REJECT=352, SUPERSEDE=109`。这是 S2 的核心正面发现：v3 prompt 的 `fact_slot` required + sentinel + retry 让四重 gate 在真实 LLM 输出上可满足，SUPERSEDE 在 109 个 candidate pair 上真的 fire 了。
- **v2 `full` EM = 0.48 vs v1 = 0.46**（Δ +0.02，略升不翻盘）。`full` vs `event_no_etec` gap 从 -0.08（v1，ETEC 有害）收敛到 0.00（v2，ETEC 中性）—— ETEC 不再拖分，但也没拉分。绝对 `full` EM (0.48) 仍远低于 `vector_rag` (0.56)。
- **可达性测试 PASS（非 XFAIL）** on v2 snapshot —— 至少一对 within-sample event 满足四重 gate。
- **fact_slot 有效率 = 66.8% (6295/9419)**（≥ 50% floor ✅，比 S1c 5 题的 60.3% 稳定上升）；**valid_from = 66.8% (6294/9419)** ✅；**valid_until = 0.7% (63/9419)**（无 floor，informational）。
- **sentinel 率 = 33.2% (3124/9419)**（≥ 20% ceiling ⚠️，xfail；从 S1c 5 题的 39.7% 下降 6.5pp，但仍不达标）—— 路由到 S3/S5 决策（**不**在 S2 反复调 prompt 凑数，AGENTS.md 反 fishing）。
- **S4b vector_rag 延迟修复生效**：v2 `vector_rag` p50 search = 2,333 ms（v1 是 437,557 ms），远低于 30,000 ms 目标。S4b 把 embedding 成本从 `search_latency_ms` 移到 `vector_index_ms`（~68,623 ms p50），所以 **v1 vs v2 latency 不可直接对比；v1 vs v2 EM 仍可对比**（latency 不影响 EM）。
- **Replay/online 一致性**：109 SUPERSEDE 在 replay 和 online 上**完全一致**；2/50 样本（577d4d32, a06e4cfe）有 minor ADD↔MERGE 重分类（4 actions total），记录为 known limitation（per spec line 432，不静默修）。
- **独立审查结论**：13/15 验收绿，1 个 xfail（sentinel 率），1 个 false-positive 测试 parsing bug（已修）。scope/R3/overclaim/provenance 全部守住。
- **git 状态**：S2 全部 commit；`git diff src/evoeventmem/` 为空（S2 是测量阶段，未改代码）；HEAD = `17b1014`。

S2 触发 S3 的路由：**SUPERSEDE > 0（109）+ EM 没翻盘（+0.02）→ S3 走中间路线**：诊断为什么 QEMR 未能把 109 个 superseded memories 转化成 reader 可见的增益。这是决定 thesis 中间路线（"ETEC 的 SUPERSEDE 在真实数据上可达但**不足以提升整体准确率**——证据约束的 operating surface 在 LongMemEval 的 single-session-user 类上太窄"）还是 pivot 到 negative-result 论文（S5 path A）的关键证据窗口。

整改 spec v1.1（`docs/REMEDIATION_SPEC.md` Stage 3，lines 285-344）明确 S3 的五个步骤：
> 1. Router 准确率诊断（最便宜；N9 修复 — 只产 confusion matrix，不改规则）
> 2. Weight profile 消融（中等；no_temporal, no_graph, uniform — 50 题 v2 run 上跑）
> 3. Embedding 模型对照（最贵，谨慎；bge-large-en-v1.5 或 e5-large-v2，只 vector_rag + full）
> 4. M2 stale-memory judge（条件性，B2 修复 — 仅当 S2 SUPERSEDE > 0；judge ≠ reader）
> 5. 写诊断报告 `docs/QEMR_FAILURE_DIAGNOSIS.md`

**scope 边界（明确声明，不藏着）**：S3 **只诊断 QEMR 失效根因 + 条件性跑 M2**，不改 `router.py` 规则、不改 `retrieval.py` 的 production QEMR weight profile、不修 R3（`multi_valued` 过打）、不跑 500 题、不改 prompt、不开始 S5 论文 draft。S3 是诊断 + 写报告阶段，不是修复阶段。任何修复（router 规则改 / weight profile 改 / embedding 换）**留到 S3 后的独立小任务**，需要独立审查显式批准。

**为什么是这一阶段**：S2 证明 SUPERSEDE 在真实数据上可达（109 个 pair fire），但 `full` EM 没翻盘。S3 回答四个关键问题：
1. **Router 准确率**：50 题 LongMemEval 上 `QueryRouter`（`router.py:85`, policy `query-router.rules.v1`）把多少 temporal / graph / semantic 类 query 分错？如果 router accuracy < 80%，QEMR 的 weight profile 就完全错位（temporal 类 query 走了 semantic 权重）。
2. **Weight profile 是否过拟**：把 temporal source 权重设 0（`qemr_no_temporal`）、graph 设 0（`qemr_no_graph`）、所有 source 等权（`qemr_uniform`），在 50 题 v2 snapshot 上跑 retrieval（reader cache 共享，只改 retrieval weight）。对比 LoCoMo §9 的发现：`no_temporal` (0.3654) > `qemr` (0.3000) → LongMemEval 上是否也成立？
3. **Embedding 模型是否是瓶颈**：用 `bge-large-en-v1.5` 或 `e5-large-v2` 重跑 vector_rag + full 50 题。如果换 embedding 后 `full` 翻盘 → embedding 质量是瓶颈，QEMR 本身没问题。如果仍输 → QEMR 设计本身有问题。
4. **SUPERSEDE 后的答案是 stale 还是 fresh**（M2 子阶段）：抽 `full` 命中但 `event_no_etec` 未命中的 sample，让 judge 模型判定 `full` 的答案是否更新、`event_no_etec` 是否给了 stale 值。如果 `full` 给了 stale 答案 → SUPERSEDE 在 consolidation 层面 fire 了但 retrieval 层面没用上 → QEMR 的 temporal source 没正确排除 superseded memories。

**S3 不论结果都是赢**：若 router accuracy ≥ 80% + weight ablation 显示 `qemr_no_temporal` 输给 `qemr` → QEMR 设计本身合理但 embedding 质量是瓶颈 → S5 走分支 B（继续 positive thesis）；若 router accuracy < 80% 或 `qemr_no_temporal` 赢了 `qemr` → QEMR 设计本身有问题 → S5 走分支 C（中间路线）；若 M2 显示 `full` 给了 stale 答案 → retrieval 没消费 SUPERSEDE → 修 retrieval 的 temporal filter（S3 后的独立小任务）。

### 已完成的前置工作

- S0 完成（commit `b60b38d`，诚信止血）。
- S1a 完成（commit `162183c`，schema + prompt v2 落地）。
- S1b 完成（commit `00b3dc6`，5 题 smoke + reachability test）。
- S1c 完成（commit `ab5ba1a`，required fact_slot + retry + salvage + v3 prompt + contrast pair）。
- S4b 完成（commit `46b7b38`，vector_rag 延迟修复：`CachedEmbeddingModel` 批量化 + `OpenAICompatibleEmbeddingClient` 渐进收缩 + `run.py` 写时 pre-warm）。
- S2 完成（commit `17b1014`，50 题 v2 run + 诊断 + 独立审查 CONDITIONAL PASS）。
- v2 run 已 finalized 在 `runs/publication/m13-longmemeval-test50-mimo-v2-factslot/`：
  - `extraction_snapshot.json`（combined，50 题，9419 events，66.8% fact_slot 有效率）
  - `samples/<sample_id>.json` × 50（含 ingestion.etec.actions，含 SUPERSEDE 计数）
  - `retrieval.jsonl`（200 行 = 50 题 × 4 retrieval methods）
  - `summary.json`（v1 vs v2 EM 对比表 + ETEC actions + fact_slot/sentinel 率 + efficiency metrics）
  - `finalized/FINALIZED.json` + `manifest.json`
  - `model_cache/chat/`（extraction + reader LLM 调用缓存，S3 ablation 可复用，节省成本）
  - `model_cache/embeddings/`（qwen3-embedding-0.6b 缓存，S3 embedding 对照时**不**能复用——换 embedding 模型后缓存失效）
- v1 baseline run 在 `runs/publication/m13-longmemeval-test50-mimo/`（finalize 于 `e585d7e`）。
- S3 复用基础设施：
  - `benchmarks/mechanism/replay.py`（offline replay，已有，S2 用过；S3 可继续用，但 `python -m benchmarks.mechanism.replay` 无 `__main__` CLI，需 programmatic call）
  - `benchmarks/mechanism/s2_diagnostics.py`（S2 诊断脚本，已有）
  - `tests/consolidation/test_etec_real_data_reachability.py`（四重 gate 可达性测试，已有，参数化 via `EEM_S1B_SNAPSHOT_PATH`）
  - `tests/benchmarks/test_s2_acceptance.py`（S2 验收测试，已有）
  - `src/evoeventmem/retrieval.py`：`RetrievalStrategy` enum (`FIXED_VECTOR` / `QEMR`)，`QEMR_WEIGHT_PROFILES` per-intent 权重表（`DENSE` / `TEMPORAL` / `GRAPH`），`RetrievalHarness` 类。S3 在这里加 ablation 入口（不改 production weight profile，加新 strategy 或 `RetrievalControls` 字段）。
  - `src/evoeventmem/router.py`：`QueryRouter` 类（policy `query-router.rules.v1`），`QueryIntent` enum (`NO_MEMORY` / `SEMANTIC` / `TEMPORAL` / `GRAPH` / `INFORMATIONAL` / `KNOWLEDGE_UPDATE`)，`_RELATIVE_RE` 正则，`TemporalOperator` enum。
- mimo-v2.5 reader/extractor provider 可用（**注意**：当前 `.env` 用新 API key `OPENAI_API_KEY=sk-18gz...`，因为 S2 中途原 key 的 weekly usage limit 耗尽；新 key 也可能再次耗尽——监控 HTTP 429，必要时换 key）。
- embedding server（GPU 上 `qwen3-embedding-0.6b`）已重启配置：`BATCH_SIZE=8`, `max_length=2048`（在 `gpu-5090:/mnt/aidata/tongjiakai/embed_server/qwen_embed_server.py`）。SSH tunnel 重建命令：`cpolar-ssh-update && ssh -o StrictHostKeyChecking=accept-new -f -N -L 11436:127.0.0.1:11436 gpu-5090`。**注意 cpolar 端口会漂移**，每次启动前先 `cpolar-ssh-update` 刷新。
- M2 judge 模型已配置在 `.env`：`ARK_API_KEY=ark-abcb054b-...`, `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3`, `ARK_MODEL=minimax-m3`。**minimax-m3 ≠ mimo-v2.5**（不同族模型，满足 spec N8 / B4 的 judge ≠ reader 要求）。

### 关键约束（违反即 spec 失败）

- **只做 S3，不开始 S4a/S5**——S3 完成并 commit + 独立审查通过后才允许进入下一阶段。
- **不修 `router.py` 规则**——S3 step 1 只产 confusion matrix + 修改建议；router 规则修改是独立任务，需先看 confusion 模式再决定改哪条 `_RELATIVE_RE`。**N9 scope 边界**。
- **不改 production QEMR weight profile**——S3 step 2 在 `retrieval.py` 加 ablation 入口（新 strategy 或 `RetrievalControls` 字段），但 `QEMR_WEIGHT_PROFILES` dict 不动。production 路径仍走原 weight。**修留到 S3 后独立小任务**。
- **不修 R3**（`multi_valued` 过打）——S3 不在 scope；`consolidation.py:876` 的 `multi_valued` 短路保留。
- **不动 prompt**——v3 prompt 已落地；sentinel 率 33.2% 是 known weakness，路由到 S5 决策。
- **不跑 500 题 / 不加新 benchmark**——S3 在 50 题上跑；500 题是 S5 path B 的事。
- **可以 git commit**——本阶段允许直接 commit + push。但**每个步骤单独 commit**（commit message 模板见下）。**不**在一个大 commit 里塞 router diagnosis + weight ablation + M2。
  - step 1 commit: `feat(s3.1): router confusion matrix diagnosis on v2 run`
  - step 2 commit: `feat(s3.2): qemr weight ablation (no_temporal / no_graph / uniform)`
  - step 3 commit（如做）: `feat(s3.3): embedding model comparison (bge-large-en-v1.5 / e5-large-v2)`
  - step 4 commit: `feat(s3.4): M2 stale-memory judge (minimax-m3 ≠ mimo-v2.5 reader)`
  - step 5 commit: `docs(s3): QEMR_FAILURE_DIAGNOSIS report + final routing`
- **不预先声明期望**——预注册的 negative-result 框架（`METHODOLOGY_CHANGE.md`）要求不 bias 结果解读。**禁止**在 ablation 跑完前在 `docs/QEMR_FAILURE_DIAGNOSIS.md` 或 commit message 写"期望 no_temporal 输给 qemr"。
- **不声称 thesis 翻盘 / ETEC 有效 / QEMR 有效**（即使 ablation 显示 QEMR 设计合理）——S3 只能说"50 题上 router accuracy = X%，weight ablation 显示 Y"，不能说"QEMR 设计合理"或"thesis 翻盘"。**thesis 翻盘 / pivot 决策在 S5**。
- **禁止跨模型对比 EM**——ablation 必须用同一 reader (mimo-v2.5) + 同一 context budget (4096 tokens) + 同一 retrieval budget，**只差** retrieval weight profile。**AGENTS.md 代码评审规则**："Reject changes that mix benchmark methods under unequal model, context-budget, or retrieval-budget settings"。
- **M2 judge 模型 ≠ reader 模型**——M2 用 minimax-m3（ARK_MODEL），reader 是 mimo-v2.5。**禁止同源 bias**（spec N8 / B4 + 审计 `9of10_AUDIT.md:248`）。M2 必须缓存 judge inputs/outputs（AGENTS.md "LLM judges require cached inputs/outputs and a documented judge model"）。
- **不破坏既有契约**：`uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q` + `uv run ruff check .` + `uv run mypy src` + `uv run python -m evoeventmem.cli smoke` 必须全绿。S3 在 `retrieval.py` / `router.py` 加 ablation 入口时不能破坏 production 路径。
- **不静默 fallback**——ablation 中如果 temporal source 被禁用，**必须可观察**（retrieval 记录里要标明 `qemr_no_temporal` strategy）。AGENTS.md "Reject silent fallback from temporal/graph retrieval to vector retrieval; fallback must be observable"。
- **不删 S2 / S4b / S1c 落地**——所有前置代码保留；ablation 是**新增** strategy，不替换。
- **可以联网补充信息**——本阶段允许使用 webfetch 查 LongMemEval §5.4 (time-aware query expansion +6.8%~11.3% temporal)、Filesystem-Based Memory (arXiv:2607.26637) 文献、confusion matrix 最佳实践、bge-large-en-v1.5 / e5-large-v2 模型卡。

## 执行步骤

### Step 0: 前置检查 + 文献补充

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-starter
git log --oneline -5   # 确认 HEAD 是 17b1014 (S2 commit) 或其后继
git status --short     # 应该 clean（runs/ 是 gitignored）

# 确认 v2 run final + 可达性 PASS
test -f runs/publication/m13-longmemeval-test50-mimo-v2-factslot/finalized/FINALIZED.json && echo "v2 FINALIZED OK"
EEM_S1B_SNAPSHOT_PATH=runs/publication/m13-longmemeval-test50-mimo-v2-factslot/extraction_snapshot.json \
  uv run pytest tests/consolidation/test_etec_real_data_reachability.py -v -s 2>&1 | tail -5
# 期望: 1 passed (不是 XFAIL)

# 确认 v2 summary 含 SUPERSEDE 计数
uv run python -c "
import json
d = json.loads(open('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json').read())
print('v2 sample_validation:', d.get('sample_validation'))
# 抽 5 个 sample 看看 SUPERSEDE 是否非 0
from pathlib import Path
from collections import Counter
actions = Counter()
for f in Path('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/samples').glob('*.json'):
    if 'extraction_snapshot' in f.name: continue
    a = json.loads(f.read_text()).get('ingestion',{}).get('etec',{}).get('actions')
    if a: actions.update(a)
print('ETEC actions:', dict(actions))
print('SUPERSEDE count:', actions.get('SUPERSEDE', 0))
"

# 确认 router / retrieval / policies
grep -n "POLICY_NAME\|class QueryRouter\|class QueryIntent\|_RELATIVE_RE" src/evoeventmem/router.py | head -10
grep -n "class RetrievalStrategy\|QEMR_WEIGHT_PROFILES\|FIXED_VECTOR_WEIGHTS\|class RetrievalHarness\|class RetrievalControls" src/evoeventmem/retrieval.py | head -10
cat runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('router_policy:', d.get('router_policy_name'))
print('retrieval_policy:', d.get('retrieval_policy_name'))
print('consolidation_policy:', d.get('consolidation_policy_name'))
"

# 确认 mimo + minimax-m3 + embedding 都可用
set -a; source .env; set +a
curl -s -m 10 https://opencode.ai/zen/go/v1/chat/completions \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "Content-Type: application/json" -H "User-Agent: opencode/1.0" \
    -d '{"model":"mimo-v2.5","messages":[{"role":"user","content":"OK"}],"max_tokens":50}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('mimo:', 'OK' if 'choices' in d else d.get('error',{}).get('message','UNKNOWN'))"
curl -s -m 10 $ARK_BASE_URL/chat/completions \
    -H "Authorization: Bearer $ARK_API_KEY" -H "Content-Type: application/json" \
    -d "{\"model\":\"$ARK_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"OK\"}],\"max_tokens\":50}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('minimax-m3:', 'OK' if 'choices' in d else d.get('error',{}).get('message','UNKNOWN'))"
nc -z 127.0.0.1 11436 && echo "embedding tunnel UP" || echo "embedding tunnel DOWN - fix: cpolar-ssh-update && ssh -o StrictHostKeyChecking=accept-new -f -N -L 11436:127.0.0.1:11436 gpu-5090"

# 确认 LongMemEval dataset 有 question_type 字段（router diagnosis 的 gold label 来源）
uv run python -c "
import json
from pathlib import Path
data = json.loads(Path('data/raw/longmemeval/longmemeval_s_cleaned.json').read_bytes())
types = {}
for r in data[:50]:
    t = r.get('question_type', 'UNKNOWN')
    types[t] = types.get(t, 0) + 1
print('LongMemEval question_type 分布（前 50 题）:', types)
"

# 可选: webfetch 文献补充（LongMemEval §5.4 / Filesystem-Based Memory）
# 验证 LongMemEval §5.4 "+6.8%~11.3% temporal reasoning" 这个 claim
# 验证 Filesystem-Based Memory (arXiv:2607.26637) "no agent converts organization into better answers"
```

预期发现：
- HEAD = `17b1014` 或后继；工作区 clean。
- v2 FINALIZED OK；可达性测试 1 passed（非 XFAIL）。
- SUPERSEDE = 109 across 40/50 samples（S2 已确认）。
- router policy = `query-router.rules.v1`；retrieval policy = `qemr-weight-profiles.v2`；consolidation policy = `etec.v1`。
- mimo + minimax-m3 + embedding tunnel 都可用。
- LongMemEval `question_type` 字段存在且分布合理（gold label 来源）。

**关键判断**：侦察完后，若 mimo 或 minimax-m3 不可用，S3 step 1-3 仍可跑（router diagnosis 不需要 LLM；weight ablation 复用 v2 的 reader cache）；M2 标 BLOCKED，等 judge 模型恢复。

### Step 1: Router 准确率诊断（最便宜；N9 scope — 只产 confusion matrix，不改规则）

```bash
# 新建 benchmarks/mechanism/router_diagnosis.py
# 功能: 对 50 题 v2 run 的 query，用 gold intent label (LongMemEval question_type) vs router.py 的预测 intent，算 confusion matrix
# 输入: --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot
# 输出: confusion matrix (gold × predicted) + per-class precision/recall + 误分类样本列表 + 修改建议
# scope: 只产 confusion matrix + 修改建议；不修改 router.py 规则
# 测试: tests/mechanism/test_router_diagnosis.py（用 fake queries + gold labels 验证 confusion matrix 计算逻辑）

# 实现要点:
# 1. 加载 v2 run 的 50 个 sample（从 samples/*.json 取 question_text + question_type）
# 2. 对每个 query 调用 QueryRouter().route(query, reference_time=question.asked_at) 拿 predicted intent
# 3. gold intent 来自 LongMemEval 的 question_type 字段（需要映射: LongMemEval question_type → QueryIntent enum）
#    - "single-session-user" / "multi-session-user" → SEMANTIC 或 INFORMATIONAL?
#    - "knowledge-update" → KNOWLEDGE_UPDATE 还是 TEMPORAL?
#    - "temporal-reasoning" → TEMPORAL
#    - "multi-session-core" / "session-gen" → 视具体子类
#    这个映射需要谨慎：参考 LongMemEval 论文 §4 的 question type 定义
# 4. 算 confusion matrix + per-class precision/recall/F1
# 5. 打印误分类样本（gold × predicted × question_text），分析模式
# 6. 写修改建议（不修 router.py）：哪条 _RELATIVE_RE 应该加 / 哪条 weight profile 应该调
# 7. 输出可读报告 + JSON

uv run python -m benchmarks.mechanism.router_diagnosis --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot
```

**验收**：`benchmarks/mechanism/router_diagnosis.py` 存在 + 跑通 + 产 confusion matrix + 写入 `docs/QEMR_FAILURE_DIAGNOSIS.md` 的 §1 节。

**commit**：`feat(s3.1): router confusion matrix diagnosis on v2 run`

### Step 2: Weight profile 消融（中等；ablation 入口加在 retrieval.py，不改 production）

```bash
# 在 src/evoeventmem/retrieval.py 加 ablation 入口:
# - RetrievalStrategy.QEMR_NO_TEMPORAL: temporal source 权重设 0
# - RetrievalStrategy.QEMR_NO_GRAPH: graph source 权重设 0
# - RetrievalStrategy.QEMR_UNIFORM: 所有 source 等权 (DENSE=TEMPORAL=GRAPH=1.0 或归一化)
# 实现方式: 新增 strategy enum + 在 _effective_weights / _score_candidates 中根据 strategy 切换 weight profile
# production 路径仍走 QEMR_WEIGHT_PROFILES，不动

# 在 50 题 v2 run 上跑 3 个 ablation:
# - 复用 v2 的 extraction_snapshot + model_cache/chat（reader 调用 cache 命中，不重跑 reader LLM）
# - 只重跑 retrieval + reader（reader cache 命中）→ 成本只算 retrieval 差异
# - 5 个 weight 配置 × 50 题 = 250 reader call，但 cache 命中后只算 retrieval
# - 控制成本在 1 天内

# 跑 ablation run（每个 strategy 一个 run dir 或一个 method-subdir）:
# 路径: runs/publication/m13-longmemeval-test50-mimo-v2-ablation-no-temporal/
#        runs/publication/m13-longmemeval-test50-mimo-v2-ablation-no-graph/
#        runs/publication/m13-longmemeval-test50-mimo-v2-ablation-uniform/
# 每个 run 只跑 full method（不需要 no_memory / full_context / vector_rag / event_no_etec / etec）

# 对比 EM: full (qemr) vs full (qemr_no_temporal) vs full (qemr_no_graph) vs full (qemr_uniform)
# 如果 no_temporal > qemr (matches LoCoMo §9 finding 0.3654 > 0.3000) → temporal source 在 LongMemEval 上有害
# 如果 uniform > qemr → weight profile 过拟，等权反而更好
# 如果 qemr 赢了所有 ablation → weight profile 设计合理，问题不在这

uv run python -m benchmarks.longmemeval.run \
    --config configs/longmemeval/test50-mimo-v2-ablation.toml \
    --run-dir runs/publication/m13-longmemeval-test50-mimo-v2-ablation-no-temporal \
    --sample-ids <50 IDs>  # 用 scripts/run50-parallel-v2-factslot.sh 的 50 IDs

# 注意: 需要新建 configs/longmemeval/test50-mimo-v2-ablation.toml（只跑 full method + 新 strategy）
# ablation run 仍用 mimo-v2.5 reader + qwen3-embedding-0.6b，同一模型同一 embedding，只改 retrieval weight
# ablation run 的 reader cache 可复用 v2-factslot 的（symlink 或 cp -r model_cache/chat）
```

**验收**：至少 2 个 weight ablation 跑完（`no_temporal` + `uniform`），结果写入 `docs/QEMR_FAILURE_DIAGNOSIS.md` 的 §2 节。

**commit**：`feat(s3.2): qemr weight ablation (no_temporal / no_graph / uniform)`

### Step 3: Embedding 模型对照（最贵，谨慎；可选）

```bash
# 仅当 Step 2 不够再启动
# 用 bge-large-en-v1.5 或 e5-large-v2 重跑 vector_rag + full 50 题
# 注意: 换 embedding 后所有 chunk 重新 embed，embedding cache 全失效
# 成本: 50 题 × ~500 chunks × 128ms = ~50 min embedding + retrieval + reader cache 复用
# 路径: runs/publication/m13-longmemeval-test50-mimo-v2-bge-large/
#        runs/publication/m13-longmemeval-test50-mimo-v2-e5-large/

# 如果换 embedding 后 full 翻盘 → embedding 质量是瓶颈，QEMR 本身没问题
# 如果仍输 → QEMR 设计本身有问题，需要重新设计 weight profile 或简化成 FIXED_VECTOR
```

**验收**：embedding 对照实验完成**或**显式声明"因成本跳过，留 S5 决定"，写入 `docs/QEMR_FAILURE_DIAGNOSIS.md` 的 §3 节。

**commit（如做）**：`feat(s3.3): embedding model comparison (bge-large-en-v1.5 / e5-large-v2)`

### Step 4: M2 stale-memory judge（条件性 — SUPERSEDE=109 > 0 触发；judge ≠ reader）

```bash
# 前置条件: S2 SUPERSEDE = 109 > 0（已确认）→ M2 必须跑
# 抽样: full 命中但 event_no_etec 未命中的 sample（SUPERSEDE 触发的，理论上旧值应被替换）
# 让 judge 模型 (minimax-m3, ≠ mimo-v2.5 reader) 判定:
#   - full 的答案是否更新（fresh）
#   - event_no_etec 是否给了 stale 值
# 缓存 judge inputs/outputs（AGENTS.md 要求）

# 实现:
# 1. 从 v2 run 提取 full EM=1 但 event_no_etec EM=0 的 sample（"full 命中 event_no_etec 未命中"）
#    如果没有这样的 sample，提取 full 答案 ≠ event_no_etec 答案的 sample（答案不同）
# 2. 构造 judge prompt:
#    "Question: <query>\nGold answer: <gold>\nfull (with SUPERSEDE) answer: <full_pred>\nevent_no_etec (without SUPERSEDE) answer: <etec_pred>\n
#     Which answer is more up-to-date / less stale? Output JSON: {\"less_stale\": \"full\" | \"event_no_etec\" | \"tie\", \"reason\": \"...\"}"
# 3. 用 minimax-m3 跑 judge（Ark API），缓存 inputs/outputs 到 runs/.../m2_judge_cache/
# 4. 统计: full 更 fresh 的占比 / event_no_etec 更 stale 的占比 / tie
# 5. 如果 full 大量更 fresh → SUPERSEDE 在 retrieval 层面没用上（应该用但没用）
#    如果 full 大量更 stale → SUPERSEDE 在 consolidation 层面 fire 了但没正确替换旧值
#    如果 tie 居多 → SUPERSEDE 对这些 sample 的答案是 no-op

# 路径: benchmarks/mechanism/m2_stale_judge.py
# 输出: runs/publication/m13-longmemeval-test50-mimo-v2-factslot/m2_judge_report.json + .md
# 写入 docs/QEMR_FAILURE_DIAGNOSIS.md 的 §4 节

uv run python -m benchmarks.mechanism.m2_stale_judge \
    --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot \
    --judge-model minimax-m3 \
    --judge-base-url $ARK_BASE_URL \
    --judge-api-key-env ARK_API_KEY \
    --output runs/publication/m13-longmemeval-test50-mimo-v2-factslot/m2_judge_report.md
```

**验收**：M2 跑完且 judge 模型 = minimax-m3（≠ mimo-v2.5 reader）+ judge inputs/outputs 已缓存，**或**显式声明"未跑 + SUPERSEDE=109>0 下的 auditability weakness"（写入 `docs/QEMR_FAILURE_DIAGNOSIS.md` 的 §4 节）。

**commit**：`feat(s3.4): M2 stale-memory judge (minimax-m3 ≠ mimo-v2.5 reader)`

### Step 5: 写诊断报告 `docs/QEMR_FAILURE_DIAGNOSIS.md`

```bash
# 报告结构:
# 1. Router 准确率诊断（Step 1 输出）
#    - confusion matrix
#    - per-class precision/recall/F1
#    - 误分类样本 + 模式分析
#    - 修改建议（不修 router.py）
# 2. Weight profile 消融（Step 2 输出）
#    - qemr vs qemr_no_temporal vs qemr_no_graph vs qemr_uniform 的 EM 对比表
#    - 是否 matches LoCoMo §9 (no_temporal > qemr)
#    - weight profile 是否过拟
# 3. Embedding 模型对照（Step 3 输出，或"跳过 + 留 S5"声明）
# 4. M2 stale-judge（Step 4 输出，或"未跑 + auditability weakness"声明）
# 5. 根因结论（human-judgment process item, N10 — 不列入"验证命令"）
#    - QEMR 失效的根因（router / weights / embedding / temporal source / 其他）
#    - 修复建议或 pivot 建议
#    - S5 路由: 分支 B (positive) / C (中间) / A (negative)
```

**commit**：`docs(s3): QEMR_FAILURE_DIAGNOSIS report + final routing`

### Step 6: 全套回归

```bash
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

要求全绿（S3 在 `retrieval.py` / `router.py` 加 ablation 入口时不能破坏 production 路径；`tests/retrieval` 现有测试必须仍通过）。

### Step 7: 全局一致性扫描

```bash
# 确认 S3 没动 production router 规则
git diff src/evoeventmem/router.py
# 期望: 空（router.py 不动；router_diagnosis.py 是新文件在 benchmarks/）

# 确认 S3 没动 production QEMR weight profile
git diff src/evoeventmem/retrieval.py
# 期望: 只有新增 strategy enum + ablation 入口；QEMR_WEIGHT_PROFILES dict 不动

# 确认 S3 没新增 overclaim
rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效|QEMR 有效' docs/QEMR_FAILURE_DIAGNOSIS.md src/
# 期望: 无新增 overclaim

# 确认 M2 judge 模型 ≠ reader
uv run python -c "
import json
v2 = json.loads(open('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json').read())
print('reader:', v2.get('reader_model'))
print('expected judge: minimax-m3 (ARK_MODEL in .env)')
print('reader == judge?', v2.get('reader_model') == 'minimax-m3')
"
# 期望: False (judge ≠ reader)

# 确认 ablation 同模型同预算
uv run python -c "
import json
v2 = json.loads(open('runs/publication/m13-longmemeval-test50-mimo-v2-factslot/summary.json').read())
ablation = json.loads(open('runs/publication/m13-longmemeval-test50-mimo-v2-ablation-no-temporal/summary.json').read())
print('v2 reader:', v2.get('reader_model'), 'ablation reader:', ablation.get('reader_model'))
print('v2 max_input_tokens:', v2.get('max_input_tokens'), 'ablation:', ablation.get('max_input_tokens'))
print('same model?', v2.get('reader_model') == ablation.get('reader_model'))
print('same budget?', v2.get('max_input_tokens') == ablation.get('max_input_tokens'))
"
# 期望: True / True (同模型同预算 — AGENTS.md 反 mixed-methods 规则)
```

## 验收标准（全部勾选才算 S3 完成）

- [ ] `benchmarks/mechanism/router_diagnosis.py` 存在且跑通，产出 confusion matrix
- [ ] router confusion matrix + 修改建议（非修改本身）写入 `docs/QEMR_FAILURE_DIAGNOSIS.md`
- [ ] 至少 2 个 weight ablation 跑完（`no_temporal`, `uniform`）
- [ ] embedding 对照实验完成或显式声明"因成本跳过，留 S5 决定"
- [ ] M2 子阶段：要么跑完且 judge 模型 ≠ reader 模型，要么显式声明"未跑 + SUPERSEDE>0 下的 auditability weakness"
- [ ] 诊断报告含明确根因结论（注：此项是 human-judgment process item，不列入"验证命令"——N10）
- [ ] `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q` 全绿
- [ ] `uv run ruff check .` 全绿
- [ ] `uv run mypy src` 全绿
- [ ] `uv run python -m evoeventmem.cli smoke` 输出 "smoke ok"
- [ ] `git diff src/evoeventmem/router.py` 为空（router 规则不动）
- [ ] `git diff src/evoeventmem/retrieval.py` 只新增 strategy enum + ablation 入口，`QEMR_WEIGHT_PROFILES` dict 不动
- [ ] M2 judge 模型 ≠ reader 模型（minimax-m3 ≠ mimo-v2.5）
- [ ] ablation 同模型同预算（AGENTS.md 反 mixed-methods 规则）
- [ ] 无新增 overclaim（"显著提升" / "thesis 翻盘" / "ETEC 有效" / "QEMR 有效"）
- [ ] 独立审查 PASS 或 CONDITIONAL PASS（`docs/STAGE3_REVIEW.md`）

## 验证命令（spec 复制）

```bash
uv run python -m benchmarks.mechanism.router_diagnosis --source-run runs/publication/m13-longmemeval-test50-mimo-v2-factslot
uv run pytest tests/mechanism -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
# 全套回归（S3 加 ablation 后必跑）:
uv run pytest tests/consolidation tests/retrieval tests/extraction tests/mechanism tests/benchmarks/test_etec_stress.py tests/benchmarks/test_extraction_smoke.py tests/benchmarks/test_s2_acceptance.py -q
```

## S3 验收失败的 fallback

**如果 router accuracy < 80%**：
1. 不在本阶段改 `router.py` 规则（N9 scope 边界）。
2. 在 `docs/QEMR_FAILURE_DIAGNOSIS.md` 写明：router accuracy = X%，per-class 误分类模式，修改建议（哪条 `_RELATIVE_RE` 应该加 / 哪条 weight profile 应该调）。
3. 路由到 S3 后的独立小任务（router 规则修改），需独立审查显式批准。

**如果 `qemr_no_temporal` > `qemr`（temporal source 有害）**：
1. 不改 production `QEMR_WEIGHT_PROFILES`（S3 scope）。
2. 在 `docs/QEMR_FAILURE_DIAGNOSIS.md` 写明：temporal source 在 LongMemEval 上有害，`qemr_no_temporal` EM = X% > `qemr` EM = Y%。
3. 路由到 S3 后的独立小任务（weight profile 调整），需独立审查显式批准。

**如果 mimo-v2.5 weekly usage limit 再次耗尽**：
1. 不强行用 fake reader 凑数。
2. 在 `docs/STAGE3_REVIEW.md` 写明：mimo-v2.5 API key 状态，HTTP 429 时间，配额恢复时间。
3. 已完成的 step 1-3 仍有效（router diagnosis 不需要 LLM；weight ablation 复用 v2 reader cache）；M2 标 BLOCKED，等配额恢复后续跑。

**如果 minimax-m3 (ARK_API_KEY) 不可用**：
1. M2 子阶段标 BLOCKED。
2. 在 `docs/QEMR_FAILURE_DIAGNOSIS.md` §4 显式声明："M2 未跑 + SUPERSEDE=109>0 下的 auditability weakness"。注明：judge 模型 minimax-m3 不可用，ARK API 错误码 / 配额状态。
3. S3 其他 step 仍可完成（step 1-3 不依赖 judge）；S5 论文 framing 把 M2 未跑写进 limitations。

**如果 embedding 对照实验成本超预算**：
1. 显式声明"因成本跳过 embedding 对照，留 S5 决定"。
2. 在 `docs/QEMR_FAILURE_DIAGNOSIS.md` §3 写明：embedding 对照实验跳过，原因（成本估算 / GPU 不可用 / 时间预算），S5 决策时是否补跑。

## 独立审查协议（S3 完成后必须执行）

S3 完成后，**派一个独立 subagent**（不审自己写的代码）执行以下检查，输出 `docs/STAGE3_REVIEW.md`：

1. **验收标准逐条勾选**：每条 acceptance criteria 都有 `验证命令` 输出截图。
2. **router confusion matrix 真实性**：独立重跑 `router_diagnosis.py`，确认 confusion matrix 与 `docs/QEMR_FAILURE_DIAGNOSIS.md` §1 一致。
3. **weight ablation 同模型同预算**：ablation run 的 `reader_model` == v2 run 的 `reader_model` == `mimo-v2.5`；`max_input_tokens` == 4096；不混模型不混预算（AGENTS.md 反 mixed-methods）。
4. **production 路径未破**：`git diff src/evoeventmem/router.py` 为空；`git diff src/evoeventmem/retrieval.py` 只新增 strategy enum + ablation 入口，`QEMR_WEIGHT_PROFILES` dict 不动；`tests/retrieval` 全绿。
5. **M2 judge 模型 ≠ reader**：M2 用 minimax-m3，reader 是 mimo-v2.5；judge inputs/outputs 已缓存。
6. **scope 边界守住**：`git diff --stat` 仅触及 `benchmarks/mechanism/router_diagnosis.py` + `benchmarks/mechanism/m2_stale_judge.py` + `configs/longmemeval/test50-mimo-v2-ablation.toml` + `docs/QEMR_FAILURE_DIAGNOSIS.md` + `docs/STAGE3_REVIEW.md` + `src/evoeventmem/retrieval.py`（新增 ablation 入口）+ tests；不碰 `src/evoeventmem/router.py`（不改规则）。
7. **未引入新 overclaim**：`rg -n '显著提升|significant improvement|outperform|thesis 翻盘|ETEC 有效|QEMR 有效' docs/QEMR_FAILURE_DIAGNOSIS.md src/` —— S3 不应声称 QEMR 有效或 thesis 翻盘。
8. **未跨模型对比 EM**：ablation EM 对比表只对比同模型同预算的不同 weight profile；不对比 mimo-v2.5 vs 其他模型。
9. **replay/online 一致性**（可选）：在 ablation run 上 replay，对比 online `ingestion.etec.actions`；若发散，记录为 known limitation。
10. **git 状态**：除 `runs/` 外工作区干净或变更可解释；每个 step 单独 commit（不一个大 commit 塞所有）。
11. **AGENTS.md 边界**：core memory logic 不依赖特定 vendor；evidence provenance 不破；UTC-aware datetime；小纯函数 + ports；不 commit datasets / secrets / model weights / benchmark caches。

审查输出 PASS / CONDITIONAL PASS / FAIL。FAIL 必须回到 S3 修复；CONDITIONAL PASS 可进 S5 但标注未决项。审查通过后才能进 S5。

## 完成后报告（必须包含）

1. **变更文件清单**：列出所有修改/新建的文件 + 每个文件的变更摘要（1-2 行）。
2. **Router 准确率**：confusion matrix + per-class precision/recall/F1 + 误分类模式 + accuracy %（80% 是 N9 阈值）。
3. **Weight ablation 对比表**：`qemr` vs `qemr_no_temporal` vs `qemr_no_graph` vs `qemr_uniform` 的 EM 对比表；是否 matches LoCoMo §9 (no_temporal > qemr)。
4. **Embedding 对照**（如做）：bge-large-en-v1.5 / e5-large-v2 vs qwen3-embedding-0.6b 的 `full` EM 对比；或"跳过 + 留 S5"声明。
5. **M2 stale-judge 结果**：full 更 fresh 占比 / event_no_etec 更 stale 占比 / tie；或"未跑 + auditability weakness"声明。
6. **根因结论**：QEMR 失效的根因（router / weights / embedding / temporal source / retrieval 不消费 SUPERSEDE / 其他）+ 修复建议或 pivot 建议。
7. **验收标准勾选**：15 条 acceptance criteria 逐条 ✅/❌/⚠️ + 验证命令输出。
8. **独立审查结果**：`docs/STAGE3_REVIEW.md` 的 PASS/CONDITIONAL PASS/FAIL 结论 + 关键发现。
9. **sanity check 结果**：pytest/ruff/mypy/smoke 四命令输出。
10. **异常/风险**（如有）：
    - mimo-v2.5 weekly usage limit 再次耗尽 → M2 BLOCKED
    - minimax-m3 不可用 → M2 BLOCKED + auditability weakness
    - embedding 对照成本超预算 → 跳过 + 留 S5
    - router accuracy < 80% → 路由到独立小任务（router 规则修改）
    - weight ablation 显示 QEMR 设计本身有问题 → S5 走分支 C 或 A
11. **下一阶段路由**：
    - 若 router accuracy ≥ 80% + weight ablation 显示 qemr 合理 + M2 显示 full fresh → S5 分支 B (positive thesis)
    - 若 router accuracy < 80% 或 qemr_no_temporal 赢了 → S5 分支 C (中间路线) 或 A (negative)
    - 若 M2 显示 full 给了 stale 答案 → 修 retrieval 的 temporal filter（S3 后独立小任务）

## 不做什么（防止 scope creep）

- 不开始 S4a/S5（S3 完成并 commit + 审查通过后才允许）。
- 不修 `router.py` 规则（N9 scope — 只产 confusion matrix + 修改建议）。
- 不改 production `QEMR_WEIGHT_PROFILES`（ablation 是新 strategy，不替换）。
- 不修 R3（`multi_valued` 过打）——不在 scope。
- 不动 prompt（sentinel 率 33.2% 是 known weakness，路由到 S5）。
- 不跑 500 题 / 不加新 benchmark。
- 不擅自写论文 draft（S5 的事）。
- 不声称 thesis 翻盘 / ETEC 有效 / QEMR 有效（即使 ablation 显示 QEMR 设计合理）——S3 只能说"50 题上测到 X%"。
- 不跨模型对比 EM（ablation 同模型同预算，只差 retrieval weight）。
- 不预先声明期望（预注册的 negative-result 框架）。
- 不静默 fallback（ablation 禁用 temporal/graph 必须可观察）。

## 故障排查

| 问题 | 解决 |
|---|---|
| mimo-v2.5 HTTP 429 / weekly limit 再次耗尽 | 换 API key（用户提供新 key 后写入 .env）；step 1-3 不依赖 reader LLM 可继续；M2 标 BLOCKED |
| minimax-m3 (ARK_API_KEY) 不可用 | M2 标 BLOCKED + auditability weakness 声明；其他 step 仍可完成 |
| embedding tunnel 断开 | `cpolar-ssh-update && ssh -o StrictHostKeyChecking=accept-new -f -N -L 11436:127.0.0.1:11436 gpu-5090` 重建 |
| embedding server OOM | 重启 GPU 服务（`ssh gpu-5090 'pkill -f qwen_embed_server; nohup /mnt/aidata/tongjiakai/llm-lifecycle-lab/.venv-serve/bin/python /mnt/aidata/tongjiakai/embed_server/qwen_embed_server.py --model-dir /mnt/aidata/tongjiakai/embed_server/qwen3-embedding-0.6b --port 11436 > /tmp/qwen_embed.log 2>&1 &'`）；BATCH_SIZE=8 + max_length=2048 已配置 |
| ablation run 遇 manifest drift | 用 per-batch sub-dir 模式（参照 `scripts/s2-resume-sequential.sh`），每批独立 run-dir 避免漂移 |
| router accuracy = 100% （gold label 映射错误） | 检查 LongMemEval question_type → QueryIntent 映射；可能映射过粗，需细分 |
| weight ablation 全部输给 qemr | 这是正面发现 — QEMR 设计合理，问题不在 weight；继续 step 3-4 找其他根因 |

## 预计时间

- 3-5 天（spec line 339），单窗口可完成。
- Step 0（前置 + 文献）：0.5 天。
- Step 1（router diagnosis）：0.5-1 天（实现 + 测试 + 跑）。
- Step 2（weight ablation）：1-2 天（实现 ablation 入口 + 跑 3 个 ablation run + 对比）。
- Step 3（embedding 对照）：1-2 天（如做；可选）。
- Step 4（M2 stale-judge）：1 天（实现 + 跑 judge + 缓存）。
- Step 5（写报告）：0.5 天。
- Step 6-7（回归 + 扫描）：0.5 天。
- 独立审查：1-2 小时。

## 文献依据

- **LongMemEval §5.4** (arXiv:2410.10813, ICLR 2025): "time-aware query expansion +6.8%~11.3% temporal reasoning" —— 如果 router 把 temporal 类 query 分错，QEMR 的 temporal weight 就完全错位。**注：具体百分比来自论文正文 §5.4，独立审查未从摘要验证，按定性"reported positive gains"采纳**。S3 step 1 的 router accuracy 测量是验证这个假设的关键。
- **Filesystem-Based Memory** (arXiv:2607.26637): "no agent converts organization into better answers" —— 警示 QEMR 的 query-adaptive 可能在 LongMemEval 上无 surface。S3 step 2 的 weight ablation 是验证这个假设的关键：如果 `qemr_uniform` > `qemr`，组织（weight profile）确实没买到更好的答案。
- **LoCoMo §9** (本项目 `runs/main/report/report.md` C01): `no_temporal` (0.3654) > `qemr` (0.3000) → 在 LongMemEval 上是否也成立？S3 step 2 直接对比。
- **Mem0** (arXiv:2504.19413): graph memory 仅 +2% over base —— 警示结构化收益有限；与 S2 的 `full` EM +0.02 一致。
- **审计 `9of10_AUDIT.md:47-54`**: "结构性 null"防御在 SUPERSEDE>0 时立即失效 → S3 M2 是 positive thesis 的必要证据（B2 修复）；judge 模型 ≠ mimo-v2.5 reader（B4 / N8）。

## 历史阶段路由回顾

- S0 (止血) ✅ DONE commit `b60b38d`
- S1a (schema + prompt v2) ✅ DONE commit `162183c`
- S1b (5q smoke + reachability) ✅ DONE commit `00b3dc6`
- S1c (required fact_slot + v3 prompt) ✅ DONE commit `ab5ba1a` (CONDITIONAL PASS — sentinel 39.7%)
- S4b (vector_rag 延迟修复) ✅ DONE commit `46b7b38`
- S2 (50q v2 run + 诊断) ✅ DONE commit `17b1014` (CONDITIONAL PASS — SUPERSEDE=109, full EM +0.02, sentinel 33.2%)
- **S3 (QEMR diagnosis + M2) ← 本阶段**
- S4a (可复现性 config + docs) — 可与 S3 并行（纯文档 + config，无代码依赖）
- S5 (定稿 + 论文 / 报告 draft) — 等 S3 完成

## 关键路径决策回顾

S2 结果触发的 S3 路由（spec line 274）：
- ✅ **SUPERSEDE > 0 (109)** → S3 必跑 M2 子阶段（B2 修复触发条件）
- ⚠️ **full EM 没翻盘 (+0.02 only)** → S3 走中间路线（spec line 460-463）：诊断为什么 QEMR 未能把 109 个 superseded memories 转化成 reader 可见的增益
- ⚠️ **sentinel 率 33.2% ≥ 20%** → 路由到 S3/S5 决策（不在 S3 调 prompt，AGENTS.md 反 fishing）

S3 完成后路由到 S5（spec line 444-470）：
- 分支 A (negative-result): SUPERSEDE = 0 或 R3 阻塞 > 50% 或 QEMR 设计本身有问题 → S5 走分支 A
- 分支 B (positive): SUPERSEDE > 0 + full EM 翻盘 + M2 显示 full fresh + QEMR 设计合理 → S5 走分支 B
- 分支 C (中间): SUPERSEDE > 0 但 full EM 不翻盘 → S5 走分支 C（"ETEC 的 SUPERSEDE 在真实数据上可达但不足以提升整体准确率"）
- 分支 D (infra 失败): S3 因 mimo / minimax / embedding 不可用无法完成 → S5 走分支 D（回退 v1 数据）
