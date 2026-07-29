# M06: Durable event-memory schema

## Objective

Replace the starter record with a research-grade event-memory contract while preserving backward-compatible service behavior.

## Context files

- `docs/ARCHITECTURE.md`
- `src/evoeventmem/domain/models.py`
- `src/evoeventmem/core/ports.py`
- `tests/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Model fact, event, episode, procedure, entity, relation, evidence, temporal validity, status, supersedes, and derivation.
- Define domain invariants and UTC datetime handling.
- Add schema versioning and JSON serialization.
- Keep transport/database concerns outside domain.


## Non-goals

- Do not implement extraction or persistence migrations.
- Do not add framework-specific fields.


## Acceptance criteria

- [ ] Invalid temporal intervals are rejected.
- [ ] Active/superseded status and links are consistent.
- [ ] Every durable memory requires at least one evidence reference unless explicitly synthetic.


## Verification

```bash
uv run pytest -q tests/domain/test_event_schema.py
```

## Codex execution prompt

```text
Execute only task M06. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
