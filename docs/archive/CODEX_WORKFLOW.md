# 使用 OpenAI Codex 推进项目

本文按 2026 年 7 月 Codex 官方能力设计：仓库级 `AGENTS.md`、repo skills、project-scoped subagents、worktrees、交互式会话和 `codex exec`。

## 1. 核心原则

1. 一个会话只处理一个任务 ID。
2. 多步骤任务先进入计划，再编辑。
3. 可并行的是探索、测试和审查；不要让多个 agent 同时修改同一模块。
4. 长期规则放 `AGENTS.md` 或 Skill，不在每个 prompt 中重复。
5. 每个任务必须有明确非目标、允许文件、验收标准和验证命令。
6. 验收失败时，只修复当前任务；不要顺手开始下一个任务。

## 2. 推荐流程

```bash
# 1. 查看任务
python scripts/taskctl.py list
python scripts/taskctl.py show M01

# 2. 新建分支
 git switch -c task/M01-dataset-manifest

# 3. 生成任务提示
python scripts/taskctl.py prompt M01

# 4. 在 Codex app/CLI 新会话中执行
codex

# 5. 人工检查 diff 后运行验收
make check

# 6. 让独立 reviewer 只读审查
# 提示：Use the reviewer subagent to review task M01 against its acceptance criteria.
```

Codex 桌面端并行工作时，使用 Git worktree；一个 worktree 对应一个互不冲突的任务。依赖关系存在的 M06→M07→M08 不要并行。

## 3. 何时使用 subagent

适合：

- `explorer`：定位现有实现和依赖，不改代码；
- `tester`：为已明确行为补测试或运行失败定位；
- `reviewer`：按任务验收标准审查 diff。

不适合：

- 把整个 M01–M18 同时委派；
- 多个 worker 修改同一文件；
- 未定义接口前让多个 agent 各自实现一套；
- 把所有日志和搜索结果回灌到主会话。

## 4. Repo Skills

- `$execute-project-task`：执行单个任务并在验收后停止。
- `$review-project-task`：只读核验任务，不扩展范围。

Skills 位于 `.agents/skills/`，Codex 会按需读取完整 `SKILL.md`，减少主上下文常驻内容。

## 5. 非交互式任务

当前推荐显式 sandbox：

```bash
codex exec --sandbox workspace-write "$(python scripts/taskctl.py prompt M01)"
```

只读审查：

```bash
codex exec --sandbox read-only "Review task M01 against tasks/mainline/M01_dataset_manifest.md. Do not edit files."
```

不要使用已弃用的 `--full-auto`。除隔离容器外，不使用 `danger-full-access`。

## 6. 上下文控制

每个 task 文件限制 Codex 首轮读取范围。一般顺序：

1. `AGENTS.md`；
2. 当前任务文件；
3. task 的 `Context files`；
4. 通过 `rg` 精确定位其他符号；
5. 禁止一开始读取全部 `docs/`、全部 benchmark 或数据文件。

当任务发生真实分叉时再开新会话；同一问题的测试修复保留在原会话，避免丢失决策链。

## 7. 模型与 reasoning 建议

- 复杂架构、算法和最终审查：可用较强 Codex 模型与 high/xhigh。
- 代码库探索、日志归纳、格式化和小测试：可用更快的小模型 subagent。
- `.codex/config.toml` 不固定具体模型，避免不同账号/版本不可用；在 Codex UI 或用户级配置中选择。

## 8. 人必须掌握的决策

Codex 可以写大部分代码，但项目负责人必须亲自确认：

- 研究问题和公平对比；
- 记忆 schema 与时间语义；
- ETEC/QEMR 的评分项；
- 数据泄漏和 judge 偏差；
- 消融设计；
- 简历指标是否真实可复现。
