# M08: Memory write pipeline and provenance

## Objective

Build the application workflow that extracts candidates, validates them, persists decision logs, and supports idempotent retries.

## Context files

- `tasks/mainline/M07_event_extraction.md`
- `src/evoeventmem/services/`
- `src/evoeventmem/core/ports.py`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Define write request, candidate, decision, and result objects.
- Add idempotency keys derived from source evidence and extractor version.
- Persist raw observation linkage and all rejected candidates.
- Expose write metrics and failure categories.


## Non-goals

- Do not implement semantic merge decisions beyond exact duplicate handling.
- Do not add asynchronous queues yet.


## Acceptance criteria

- [ ] Retrying the same observation creates no duplicate durable memory.
- [ ] Failures do not leave partially committed in-memory state.
- [ ] Decision log explains accepted/rejected candidates.


## Verification

```bash
pytest -q tests/services/test_write_pipeline.py
```

## Codex execution prompt

```text
Execute only task M08. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
