# 主线执行方案

主线只有一个目标：证明“证据感知时态事件记忆”在公开基准上相较强基线具有可归因的收益，并完成 OpenCode 工程接入。

## 阶段 A：评测地基

- M01–M05。
- 先完成数据规范化、指标、No Memory、Full Context、Vector RAG。
- 此阶段不实现事件图算法，以免在没有可靠评测器时制造不可验证代码。

退出条件：一个固定小样本可以一条命令运行，生成逐样本 prediction、evidence、latency、token 和聚合指标。

## 阶段 B：写入算法

- M06–M10。
- 建立事件/证据/时间 schema，完成抽取、去重、实体对齐和 ETEC。

退出条件：人工构造与公开样本上的 ADD/MERGE/SUPERSEDE/REJECT 决策可测试；能报告事件合并 F1、冲突更新准确率和 stale-memory error。

## 阶段 C：读取算法

- M11–M12。
- 先规则路由，再混合候选与预算打包；不要一开始训练 router。

退出条件：在固定 token budget 下，能比较 fixed-vector、fixed-hybrid 和 QEMR；每条返回结果含可追踪 evidence。

## 阶段 D：公开实验

- M13–M15。
- LongMemEval 为时间/更新主基准；LoCoMo 为事件、多跳和证据主基准。

退出条件：主结果、消融、分类型结果、效率结果和错误案例均由脚本生成。

## 阶段 E：工程化与求职包装

- M16–M18。
- API、追踪、OpenCode MCP、Demo、复现和简历材料。

退出条件：新环境按 README 可跑 smoke benchmark；OpenCode 能主动检索记忆并展示来源；简历只写真实指标。
