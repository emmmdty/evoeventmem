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

- [x] Report validation catches incompatible run configs.
- [x] Each claim links to run IDs and config hashes.
- [x] At least 50 representative failures or all failures if fewer are categorized.

All criteria pass on the 2026-08-13/14 evidence base (see
`docs/STRONG_RESULTS_SMALL_SAMPLE.md` and `docs/METHODOLOGY_CHANGE.md`):
33/33 r2 failures were reviewed (fewer than 50, so all), and every sealed
report under `runs/analysis/` validates with `validate_report`.


## Verification

The analysis pipeline is content-addressed (C3/C8): source runs are finalized
publication runs (not the legacy `runs/main` summary/config trees), and both
the generator and the validator require the analysis config plus the explicit
source-run list. Generate and validate with:

```bash
uv run python -m benchmarks.analysis.report \
  --config configs/analysis/r2-pilot.toml \
  --source-run runs/publication/longmemeval-test20-r2 \
  --output-root runs/analysis
uv run python -m benchmarks.analysis.validate_report \
  --config configs/analysis/r2-pilot.toml \
  --source-run runs/publication/longmemeval-test20-r2 \
  --artifact-root runs/analysis
```

No `runs/main` compatibility entry is provided: legacy summary/config run
trees are rejected as analysis inputs (`legacy_report_input`) by design, so
the verification command targets the finalized, content-addressed runs under
`runs/publication/` and the sealed artifact under `runs/analysis/`.

## Codex execution prompt

```text
Execute only task M15. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
