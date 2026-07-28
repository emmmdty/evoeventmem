# M07: Event extraction and evidence linking

## Objective

Convert normalized turns/observations into candidate event memories whose fields point to exact source evidence.

## Context files

- `tasks/mainline/M06_event_schema.md`
- `tasks/mainline/M05_vector_baseline.md`
- `benchmarks/normalized/`
- `src/evoeventmem/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Define extractor input/output contracts and prompt versioning.
- Provide a deterministic rule extractor for tests.
- Provide an LLM extractor behind the model gateway.
- Validate spans/turn IDs and reject hallucinated evidence references.


## Non-goals

- Do not merge candidates into durable memory.
- Do not optimize prompts using test labels.


## Acceptance criteria

- [ ] Fixture events preserve speaker/entity/time/evidence.
- [ ] Invalid evidence is surfaced as a structured error.
- [ ] Extraction requests and raw outputs are cached.


## Verification

```bash
pytest -q tests/extraction
```

## Codex execution prompt

```text
Execute only task M07. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
