# M15: Ablations, statistics, and error analysis

## Objective

Turn raw benchmark runs into defensible method claims and identify failure modes.

## Context files

- `docs/EVALUATION.md`
- `runs/`
- `benchmarks/analysis/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement paired bootstrap confidence intervals.
- Generate overall, category, efficiency, and ablation tables.
- Run required ablations: evidence, temporal, graph, router, weights, budget.
- Create a typed error taxonomy and sample review sheet.
- Generate plots/tables from immutable run artifacts.


## Non-goals

- Do not hand-edit final metric tables.
- Do not report significance from unpaired tests.
- Do not cherry-pick only improved categories.


## Acceptance criteria

- [ ] Report validation catches incompatible run configs.
- [ ] Each claim links to run IDs and config hashes.
- [ ] At least 50 representative failures or all failures if fewer are categorized.


## Verification

```bash
uv run python -m benchmarks.analysis.validate_report runs/main
```

## Codex execution prompt

```text
Execute only task M15. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
