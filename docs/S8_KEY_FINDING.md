# S8 关键发现：ETEC SUPERSEDE 的设计 Trade-off

> 日期：2026-08-25
> 样本：S8 分层 100q（LongMemEval-S 真实分布，seed=42）
> 决策：分支 C 维持（delta=+0.000 < 0.05 阈值）

## 核心发现

ETEC SUPERSEDE 机制存在**内在设计矛盾**：在消除过期值的同时，也消除了时序推理所需的历史信息。

### Per-question_type EM（S8 分层 100q）

| 类型 | n | vector_rag | full | etec | etec vs vr |
|---|---|---|---|---|---|
| knowledge-update | 15 | 0.133 | 0.267 | **0.467** | **+0.334** |
| temporal-reasoning | 27 | **0.222** | 0.148 | 0.111 | **-0.111** |
| multi-session | 27 | 0.222 | 0.185 | 0.185 | -0.037 |
| single-session-user | 14 | **0.500** | 0.357 | 0.429 | -0.071 |
| single-session-assistant | 11 | 0.273 | 0.182 | 0.182 | -0.091 |
| single-session-preference | 6 | 0.000 | 0.000 | 0.000 | 0.000 |

### ETEC 主场（TR+KU, n=42）

- vector_rag: 0.190
- full: 0.190
- etec: 0.238
- **full vs vr delta: +0.000** → 分支 C 维持

### Trade-off 机制

1. **knowledge-update（问"现在的值是什么"）**：SUPERSEDE 有效（+0.334）
   - 旧值被替换 → reader 只看到正确新值 → EM 提升

2. **temporal-reasoning（问"哪个先发生"）**：SUPERSEDE 有害（-0.111）
   - 旧值被替换 → reader 丢失排序所需的历史上下文 → EM 下降

3. **两者在主场合并指标上互相抵消**（delta=+0.000）

### 下一步研究方向

1. **选择性 SUPERSEDE**：temporal-reasoning 查询时不执行 SUPERSEDE（保留历史值供排序）
2. **时序感知合并**：SUPERSEDE 保留旧值的 valid_to 时间戳，让 reader 可以看到时间线
3. **router 感知 ETEC**：当 router 判定 temporal-reasoning 时，跳过 SUPERSEDE 或降级为 MERGE
4. **更大的 n**：500q 稳定性确认（optional future-work）
