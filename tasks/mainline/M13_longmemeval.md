# M13: LongMemEval main experiment

## Objective

Run a fair, resumable LongMemEval Small experiment comparing context and memory methods.

## Context files

- `docs/EVALUATION.md`
- `tasks/mainline/M12_qemr.md`
- `docs/DATASETS.md`
- `benchmarks/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Add smoke and main configs.
- Run No Memory, Full Context, Vector RAG, event without ETEC, ETEC, and full ETEC+QEMR.
- Report all official ability categories and efficiency metrics.
- Support resume and per-sample retry without overwriting completed outputs.


## Non-goals

- Do not use oracle evidence for the main comparison.
- Do not change reader model or context budget between methods.
- Do not run Medium before Small is validated.


## Acceptance criteria

- [ ] Smoke config completes on a tiny subset.
- [ ] Main config records dataset hash and all model/config versions.
- [ ] Summary detects missing/duplicate sample IDs.


## Verification

```bash
uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/smoke.toml
```

## Codex execution prompt

```text
Execute only task M13. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
