# O05: Pi extension adapter

## Objective

Add a thin TypeScript Pi extension that calls EvoEventMem.

## Context files

- `M17 stable MCP/API contracts`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement tool/lifecycle integration and one demo.


## Non-goals

- Do not duplicate memory algorithms in TypeScript.


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
Execute only task O05. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
