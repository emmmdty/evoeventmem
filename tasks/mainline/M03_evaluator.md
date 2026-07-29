# M03: Evaluation artifacts and deterministic metrics

## Objective

Define immutable run artifacts and implement deterministic answer/evidence metrics before adding memory methods.

## Context files

- `docs/EVALUATION.md`
- `tasks/mainline/M02_normalization.md`
- `benchmarks/`
- `tests/fixtures/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Define prediction JSONL and run metadata schema.
- Implement exact match/token F1 and evidence precision/recall/F1.
- Record latency, token usage when available, config hash, git commit, model ID, and dataset fingerprint.
- Add a smoke evaluator over fixtures.


## Non-goals

- Do not use an LLM judge as the only metric.
- Do not implement retrieval or model calls.


## Acceptance criteria

- [ ] Metrics have edge-case tests.
- [ ] Run artifacts are append-safe or write-once.
- [ ] Smoke evaluator produces a summary JSON and per-sample JSONL.


## Verification

```bash
uv run pytest -q tests/benchmarks/test_metrics.py
uv run python -m benchmarks.smoke_eval
```

## Codex execution prompt

```text
Execute only task M03. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
