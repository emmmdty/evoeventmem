# M11: Rule-based query router

## Objective

Classify queries into no-memory, semantic, temporal, graph, episodic, procedural, or hybrid retrieval using transparent rules.

## Context files

- `docs/PROJECT_BRIEF.md`
- `tasks/mainline/M10_etec.md`
- `src/evoeventmem/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Define router labels and features.
- Implement a deterministic rules-first router.
- Create a small labeled fixture across query types.
- Persist router decision and confidence.


## Non-goals

- Do not train a router.
- Do not couple the router to one benchmark schema.


## Acceptance criteria

- [ ] All labels have positive and negative tests.
- [ ] Unknown/low-confidence queries fall back to HYBRID with an observable reason.
- [ ] Router fixture Macro-F1 is reported but not overclaimed.


## Verification

```bash
uv run pytest -q tests/retrieval/test_query_router.py
```

## Codex execution prompt

```text
Execute only task M11. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
