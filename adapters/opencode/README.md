# OpenCode adapter plan

主线 M17 实现。推荐形态：EvoEventMem 暴露 MCP 工具，OpenCode 作为 Agent Host。

建议工具保持在 4–6 个高层接口：

- `memory_search(query, user_id, budget)`；
- `memory_observe(observation, source)`；
- `memory_explain(memory_id)`；
- `memory_timeline(entity_or_topic)`；
- `memory_feedback(memory_id, outcome)`；
- `memory_forget(memory_id)`。

示例项目配置最终写入 `opencode.json`，但在 M17 前不固定未验证的命令或 npm 包名。适配器只调用 Memory Service，不复制 Python 算法。

主动检索由 MCP tool 完成；如需自动捕获 session/tool 事件，可增加极薄的 OpenCode plugin/hook，将轨迹发送到 `memory_observe`。先完成 MCP，再评估 hook。
