# M12: QEMR hybrid retrieval and budget packing

## Objective

Combine dense, temporal, graph, episodic, and procedural candidates according to query type, then pack evidence under a strict token budget.

## Context files

- `tasks/mainline/M11_query_router.md`
- `tasks/mainline/M10_etec.md`
- `docs/EVALUATION.md`
- `src/evoeventmem/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement candidate-source interfaces and score normalization.
- Define query-type weight profiles and obsolete-memory penalty.
- Deduplicate overlapping evidence.
- Implement budget-aware packing with evidence coverage and source diversity.
- Persist component scores and exclusion reasons.


## Non-goals

- Do not train weights in the mainline.
- Do not exceed the configured token budget.
- Do not silently include superseded memories as current facts.


## Acceptance criteria

- [ ] Fixed vector, fixed hybrid, and QEMR are runnable from one harness.
- [ ] Selected context never exceeds budget.
- [ ] Every packed item has a source evidence reference and score decomposition.


## Verification

```bash
uv run pytest -q tests/retrieval
uv run python -m benchmarks.retrieval_smoke
```

## Codex execution prompt

```text
Execute only task M12. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
