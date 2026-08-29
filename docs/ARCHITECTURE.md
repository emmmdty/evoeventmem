# 架构设计

## 1. 逻辑架构

```text
Input adapters
├── Benchmark runner
├── Reference Agent
└── OpenCode MCP adapter
          │
          ▼
Memory application service
├── write(observation)
├── search(query, budget)
├── explain(memory_id)
├── feedback(memory_id, outcome)
└── forget(memory_id)
          │
          ├── Write pipeline
          │   ├── candidate extraction (LLM + deterministic span)
          │   ├── entity/event linking
          │   ├── ETEC decision (ADD/MERGE/SUPERSEDE/REJECT)
          │   └── provenance persistence
          │
          └── Read pipeline
              ├── query routing (rule-based)
              ├── candidate retrieval (vector + temporal + graph)
              ├── QEMR reranking (dynamic weight profiles)
              └── budget-aware packing
          │
          ▼
Storage ports
├── relational event store (PostgreSQL)
├── vector index (pgvector)
├── graph-edge store (edge table + recursive query)
└── experiment/event log
```

## 2. 分层边界

| 层 | 职责 | 禁止依赖 |
|---|---|---|
| `domain` | 纯数据结构和不变量 | 无外部依赖 |
| `core` | repository、model、embedding、clock 等端口 | infra、api、adapters |
| `services` | 写入、整合、检索、解释用例 | FastAPI、数据库客户端 |
| `infra` | PostgreSQL、pgvector、API client、缓存实现 | adapters |
| `api` | HTTP/MCP transport | 业务逻辑 |
| `adapters` | Agent Runtime 接入（OpenCode MCP, Pi） | 内部模块，仅用公共接口 |
| `benchmarks` | 数据转换、运行器和评测 | 不反向依赖 adapter |

## 3. 记忆数据模型

```text
memory_id, tenant_id, user_id, session_id
memory_kind: fact/event/episode/procedure
content, normalized_content
entities, roles, relations
event_time, valid_from, valid_to
status: active/superseded/rejected/deleted
supersedes, derived_from
evidence_refs (locator=chars=X:Y)
confidence, utility, embedding_version
created_at, updated_at
```

证据引用必须能够定位：数据集样本、session、turn、message、tool call 或文件片段。

## 4. 存储取向

主线使用 PostgreSQL + pgvector：

- 降低组件数量
- 事务内同时更新事件、版本、证据和向量元数据
- 图关系先用 edge table 与递归查询/应用层遍历
- 只有在主线完成后，才增加 Neo4j/FalkorDB 适配作为附加优化

## 5. 模型边界

模型只通过端口调用：

```python
class ChatModel: ...
class EmbeddingModel: ...
class Reranker: ...
```

算法不得依赖某个供应商特有响应格式。API 模型用于快速建立上限，本地 OpenAI-compatible 模型用于复现、隐私和成本实验。

## 6. 公平评测约束

每个方法共享：

- 相同 reader/answer model
- 相同 embedding/reranker（除非该组件本身是被研究变量）
- 相同最大输入 token 与检索条数
- 相同 prompt version
- 相同数据 split
- 相同 judge 与缓存

## 7. 多租户隔离

- `RequestScope`（tenant_id + user_id 必填，session_id 可选）——API 层强制
- 所有 UUID 查询必须携带 scope；跨租户返回 404（不泄露存在性）
- PostgreSQL 层：`tenant_id/user_id/session_id` 参与每条 SQL 过滤
- 测试覆盖：`tests/api/test_request_scope.py`、`tests/infra/test_async_repository_contract.py`
