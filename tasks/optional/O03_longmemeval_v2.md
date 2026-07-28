# O03: LongMemEval-V2 small-tier study

## Objective

Evaluate environment experience memory on the public small tier.

## Context files

- `M15 complete`
- `LongMemEval-V2 fixed commit`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Use a bounded subset, record latency, and compare raw trajectory RAG vs structured event/procedure memory.


## Non-goals

- Do not download/run the largest tier by default.


## Acceptance criteria

- [ ] A separate design note defines dataset/split/baseline before implementation.
- [ ] The extension cannot change mainline result artifacts.
- [ ] All new claims have dedicated tests or benchmark outputs.


## Verification

```bash
pytest -q
```

## Codex execution prompt

```text
Execute only task O03. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
