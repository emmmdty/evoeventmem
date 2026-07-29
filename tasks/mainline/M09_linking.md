# M09: Candidate generation and entity/event linking

## Objective

Generate bounded candidate sets for entity resolution and event consolidation, with measurable recall and latency.

## Context files

- `tasks/mainline/M08_write_pipeline.md`
- `docs/EVALUATION.md`
- `src/evoeventmem/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement normalized keys, alias matching, embedding candidates, and time-window filtering.
- Separate candidate generation from final decision.
- Create a small manually inspectable linking fixture.
- Record candidate recall@K and latency.


## Non-goals

- Do not implement final ETEC scoring.
- Do not use unrestricted all-pairs comparison.


## Acceptance criteria

- [ ] Candidate sets are bounded and deterministic for fixed embeddings.
- [ ] Gold candidate recall@K can be calculated.
- [ ] Entity and event candidates use distinct policies.


## Verification

```bash
uv run pytest -q tests/linking
```

## Codex execution prompt

```text
Execute only task M09. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
