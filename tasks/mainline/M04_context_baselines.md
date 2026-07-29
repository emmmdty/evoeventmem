# M04: No-memory and full-context baselines

## Objective

Implement the simplest fair baselines and verify the end-to-end runner without retrieval.

## Context files

- `tasks/mainline/M03_evaluator.md`
- `docs/MODEL_STRATEGY.md`
- `benchmarks/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement No Memory and Full Context context builders.
- Use a deterministic fake chat model in tests.
- Enforce a context budget and record truncation decisions.
- Add config files for fixture smoke runs.


## Non-goals

- Do not add vector search.
- Do not use dataset-specific hidden evidence in the Full Context baseline.


## Acceptance criteria

- [ ] Both baselines produce standard run artifacts.
- [ ] Budget overflow behavior is deterministic and tested.
- [ ] No-memory never accesses history.


## Verification

```bash
uv run pytest -q tests/benchmarks/test_context_baselines.py
```

## Codex execution prompt

```text
Execute only task M04. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
