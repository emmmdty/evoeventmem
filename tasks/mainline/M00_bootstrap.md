# M00: Repository bootstrap and vertical slice

## Objective

Provide a runnable repository with a minimal memory model, in-memory store, service, API, tests, task protocol, and dataset download entry points.

## Context files

- `README.md`
- `AGENTS.md`
- `pyproject.toml`
- `src/evoeventmem/`
- `tests/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Keep the vertical slice deliberately simple.
- Ensure fresh-clone commands are documented.


## Non-goals

- Do not implement ETEC, QEMR, PostgreSQL, MCP, or benchmark runners.


## Acceptance criteria

- [ ] Unit tests pass.
- [ ] CLI smoke command writes and retrieves one memory.
- [ ] API health/write/search endpoints are covered.


## Verification

```bash
uv run pytest -q
uv run python -m evoeventmem.cli smoke
```

## Codex execution prompt

```text
Execute only task M00. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.

## Notes

This task is completed by the starter archive. Do not redo it unless bootstrap tests fail.
