# O08: Privacy and multi-tenant hardening

## Objective

Add deletion, TTL, redaction, audit, and isolation tests.

## Context files

- `M16 persistent service`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Threat model, tenant isolation tests, deletion verification, and log redaction.


## Non-goals

- Do not claim formal privacy guarantees without evidence.


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
Execute only task O08. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
