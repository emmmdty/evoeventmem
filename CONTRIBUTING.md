# Contributing

## Workflow

1. 从 `TASKS.md` 选择一个任务 ID。
2. 创建分支 `task/<id>-<slug>`。
3. 不要在主线任务中混入附加优化工作。
4. 运行任务特定的验证命令 + `make check`（如可用）。
5. 指标声明必须附带生成的基准产物，但不要提交原始数据集或密钥。

## 代码标准

- Python 3.11+，完整类型标注。
- 领域层和服务层不导入 FastAPI 或数据库客户端。
- 测试覆盖行为，不覆盖实现细节。
- 变更足够小，可在一次 diff 中审查。

## 提交规范

- 提交信息格式：`type(scope): description`
- 类型：`feat`, `fix`, `bench`, `docs`, `refactor`, `test`, `chore`
- 不提交 `.env`、数据集、模型权重、基准缓存产物。
