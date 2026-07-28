# O01: Learned query router

## Objective

Train a small query router only after the rule router and end-to-end harness are stable.

## Context files

- `M11/M12 outputs and labeled router fixture`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Compare rules, classical classifier, and optional small-model LoRA.
- Report Macro-F1, calibration, latency, cost, and end-to-end impact.


## Non-goals

- Do not train on final benchmark test labels.


## Acceptance criteria

- [ ] A separate design note defines dataset/split/baseline before implementation.
- [ ] The extension cannot change mainline result artifacts.
- [ ] All new claims have dedicated tests or benchmark outputs.


## Verification

```bash
pytest -q
```

## Codex execution prompt

```text
Execute only task O01. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
