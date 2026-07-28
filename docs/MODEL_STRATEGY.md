# 模型策略

## 主线

不训练主 Agent 模型。采用外部记忆研究设置，模型通过统一 gateway 接入。

### Cloud profile

强 API 模型负责事件抽取、回答和必要的 judge，用于快速建立方法上限。

### Hybrid profile

API reader + 本地 embedding/reranker/extractor，用于成本和工程可用性。

### Local profile

本地 OpenAI-compatible chat/embedding endpoint，用于可复现、隐私和单卡部署实验。

## 算力适配

单卡 RTX 5090 或 1–2 张 RTX 4090 足以运行：

- 7B–14B 量化 chat/extractor；
- embedding 与 reranker；
- LoRA 级轻量 router（可选）；
- 两个主基准的顺序实验。

不在主线进行基础模型 SFT、完整 RL 或自训练 embedding。

## 模型角色拆分

```text
reader/agent brain  强 API 或较强本地模型
memory extractor    API 或 7B–14B 本地模型
query router        规则优先，O01 再训练
embedding           本地模型
reranker            本地模型
judge               与被评模型隔离，缓存输出
```
