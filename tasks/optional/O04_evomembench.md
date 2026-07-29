# O04: EvoMemBench focused study

## Objective

Evaluate one knowledge and one execution setting.

## Context files

- `M15 complete`
- `EvoMemBench fixed commit`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Select settings before seeing final results and reuse common artifacts.


## Non-goals

- Do not attempt all 15 systems in three months.


## Acceptance criteria

- [ ] A separate design note defines dataset/split/baseline before implementation.
- [ ] The extension cannot change mainline result artifacts.
- [ ] All new claims have dedicated tests or benchmark outputs.


## Verification

```bash
uv run pytest -q
```

## Codex execution prompt

```text
Execute only task O04. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
