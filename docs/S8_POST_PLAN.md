# S8 后续任务计划

> 基于 S8 分层验证结果 + 3 路独立调查（代码质量 / 方法论 / 生产就绪性）
> 日期：2026-08-25

## 当前状态

- **项目定位**：分支 C（中间路线，维持）
- **S8 关键发现**：ETEC SUPERSEDE 在 knowledge-update 上有效（+0.334），在 temporal-reasoning 上有害（-0.111），主场合并 delta=0.000
- **代码质量**：mypy/ruff 全通过，1013 测试，无循环导入
- **生产就绪**：研究原型级别，缺认证/CI/速率限制

## 任务清单

### Phase 1：阻塞项（必须先做）

#### T1: 选择性 SUPERSEDE（修复 ETEC 在 TR 上的有害效果）
- **优先级**：P0
- **问题**：ETEC SUPERSEDE 在 temporal-reasoning 上有害（0.111 vs vector_rag 0.222），因为替换旧值丢失了排序所需的历史上下文
- **方案**：当 router 判定 temporal-reasoning 时，跳过 SUPERSEDE（保留历史值供排序）
- **预期**：KU 保持 0.467，TR 从 0.111 恢复到 ~0.148+，主场 delta 可能转正
- **工件**：修改 `src/evoeventmem/consolidation.py` 的 `_apply_supersede()`，添加 router intent 检查
- **验证**：重新跑 S8 分层 100q，比较 TR 子集 EM
- **预估**：3-5 天

#### T2: API 认证（生产部署前提）
- **优先级**：P0
- **问题**：所有端点无认证，任何人可读写删数据
- **方案**：添加 Bearer token 验证 middleware，最少静态 token
- **工件**：`src/evoeventmem/api/auth.py`，修改 `app.py`
- **验证**：无 token 返回 401，有 token 正常访问
- **预估**：2-3 天

#### T3: CI/CD 配置
- **优先级**：P0
- **问题**：无自动化质量门禁，每次改动都是赌博
- **方案**：添加 `.github/workflows/ci.yml`（ruff + mypy + pytest + Docker build）
- **工件**：`.github/workflows/ci.yml`
- **验证**：PR 触发 CI，所有检查通过
- **预估**：1-2 天

### Phase 2：重要改进（1-2 周）

#### T4: MDE 计算显式化
- **优先级**：P1
- **问题**：n=100 MDE=0.14 远大于阈值 0.05，C+/C/D 判断不稳定
- **方案**：在 S8-PREREGISTRATION.md 补充 MDE 公式、假设、caveat
- **工件**：修改 `docs/S8-PREREGISTRATION.md`
- **验证**：文档审查
- **预估**：0.5 天

#### T5: full_context baseline 验证
- **优先级**：P1
- **问题**：EM=0.00 解释存疑（是 reader 能力问题还是信息缺失？）
- **方案**：增加 unlimited-budget 变体或 token_f1 breakdown
- **工件**：修改 `benchmarks/context_baselines.py`，新增测试
- **验证**：token_f1 > 0 证明部分信息在 truncated context 中
- **预估**：2-3 天

#### T6: router 死代码修复
- **优先级**：P1
- **问题**：`_detect_temporal_constraint` 有不可达分支（语义 bug）
- **方案**：重排检查顺序，让 "temporal relation without date" 在 year-specific 检查后立即执行
- **工件**：修改 `src/evoeventmem/router.py`
- **验证**：新增边界测试用例
- **预估**：1 天

#### T7: 多重比较校正
- **优先级**：P1
- **问题**：per-category EM 未做多重比较校正
- **方案**：在 S8 报告中标注探索性，或对 6 category 做 Holm 校正
- **工件**：修改 `docs/S8-STRATIFIED_VALIDATION_REPORT.md`
- **验证**：文档审查
- **预估**：0.5 天

### Phase 3：代码质量（2-4 周）

#### T8: 提取共享工具函数
- **优先级**：P2
- **问题**：`_jaccard()`、`_cosine_similarity()`、`_unique_evidence()` 在 3 个模块重复
- **方案**：提取到 `src/evoeventmem/core/math_utils.py`
- **工件**：新模块 + 修改 import
- **验证**：所有现有测试通过
- **预估**：1-2 天

#### T9: 拆分 300 行 prompt 函数
- **优先级**：P2
- **问题**：`_build_llm_prompt()` 300 行，不可测试不可维护
- **方案**：拆分为 `_build_schema_section()`、`_build_fact_slot_rules()`、`_build_examples()` 等
- **工件**：修改 `src/evoeventmem/extraction.py`
- **验证**：现有提取测试通过 + 新增 section 测试
- **预估**：2-3 天

#### T10: 修复 `Any` 类型滥用
- **优先级**：P2
- **问题**：`ExtractionInput.from_normalized_record(record: Any)` 等关键边界用 Any
- **方案**：定义 Protocol 或使用实际类型
- **工件**：修改 `src/evoeventmem/extraction.py`、`src/evoeventmem/infra/service_factory.py`
- **验证**：mypy 通过
- **预估**：1-2 天

#### T11: 清理死 regex 和 dead code
- **优先级**：P2
- **问题**：`_MONTH_RE`、`_TO_YEAR_RE` 未使用；`RetrievalRequest`/`RetrievalResult` 是死代码
- **方案**：删除未使用定义
- **工件**：修改 `src/evoeventmem/router.py`、`src/evoeventmem/retrieval.py`
- **验证**：所有测试通过
- **预估**：0.5 天

