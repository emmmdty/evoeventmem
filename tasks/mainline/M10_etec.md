# M10: ETEC temporal consolidation

## Objective

Implement evidence-constrained ADD/MERGE/SUPERSEDE/REJECT decisions and quantify their correctness.

## Context files

- `docs/PROJECT_BRIEF.md`
- `tasks/mainline/M09_linking.md`
- `src/evoeventmem/`
- `benchmarks/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Define feature computation for semantic, entity/role, temporal, structural, and evidence consistency.
- Implement an interpretable rule/weighted scorer first.
- Apply decisions transactionally and maintain valid_from/valid_to.
- Persist feature values, thresholds, rule hits, and decision reason.
- Create an annotation format and a small evaluation set.


## Non-goals

- Do not train a large model.
- Do not hide LLM-only decisions without features.
- Do not tune on final test examples.


## Acceptance criteria

- [ ] ADD/MERGE/SUPERSEDE/REJECT paths each have tests.
- [ ] Temporal contradictions cannot leave two current active facts unless explicitly multi-valued.
- [ ] ETEC smoke report includes merge F1, conflict accuracy, provenance coverage, and stale-memory error.


## Verification

```bash
pytest -q tests/consolidation
python -m benchmarks.etec_smoke
```

## Codex execution prompt

```text
Execute only task M10. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
