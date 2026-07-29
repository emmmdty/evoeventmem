# M18: Reproduction, demo, and resume release

## Objective

Produce a public-repository release that a reviewer can understand and verify without private context.

## Context files

- `README.md`
- `docs/`
- `runs/main/`
- `adapters/opencode/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Add one-command smoke reproduction.
- Create architecture/method figures and generated result tables.
- Write a concise technical report and model/data cards.
- Record a 3–5 minute demo script.
- Fill resume bullets only from validated result artifacts.
- Pin dependencies and benchmark commits.


## Non-goals

- Do not include datasets, secrets, private traces, or unverifiable claims.
- Do not require the full expensive experiment for smoke reproduction.


## Acceptance criteria

- [ ] Fresh-clone release check passes.
- [ ] All README result numbers map to run artifacts.
- [ ] Licenses and citations are present.
- [ ] Open issues and limitations are explicit.


## Verification

```bash
uv run python scripts/release_check.py
```

## Codex execution prompt

```text
Execute only task M18. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
