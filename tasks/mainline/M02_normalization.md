# M02: Normalize LongMemEval and LoCoMo

## Objective

Create a shared internal representation for sessions, turns, questions, answers, timestamps, categories, and evidence references.

## Context files

- `tasks/mainline/M01_dataset_manifest.md`
- `docs/EVALUATION.md`
- `benchmarks/`
- `tests/fixtures/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Define normalized dataclasses/Pydantic models.
- Implement streaming or bounded-memory converters for both datasets.
- Create tiny representative fixtures checked into tests.
- Preserve original sample IDs and evidence pointers.


## Non-goals

- Do not call an LLM.
- Do not alter official source data.
- Do not implement metrics yet.


## Acceptance criteria

- [ ] Both fixtures normalize deterministically.
- [ ] Round-trip serialization retains IDs, timestamps, and evidence.
- [ ] Malformed records fail with sample-local diagnostics.


## Verification

```bash
uv run pytest -q tests/benchmarks/test_normalization.py
```

## Codex execution prompt

```text
Execute only task M02. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