#### T12: 补充 facade 测试
- **优先级**：P2
- **问题**：`QueryRouterService` 和 `RetrievalService` 无测试
- **方案**：新增 `tests/retrieval/test_retrieval_service.py`
- **工件**：新测试文件
- **验证**：`uv run pytest tests/retrieval/test_retrieval_service.py`
- **预估**：1 天

### Phase 4：生产就绪（1-2 个月）

#### T13: 速率限制
- **优先级**：P1
- **问题**：无限制，可被 DoS
- **方案**：基于 tenant_id 的令牌桶限流（slowapi 或 Redis-backed）
- **工件**：`src/evoeventmem/api/ratelimit.py`，修改 `app.py`
- **验证**：超限返回 429
- **预估**：2-3 天

#### T14: 写入去重下沉到 DB 层
- **优先级**：P1
- **问题**：每次写入 O(n) 全表扫描去重
- **方案**：在 memories 表添加 `(tenant_id, user_id, normalized_content)` 索引
- **工件**：新增迁移文件，修改 repository
- **验证**：写入延迟 < 10ms（P99）
- **预估**：2-3 天

#### T15: Docker 安全加固
- **优先级**：P1
- **问题**：容器以 root 运行，无 .dockerignore
- **方案**：添加非 root 用户 + .dockerignore + layer cache 优化
- **工件**：修改 `Dockerfile`，新增 `.dockerignore`
- **验证**：`docker run --user` 验证非 root
- **预估**：1 天

#### T16: 分布式追踪
- **优先级**：P2
- **问题**：无 OpenTelemetry 集成
- **方案**：集成 opentelemetry-sdk + instrumentation-fastapi
- **工件**：`src/evoeventmem/infra/tracing.py`，修改 `app.py`
- **验证**：Jaeger UI 可见 span
- **预估**：3-5 天

#### T17: API DTO 隔离
- **优先级**：P2
- **问题**：POST /v1/memories 接收完整 domain model
- **方案**：定义 `V1WriteMemoryRequest` DTO，仅暴露合法输入字段
- **工件**：`src/evoeventmem/api/dto.py`，修改 `app.py`
- **验证**：内部字段（memory_id、created_at）不可由客户端指定
- **预估**：1-2 天

#### T18: explain 端点错误处理
- **优先级**：P2
- **问题**：explain 端点缺少 RepositoryUnavailableError 处理
- **方案**：添加 try/except 转换为 503
- **工件**：修改 `src/evoeventmem/api/app.py`
- **验证**：PostgreSQL 不可用时返回 503
- **预估**：0.5 天

### Phase 5：长期改进（3 个月+）

#### T19: 500q 稳定性确认 → T19a: 小样本性能验证
- **优先级**：P3
- **状态**：DONE (T19a)
- **方案**：先在 n=100 上验证效果方向和效应量，若方向一致但 CI 宽，再扩展到 n=200-300
- **工件**：`benchmarks/small_sample_analysis.py`
- **验证**：分析框架包含 bootstrap CI、Cohen's d、样本量建议

#### T20: session_summary baseline
- **优先级**：P3
- **状态**：DONE
- **方案**：将 session 内所有记忆内容拼接为 summary，用 summary 回答 query
- **工件**：`benchmarks/context_baselines.py` 新增 `SessionSummaryBuilder`
- **验证**：在 baseline 注册表中添加 session_summary，ruff/mypy 通过

#### T21: 认证升级（OAuth2/OIDC）
- **优先级**：P3
- **状态**：DONE
- **方案**：集成 OAuth2/OIDC（python-jose），保持静态 token 向后兼容
- **工件**：`src/evoeventmem/api/auth.py` 重写，`src/evoeventmem/infra/config.py` 新增配置
- **验证**：JWT 验证通过，18 个测试全部通过

#### T22: Kubernetes 部署
- **优先级**：P3
- **状态**：DONE
- **方案**：添加完整 K8s manifests（namespace, secrets, deployment, service, ingress, hpa, postgres, kustomization）
- **工件**：`k8s/` 目录（8 个 YAML 文件）
- **验证**：所有 YAML 文件语法验证通过

## 执行顺序

```
Phase 1 (阻塞项):
  T1 (选择性 SUPERSEDE) ──→ T4 (MDE 显式化) ──→ T7 (多重比较)
  T2 (API 认证) ──→ T3 (CI/CD)

Phase 2 (重要改进):
  T5 (baseline 验证) ──→ T6 (router 死代码)

Phase 3 (代码质量):
  T8 (共享工具) ──→ T9 (prompt 拆分) ──→ T10 (Any 修复)
  T11 (dead code) ──→ T12 (facade 测试)

Phase 4 (生产就绪):
  T13 (速率限制) ──→ T14 (DB 去重) ──→ T15 (Docker 加固)
  T16 (追踪) ──→ T17 (DTO) ──→ T18 (错误处理)

Phase 5 (长期):
  T19a (小样本分析) ✅ ──→ T20 (session_summary) ✅
  T21 (OAuth2) ✅ ──→ T22 (K8s) ✅
```

## 资源估算

| Phase | 预估工时 | 依赖 |
|---|---|---|
| Phase 1 | 6-10 天 | 无外部依赖 |
| Phase 2 | 4-7 天 | Phase 1 |
| Phase 3 | 5-9 天 | 无外部依赖 |
| Phase 4 | 10-17 天 | Phase 1-2 |
| Phase 5 | 10-15 天 | Phase 1-4 |

**总计**：35-58 天（1-2 人月）
