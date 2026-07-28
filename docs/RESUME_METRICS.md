# 简历与面试指标模板

只有实验脚本生成并可复现的数据才能填入括号。

## Agent 算法版

设计并实现框架无关的时态事件图谱 Agent Memory Service，提出 ETEC 证据约束记忆整合和 QEMR 查询自适应混合检索；在 LongMemEval/LoCoMo 上相较 Vector RAG 将【总体指标】提升【X】，时间/知识更新子集提升【Y】，stale-memory error 降低【Z】；完成【N】项消融和逐类型错误分析。

## Agent 开发版

实现可独立部署的 Agent Memory Service，支持 FastAPI、MCP、PostgreSQL/pgvector 与 API/本地模型双后端；完成 OpenCode 接入、异步写入、证据追踪和降级观测，在【硬件】上达到检索 p95【X ms】、写入 p95【Y ms】、单请求 token 降低【Z%】。

## 面试必须能解释

- 为什么向量相似不能处理 valid time；
- supersede 与 contradiction 的差别；
- 如何避免 judge 泄漏和基线不公平；
- QEMR 如何在 token budget 下选择证据；
- 结果提升来自哪个模块；
- 失败案例与下一步。
