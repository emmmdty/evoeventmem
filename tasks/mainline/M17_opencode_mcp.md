# M17: OpenCode MCP adapter

## Objective

Expose a compact memory tool surface that OpenCode can use without embedding memory algorithms inside the plugin/runtime.

## Context files

- `adapters/opencode/README.md`
- `tasks/mainline/M16_production_api.md`
- `src/evoeventmem/api/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement an MCP server or adapter with 4–6 high-level tools: search, write/observe, explain, timeline, feedback, forget.
- Provide OpenCode project config example.
- Use a fake Memory Service in tests.
- Document active retrieval and optional lifecycle hook strategy.
- Add a Coding/Debug Agent demo script with trace capture.


## Non-goals

- Do not fork OpenCode.
- Do not expose dozens of fine-grained tools.
- Do not let the adapter own ETEC/QEMR logic.


## Acceptance criteria

- [ ] Tool schemas are stable and tested.
- [ ] OpenCode setup is reproducible from README.
- [ ] Demo shows retrieved memory, evidence, and resulting action.
- [ ] Service outage returns a clear fallback rather than breaking the Agent loop.


## Verification

```bash
pytest -q tests/adapters/test_mcp.py
```

## Codex execution prompt

```text
Execute only task M17. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
