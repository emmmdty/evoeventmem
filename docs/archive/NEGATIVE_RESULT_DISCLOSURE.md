# Negative Result Disclosure

EvoEventMem 的两个研究贡献——ETEC（证据约束 consolidation）和 QEMR（查询自适应检索）——在 LongMemEval (n=50, mimo-v2.5) 和 LoCoMo (n=1986) 上都没提升准确率。flagship `full` (ETEC+QEMR) 在 50 题上 EM=0.46，是所有记忆方法里最差（vector_rag=0.56）；拆掉 ETEC 或 QEMR 各自都提升准确率（+8 / +6 EM）。ETEC 的 SUPERSEDE 在真实数据上结构性不可达（extraction 不产 fact_slot/valid_from，0/8 测量 + 0/24 外推）。LoCoMo 上 `full`=0.0634 显著劣于 `vector_rag`=0.0861（p=0.000）。

整改 spec（`docs/REMEDIATION_SPEC.md` v1.1，独立审查 PASS）规划 6 阶段修复：S0 诚信披露 → S1a/S1b 修 ETEC 第一道闸门 → S2 重跑诊断 → S3 QEMR 失效根因 → S4 可复现性 → S5 定稿。当前状态：S0 执行完毕。正面贡献保留：100% provenance coverage、33/33 failure attribution、不可篡改 FINALIZED.json——记忆系统需要可审计，不只是准确。
