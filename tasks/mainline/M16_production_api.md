# M16: Production API, persistence, and observability

## Objective

Move from the in-memory demo to a deployable PostgreSQL/pgvector service while preserving the tested domain behavior.

## Context files

- `docs/ARCHITECTURE.md`
- `docker-compose.yml`
- `src/evoeventmem/api/`
- `src/evoeventmem/infra/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Add async PostgreSQL repositories and migrations.
- Add tenant/user/session scoping.
- Expose health/readiness, write, search, explain, feedback, and forget endpoints.
- Add structured logs, request IDs, metrics, timeouts, and explicit fallback events.
- Provide Docker Compose smoke test.


## Non-goals

- Do not add a second graph database.
- Do not add a large frontend.
- Do not expose raw secrets or user text in default logs.


## Acceptance criteria

- [ ] Repository contract tests pass for in-memory and PostgreSQL.
- [ ] API schema is generated and stable.
- [ ] Fallbacks and partial failures are observable.
- [ ] Docker Compose config validates.


## Verification

```bash
uv run pytest -q tests/api tests/infra
docker compose config
```

## Codex execution prompt

```text
Execute only task M16. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
