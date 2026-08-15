# OpenCode adapter（M17）

EvoEventMem 通过 MCP 向 OpenCode 暴露 6 个高层记忆工具。适配器是纯翻译层：只调用
`AsyncMemoryService` 的公共方法（`write` / `search` / `explain` / `feedback` /
`forget`），不实现提取、ETEC、QEMR 或持久化逻辑；每个工具返回稳定 JSON 信封，
服务故障时返回可解析的 fallback，不中断 Agent 循环。

## 工具面（稳定且已测试）

| 工具 | 作用 | 对应服务方法 |
|---|---|---|
| `memory_search(query, user_id, limit, ...)` | 混合检索，返回命中内容、分数、检索原因与证据引用 | `search` |
| `memory_observe(observation, source_type, source_id, ...)` | 将观察写入为带证据溯源的可持久记忆；内容重复时幂等返回已有记忆 | `write` |
| `memory_explain(memory_id, ...)` | 解释一条记忆：内容、证据、时间/派生关联的记忆 | `explain` |
| `memory_timeline(entity_or_topic, ...)` | 按事件时间排序展示检索到的记忆（检索仍由服务完成，排序只是呈现） | `search` |
| `memory_feedback(memory_id, outcome, rating, ...)` | 记录记忆对当前任务是否有用 | `feedback` |
| `memory_forget(memory_id, ...)` | 遗忘记忆，不再出现在检索中 | `forget` |

所有工具返回统一信封：

```json
{"status": "ok|not_found|unavailable|error", "message": "...", "data": {...}}
```

- 服务故障（`store_unavailable` / `embedding_unavailable` / `internal_error`）→
  `status: "unavailable"`，消息明确告知 Agent 本轮跳过记忆检索继续执行；
- 客户端错误（无效 memory_id、scope 冲突、记忆 ID 冲突）→ `status: "error"`；
- 目标记忆不存在或越权 → `status: "not_found"`。

原因码复用 `src/evoeventmem/infra/failures.py` 的稳定常量，与 HTTP API 一致。

## 接入步骤（可复现）

1. 同步依赖并安装 MCP SDK（`mcp` extra）：

   ```bash
   uv sync --extra dev --extra mcp
   ```

2. 把 `opencode.json.example` 复制为项目根目录的 `opencode.json`：

   ```bash
   cp adapters/opencode/opencode.json.example opencode.json
   ```

   配置通过 stdio 启动本地 MCP server：

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "mcp": {
       "evoeventmem": {
         "type": "local",
         "command": ["uv", "run", "--extra", "mcp", "python", "-m", "adapters.opencode.mcp_server"],
         "enabled": true
       }
     }
   }
   ```

3. 启动 OpenCode 后检查 MCP 是否连上：

   ```bash
   opencode mcp list
   ```

   Agent 即可使用 `memory_search` 等工具。默认 `tenant_id="default"`，可通过
   工具参数覆盖；`user_id` 为必填作用域参数。

   默认 stdio 入口（`adapters/opencode/mcp_server.py:main`）是开发模式：内存
   存储 + 确定性 embedding，策略读取 `EEM_*` 环境变量（默认 `vector`，可设
   `EEM_EMBEDDING_POLICY=token_overlap` 获得可读的关键词排序）。生产部署通过
   `build_server(service)` 注入真实服务（如 M16 的 PostgreSQL + 生产 embedding）。

4. 验证服务器本身可运行（stdio 握手）：

   ```bash
   printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}\n' | uv run --extra mcp python -m adapters.opencode.mcp_server
   ```

## 演示脚本（trace capture）

`demo.py` 模拟一次 Coding/Debug 会话：报告 bug → `memory_search` 检索到相关
记忆与证据 → `memory_explain` 展示时间关联 → Agent 依据记忆采取行动 →
`memory_observe` 记录结果 → `memory_feedback` 反馈 → 再次检索确认。全程不使用
真实模型：使用确定性 token-overlap 开发策略（`token_overlap_policy=True`，
检索命中的 `fallback=True` 与原因可见），保证关键词排序可读、输出可复现。

```bash
uv run python -m adapters.opencode.demo --trace-out /tmp/evoeventmem-demo.jsonl
```

`--trace-out` 将本次会话的 JSONL 轨迹写入指定路径（默认不写文件）。

## 主动检索与可选 hook 策略

- 主动检索（proactive retrieval）：由 `memory_search` / `memory_timeline` 两个
  MCP 工具完成，Agent 在需要时显式调用，无需运行时钩子。
- 可选生命周期 hook（未实现）：如需自动捕获 session/tool 事件，可在
  OpenCode plugin 中挂载极薄的事件钩子，把轨迹发送到 `memory_observe`
  （`source_type="opencode"`）。该 hook 不包含任何记忆算法，只是把观察转发给
  MCP 工具；在确认需要自动捕获之前保持未实现，避免无端扩大改动面。

## 测试

`tests/adapters/test_mcp.py` 使用 Fake Memory Service（可注入服务故障），通过
进程内 MCP 会话端到端验证工具 schema、行为与 fallback：

```bash
uv run pytest -q tests/adapters/test_mcp.py
```
