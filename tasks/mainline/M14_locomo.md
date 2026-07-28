# M14: LoCoMo main experiment

## Objective

Run the same method family on LoCoMo with special emphasis on evidence and event structure.

## Context files

- `docs/EVALUATION.md`
- `tasks/mainline/M13_longmemeval.md`
- `docs/DATASETS.md`
- `benchmarks/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Reuse shared runner contracts.
- Map evidence dialog IDs to normalized evidence references.
- Report QA categories, Evidence F1, retrieval metrics, token, and latency.
- Add event-summary evaluation or a clearly documented structural proxy.


## Non-goals

- Do not create a dataset-specific memory algorithm.
- Do not evaluate on generated observations if that gives one method privileged information.


## Acceptance criteria

- [ ] Smoke config completes.
- [ ] All compared methods share the same reader and budget.
- [ ] Evidence metrics are computed from official evidence IDs.


## Verification

```bash
python -m benchmarks.locomo.run --config configs/locomo/smoke.toml
```

## Codex execution prompt

```text
Execute only task M14. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
