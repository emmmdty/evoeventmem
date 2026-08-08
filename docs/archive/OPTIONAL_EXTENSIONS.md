# 附加优化项目

以下内容必须在 M15 主结果稳定后再启动。每次只选一项，不得影响主线复现。

## O01 轻量学习式 Query Router

用规则路由生成弱标签，人工修订一小部分；训练分类器或 0.5B–3B LoRA。评价 Router Macro-F1、端到端提升、延迟和 token 成本。价值：强化算法岗叙事。

## O02 程序性记忆与 Skill 提炼

从成功/失败 Coding Agent 轨迹提炼适用条件、步骤、验证和回退；只有 replay 通过后才晋升为 procedure。价值：衔接 Agent 自进化与 Skills。

## O03 LongMemEval-V2 子集

验证环境经验、workflow knowledge 和 gotcha 记忆。数据规模很大，只使用 small tier 或自定义子集。价值：从聊天记忆扩展到执行经验。

## O04 EvoMemBench

选择一个 knowledge 和一个 execution setting，不完整复现全部方法。价值：验证记忆形式与任务结构匹配。

## O05 Pi Adapter

实现 Pi TypeScript extension，证明框架无关性。仅在 OpenCode MCP 稳定后进行。

## O06 图数据库后端

增加 Neo4j/FalkorDB repository，比较查询复杂度、延迟和运维成本。除非 PostgreSQL edge traversal 已成为瓶颈，否则不做。

## O07 Memory Inspector

展示 timeline、supersedes、evidence、召回评分和失败案例。前端只服务于解释与 Demo，不做通用管理后台。

## O08 隐私与多租户

字段级脱敏、tenant isolation、TTL、可删除性和审计。适合偏 Agent Infra/平台岗位。
